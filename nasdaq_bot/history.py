"""Kalıcı veri arşivi: botun geçmiş veriyi biriktirip öğrenmede kullanması için.

Sorun: Yahoo ücretsiz gün içi veriyi kısıtlı verir (5m/15m: son 60 gün, 1h: son 730 gün).
Çözüm:
  1. ARŞİV  : her indirme `data/history/<interval>/<TICKER>.pkl` dosyasına EKLENİR (üzerine yazılmaz).
              Bot her gün çalıştıkça gün içi geçmiş büyür; 60 günlük sınır zamanla aşılır.
  2. ALPACA : .env içinde ücretsiz Alpaca anahtarları varsa 2016'dan bu yana gün içi bar çekilir
              (ücretsiz Basic plan: tüm ABD borsaları SIP verisi, son 15 dk hariç).
              Bir aralığın kaynağı karıştırılmaz: Alpaca ile başlayan arşiv Alpaca ile devam eder.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .config import ROOT
from .data import NY, clean_ohlcv, download_ohlcv

log = logging.getLogger(__name__)

YF_PERIOD = {"1d": "max", "1h": "730d", "15m": "60d", "5m": "60d"}
ALPACA_TF = {"1d": "1Day", "15m": "15Min", "5m": "5Min"}   # 1h = 15m'den 09:30 hizalı üretilir
ALPACA_START = "2016-01-01"


def _dir(interval: str) -> Path:
    p = ROOT / "data" / "history" / interval
    p.mkdir(parents=True, exist_ok=True)
    return p


def _source_file(interval: str) -> Path:
    return _dir(interval) / "_source.txt"


def archive_source(interval: str) -> str | None:
    f = _source_file(interval)
    return f.read_text().strip() if f.exists() else None


def load_archive(ticker: str, interval: str) -> pd.DataFrame | None:
    f = _dir(interval) / f"{ticker}.pkl"
    if f.exists():
        try:
            return pd.read_pickle(f)
        except Exception:  # noqa: BLE001
            return None
    return None


def _merge_save(ticker: str, interval: str, new: pd.DataFrame | None) -> pd.DataFrame | None:
    old = load_archive(ticker, interval)
    if new is None or new.empty:
        return old
    df = new if old is None else pd.concat([old, new])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_pickle(_dir(interval) / f"{ticker}.pkl")
    return df


# ----------------------------------------------------------------------------- Alpaca
def alpaca_keys() -> tuple[str, str] | None:
    k, s = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_API_SECRET", "")
    return (k, s) if k and s else None


def fetch_alpaca(ticker: str, tf: str, start: datetime, end: datetime | None = None, feed: str = "sip") -> pd.DataFrame | None:
    keys = alpaca_keys()
    if not keys:
        return None
    s = requests.Session()
    s.headers.update({"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]})
    end = end or (datetime.now(timezone.utc) - timedelta(minutes=16))
    params = {"timeframe": tf, "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "limit": 10000, "adjustment": "all", "feed": feed}
    rows, token = [], None
    for _ in range(500):
        if token:
            params["page_token"] = token
        for attempt in range(4):
            r = s.get(f"https://data.alpaca.markets/v2/stocks/{ticker}/bars", params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            break
        if r.status_code == 403 and feed == "sip":
            return fetch_alpaca(ticker, tf, start, end, feed="iex")   # plan SIP'e izin vermiyorsa
        if r.status_code != 200:
            log.warning("Alpaca %s %s: HTTP %s %s", ticker, tf, r.status_code, r.text[:120])
            break
        js = r.json()
        rows += js.get("bars") or []
        token = js.get("next_page_token")
        if not token:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY).dt.tz_localize(None)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    return clean_ohlcv(df)


def resample_1h(df15: pd.DataFrame) -> pd.DataFrame:
    """15 dk -> 1 saat, seans açılışına (09:30) hizalı: 09:30, 10:30, ... 15:30 (Yahoo ile aynı)."""
    df = df15.between_time("09:30", "15:59")
    agg = df.resample("60min", origin="start_day", offset="30min").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    return clean_ohlcv(agg.dropna(subset=["Open"]))


# ----------------------------------------------------------------------------- ana giriş
def update_history(cfg: dict, tickers: list[str], interval: str, progress=None) -> dict[str, pd.DataFrame]:
    """Arşivi günceller ve birleşik geçmişi döndürür."""
    src = archive_source(interval)
    use_alpaca = alpaca_keys() is not None and src in (None, "alpaca")
    out: dict[str, pd.DataFrame] = {}
    if use_alpaca:
        _source_file(interval).write_text("alpaca")
        base_iv = "15m" if interval == "1h" else interval
        for t in tickers:
            old = load_archive(t, base_iv) if base_iv != interval else load_archive(t, interval)
            start = (old.index[-1] - pd.Timedelta(days=3)).tz_localize(NY) if old is not None and len(old) else \
                pd.Timestamp(ALPACA_START, tz=NY)
            new = fetch_alpaca(t, ALPACA_TF[base_iv], start.to_pydatetime())
            df = _merge_save(t, base_iv, new)
            if base_iv != interval and df is not None:
                _source_file(base_iv).write_text("alpaca")
                df = _merge_save(t, interval, resample_1h(df))
            if df is not None:
                out[t] = df
            if progress:
                progress(t)
        return out

    _source_file(interval).write_text("yahoo")
    fresh = download_ohlcv(tickers, period=YF_PERIOD[interval], interval=interval,
                           cache_dir=cfg["data"]["cache_dir"], ttl_minutes=cfg["data"]["cache_ttl_minutes"])
    for t in tickers:
        df = _merge_save(t, interval, fresh.get(t))
        if df is not None:
            out[t] = df
        if progress:
            progress(t)
    return out


def history_summary(data: dict[str, pd.DataFrame]) -> str:
    if not data:
        return "veri yok"
    starts = [d.index[0] for d in data.values()]
    ends = [d.index[-1] for d in data.values()]
    bars = sum(len(d) for d in data.values())
    return (f"{len(data)} hisse | {min(starts).date()} → {max(ends).date()} | toplam {bars:,} bar | "
            f"kaynak: {'Alpaca' if alpaca_keys() else 'Yahoo'} + yerel arşiv")
