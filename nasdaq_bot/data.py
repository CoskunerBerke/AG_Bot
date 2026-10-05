"""Piyasa verisi: NASDAQ-100 listesi, OHLCV indirme, temizleme ve önbellek."""
from __future__ import annotations

import io
import logging
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Wikipedia'ya ulaşılamazsa kullanılacak yedek liste
FALLBACK_NASDAQ100 = """
AAPL MSFT NVDA AMZN META AVGO GOOGL GOOG TSLA COST NFLX AMD PEP ADBE CSCO TMUS LIN INTU QCOM
TXN AMGN ISRG CMCSA BKNG HON AMAT PDD VRTX PANW ADP GILD SBUX MU ADI LRCX MELI INTC KLAC MDLZ
CTAS REGN SNPS CDNS PYPL CRWD MAR CEG MRVL ORLY CSX ASML FTNT ADSK DASH ROP NXPI PCAR WDAY ABNB
CHTR MNST AEP CPRT PAYX TTD ROST FANG KDP ODFL FAST BKR EA VRSK CTSH XEL EXC GEHC KHC CCEP DDOG
IDXX LULU TEAM AZN ZS CSGP ON TTWO CDW DXCM BIIB GFS WBD MDB MCHP PLTR APP AXON MSTR SHOP ARM
""".split()

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def get_nasdaq100(cache_dir: str) -> list[str]:
    """Güncel NASDAQ-100 bileşenlerini Wikipedia'dan çeker (7 gün önbellek)."""
    cache = Path(cache_dir) / "nasdaq100.txt"
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        tickers = cache.read_text(encoding="utf-8").split()
        if len(tickers) >= 90:
            return tickers
    try:
        html = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=UA, timeout=15).text
        for table in pd.read_html(io.StringIO(html)):
            cols = [str(c) for c in table.columns]
            for name in ("Ticker", "Symbol"):
                if name in cols and 90 <= len(table) <= 110:
                    tickers = sorted({str(x).strip().replace(".", "-") for x in table[name] if str(x).strip()})
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text("\n".join(tickers), encoding="utf-8")
                    return tickers
    except Exception as e:  # noqa: BLE001
        log.warning("NASDAQ-100 listesi alınamadı (%s), yedek liste kullanılıyor.", e)
    return FALLBACK_NASDAQ100


def clean_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Veri doğrulama: eksik/bozuk satırları atar, OHLC tutarlılığını sağlar."""
    if df is None or df.empty:
        return None
    if not all(c in df.columns for c in OHLCV):
        return None
    df = df[OHLCV].copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    df["Volume"] = df["Volume"].fillna(0)
    # High her zaman en yüksek, Low her zaman en düşük olmalı
    px = df[["Open", "High", "Low", "Close"]]
    df["High"] = px.max(axis=1)
    df["Low"] = px.min(axis=1)
    return df if len(df) else None


def drop_partial_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Seans devam ederken bugünün tamamlanmamış günlük mumunu çıkarır."""
    if df.empty:
        return df
    now = datetime.now(NY)
    if df.index[-1].date() == now.date() and now.time() < dtime(16, 0):
        return df.iloc[:-1]
    return df


def download_ohlcv(
    tickers: list[str],
    period: str = "2y",
    interval: str = "1d",
    cache_dir: str = ".cache",
    ttl_minutes: int = 30,
    batch: int = 40,
) -> dict[str, pd.DataFrame]:
    """Toplu, yeniden denemeli ve önbellekli OHLCV indirme (düzeltilmiş fiyatlar)."""
    cdir = Path(cache_dir) / "ohlcv"
    cdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    for t in dict.fromkeys(tickers):
        f = cdir / f"{t}_{period}_{interval}.pkl"
        miss = cdir / f"{t}_{period}_{interval}.missing"
        miss_ttl = 3600 if interval.endswith("m") else 12 * 3600
        if miss.exists() and time.time() - miss.stat().st_mtime < miss_ttl:
            continue  # yakın zamanda veri dönmedi (borsadan çıkmış olabilir)
        if f.exists() and (time.time() - f.stat().st_mtime) / 60 < ttl_minutes:
            try:
                out[t] = pd.read_pickle(f)
                continue
            except Exception:  # noqa: BLE001
                pass
        missing.append(t)

    for i in range(0, len(missing), batch):
        chunk = missing[i:i + batch]
        raw, had_error = None, False
        for attempt in range(3):
            try:
                raw = yf.download(
                    chunk, period=period, interval=interval, group_by="ticker",
                    auto_adjust=True, threads=True, progress=False,
                )
                if raw is not None and not raw.empty:
                    break
            except Exception as e:  # noqa: BLE001
                had_error = True
                log.warning("İndirme hatası (deneme %d): %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
        got = []
        if raw is not None and not raw.empty:
            for t in chunk:
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        if t not in raw.columns.get_level_values(0):
                            continue
                        df = raw[t]
                    else:
                        df = raw
                    df = clean_ohlcv(df)
                    if df is not None:
                        out[t] = df
                        got.append(t)
                        df.to_pickle(cdir / f"{t}_{period}_{interval}.pkl")
                except Exception as e:  # noqa: BLE001
                    log.debug("%s işlenemedi: %s", t, e)
        # Ağ sorunu değil de sembolün kendisinde sorun varsa 12 saat tekrar deneme
        if got or (len(chunk) == 1 and not had_error):
            for t in set(chunk) - set(got):
                (cdir / f"{t}_{period}_{interval}.missing").touch()
    return out
