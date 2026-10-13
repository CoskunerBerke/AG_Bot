"""DMC veri katmanı: Alpaca 1 dk barları, aylık parçalı önbellek, yeniden örnekleme.

Tüm zaman damgaları America/New_York, tz-naive ve barın BAŞLANGIÇ zamanıdır
(10:00 barı = 10:00:00–10:00:59). Bir N dk bar, başlangıcı s ise s+N anında kapanır.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from ..common import NY, alpaca_keys

log = logging.getLogger(__name__)
COLS = ["Open", "High", "Low", "Close", "Volume"]
DATA_URL = "https://data.alpaca.markets/v2/stocks/{sym}/bars"


def _session() -> requests.Session:
    keys = alpaca_keys()
    if not keys:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET tanımlı değil (.env)")
    s = requests.Session()
    s.headers.update({"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]})
    return s


def fetch_minutes(symbol: str, start: datetime, end: datetime, feed: str = "sip",
                  session: requests.Session | None = None) -> pd.DataFrame:
    """[start, end) aralığındaki 1 dk barlar (extended hours dahil). start/end tz-aware olmalı."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end tz-aware olmalı")
    s = session or _session()
    params = {"timeframe": "1Min", "limit": 10000, "adjustment": "all", "feed": feed,
              "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    rows, token = [], None
    for _ in range(1000):
        if token:
            params["page_token"] = token
        for attempt in range(5):
            r = s.get(DATA_URL.format(sym=symbol), params=params, timeout=30)
            if r.status_code != 429:
                break
            import time
            time.sleep(2 * (attempt + 1))
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca bars {symbol} HTTP {r.status_code}: {r.text[:200]}")
        js = r.json()
        rows += js.get("bars") or []
        token = js.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame(columns=COLS, dtype=float)
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY).dt.tz_localize(None)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[COLS].astype(float)
    df.index.name = None
    return df[~df.index.duplicated(keep="last")].sort_index()


def _cache_dir(cfg: dict) -> Path:
    p = Path(cfg["data"]["cache_dir"]) / "dmc"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_futures(symbol: str) -> bool:
    return symbol.upper().startswith(("NQ", "ES"))


def load_futures(cfg: dict, start: date, end: date, root: str = "NQ") -> pd.DataFrame:
    """Databento'dan indirilmiş 1 dk vadeli (NQ.v.0 / ES.v.0, ayarlanmamış sürekli kontrat = TradingView NQ1!/ES1!)."""
    df = pd.read_pickle(_cache_dir(cfg) / f"{root}_ohlcv1m.pkl")
    lo = pd.Timestamp(datetime.combine(start, datetime.min.time()))
    hi = pd.Timestamp(datetime.combine(end + timedelta(days=1), datetime.min.time()))
    return df[(df.index >= lo) & (df.index < hi)]


def daily_bars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Günlük mumlar: vadelide 18:00-17:00 seansı (Kaan'ın NQ1! günlük mumu), hissede 09:30-16:00."""
    if not is_futures(symbol):
        return rth_daily(df)
    t = df.index
    sess = (t + pd.Timedelta(hours=6)).normalize()            # 18:00 ve sonrası ertesi günün seansı
    g = df.groupby(sess)
    d = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                      "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    return d[d.index.dayofweek < 5]


PEERS = {"NQ": "ES", "QQQ": "SPY", "SPY": "QQQ", "ES": "NQ", "TQQQ": "SPY", "DIA": "SPY"}


def peer_symbol(symbol: str) -> str | None:
    """SMT eşi: NQ↔ES (vadeli), QQQ↔SPY (ETF); TQQQ ve DIA → SPY."""
    base = "NQ" if symbol.upper().startswith("NQ") else "ES" if symbol.upper().startswith("ES") else symbol.upper()
    p = PEERS.get(base)
    if p in ("NQ", "ES"):
        return f"{p}.v.0"
    return p


def load_history(cfg: dict, symbol: str, start: date, end: date, feed: str = "sip") -> pd.DataFrame:
    """[start, end] günlerinin 1 dk barları. Ay bazında önbelleğe alınır; içinde bulunulan ay her seferinde
    yeniden çekilir (SIP verisi 15 dk gecikmeli olduğundan bitiş en fazla now-16dk). NQ* → yerel Databento verisi."""
    if is_futures(symbol):
        return load_futures(cfg, start, end, symbol.upper()[:2])
    out, s = [], None
    cur = date(start.year, start.month, 1)
    today = datetime.now(NY).date()
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        f = _cache_dir(cfg) / f"{symbol}_{feed}_{cur:%Y%m}.pkl"
        complete_month = nxt <= today
        if f.exists() and complete_month:
            df = pd.read_pickle(f)
        else:
            s = s or _session()
            a = datetime.combine(cur, datetime.min.time(), NY)
            b = min(datetime.combine(nxt, datetime.min.time(), NY), datetime.now(NY) - timedelta(minutes=16))
            log.info("indiriliyor %s %s", symbol, f"{cur:%Y-%m}")
            df = fetch_minutes(symbol, a, b, feed=feed, session=s)
            if complete_month:
                df.to_pickle(f)
        out.append(df)
        cur = nxt
    df = pd.concat(out) if out else pd.DataFrame(columns=COLS, dtype=float)
    lo = pd.Timestamp(datetime.combine(start, datetime.min.time()))
    hi = pd.Timestamp(datetime.combine(end + timedelta(days=1), datetime.min.time()))
    return df[(df.index >= lo) & (df.index < hi)]


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1 dk barları N dk barlara çevirir (sol etiket, sol kapalı). 60 ve katları 09:30'a hizalanır."""
    if minutes == 1 or df.empty:
        return df
    offset = "30min" if minutes % 60 == 0 else None
    r = df.resample(f"{minutes}min", label="left", closed="left", offset=offset)
    out = pd.DataFrame({"Open": r["Open"].first(), "High": r["High"].max(), "Low": r["Low"].min(),
                        "Close": r["Close"].last(), "Volume": r["Volume"].sum()})
    return out.dropna(subset=["Open"])


def closed_only(df: pd.DataFrame, minutes: int, now: pd.Timestamp) -> pd.DataFrame:
    """`now` anında kapanmış N dk barlar (now = son kapanmış 1 dk barın bitiş zamanı)."""
    return df[df.index + pd.Timedelta(minutes=minutes) <= now]


def rth_daily(df: pd.DataFrame) -> pd.DataFrame:
    """1 dk barlardan normal seans (09:30–16:00) günlük OHLC."""
    t = df.index.time
    rth = df[(t >= datetime.strptime("09:30", "%H:%M").time()) & (t < datetime.strptime("16:00", "%H:%M").time())]
    g = rth.groupby(rth.index.date)
    d = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                      "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    d.index = pd.to_datetime(d.index)
    return d
