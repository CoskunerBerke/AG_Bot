"""Analist görüşleri (tavsiye ortalaması, hedef fiyat, not artırım/indirimleri) ve bilanço tarihi riski."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    try:
        ts = pd.Timestamp(v)
        return None if pd.isna(ts) else ts.date()
    except Exception:  # noqa: BLE001
        return None


def analyst_view(ticker: str, earnings_window_days: int = 2) -> dict:
    t = yf.Ticker(ticker)
    res = {
        "name": ticker, "rec_mean": None, "rec_key": None, "n_analysts": None,
        "target_mean": None, "upside_pct": None, "ups_30d": 0, "downs_30d": 0,
        "earnings_date": None, "earnings_soon": False, "sector": None, "score": 0.0,
    }
    try:
        info = t.info or {}
    except Exception as e:  # noqa: BLE001
        log.debug("%s info: %s", ticker, e)
        info = {}

    res["name"] = info.get("shortName") or ticker
    res["sector"] = info.get("sector")
    res["rec_mean"] = info.get("recommendationMean")
    res["rec_key"] = info.get("recommendationKey")
    res["n_analysts"] = info.get("numberOfAnalystOpinions")
    res["target_mean"] = info.get("targetMeanPrice")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if res["target_mean"] and price:
        res["upside_pct"] = (res["target_mean"] / price - 1) * 100

    # Son 30 gündeki not artırım / indirimleri
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud.reset_index()
            dcol = "GradeDate" if "GradeDate" in ud.columns else ud.columns[0]
            dts = pd.to_datetime(ud[dcol], errors="coerce", utc=True).dt.tz_localize(None)
            recent = ud[dts >= datetime.now() - timedelta(days=30)]
            if "Action" in recent.columns:
                act = recent["Action"].astype(str).str.lower()
                res["ups_30d"] = int((act == "up").sum())
                res["downs_30d"] = int((act == "down").sum())
    except Exception as e:  # noqa: BLE001
        log.debug("%s upgrades: %s", ticker, e)

    # Bilanço tarihi
    ed = None
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            ed = _to_date(cal.get("Earnings Date"))
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            ed = _to_date(cal.loc["Earnings Date"].iloc[0])
    except Exception as e:  # noqa: BLE001
        log.debug("%s calendar: %s", ticker, e)
    if ed is None and info.get("earningsTimestamp"):
        ed = _to_date(pd.Timestamp(info["earningsTimestamp"], unit="s"))
    if ed:
        res["earnings_date"] = ed
        days = (ed - date.today()).days
        res["earnings_soon"] = 0 <= days <= earnings_window_days

    # Skor: -10..+10
    s = 0.0
    if res["rec_mean"]:
        s += (3 - float(res["rec_mean"])) / 2 * 6         # 1=Güçlü Al -> +6, 5=Sat -> -6
    if res["upside_pct"] is not None:
        s += 2 if res["upside_pct"] > 10 else (-2 if res["upside_pct"] < 0 else 0)
    if res["ups_30d"] > res["downs_30d"]:
        s += 2
    elif res["downs_30d"] > res["ups_30d"]:
        s -= 2
    res["score"] = max(-10.0, min(10.0, s))
    return res
