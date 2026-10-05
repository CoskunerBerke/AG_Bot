"""Gün içi (5 dk) analiz motoru: long + short kompozit skor.

Short skoru, fiyat serisi ters çevrilerek (1/fiyat) AYNI teknik motorla hesaplanır: düşüş trendi
ters seride yükseliş trendine, ayı formasyonları boğa formasyonlarına dönüşür. Böylece long ve
short tarafı birebir simetrik ve tutarlı kurallarla değerlendirilir.

Kompozit skor (0-100), her iki yön için:
  0.45 x 5 dk teknik skor  +  0.35 x günlük teknik skor (dünkü kapanışa göre)
  + VWAP konumu (+8 / -10) + açılış aralığı kırılımı (+7) + QQQ yönü (+5 / -5)
Canlı modda en iyi adaylara haber (±10) ve analist (±10) puanı eklenir.
"""
from __future__ import annotations

from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

from .data import NY
from .strategy import analyze_frame

SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)


def invert_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": 1 / df["Open"], "High": 1 / df["Low"], "Low": 1 / df["High"],
        "Close": 1 / df["Close"], "Volume": df["Volume"],
    }, index=df.index)


def session_only(df: pd.DataFrame) -> pd.DataFrame:
    t = df.index.time
    return df[(t >= SESSION_OPEN) & (t < SESSION_CLOSE)]


def drop_incomplete(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Henüz kapanmamış (oluşmakta olan) 5 dk mumunu çıkarır."""
    now = pd.Timestamp(datetime.now(NY).replace(tzinfo=None))
    return df[df.index + pd.Timedelta(minutes=minutes) <= now]


def session_vwap(df: pd.DataFrame) -> pd.Series:
    day = df.index.normalize()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).groupby(day).cumsum()
    vv = df["Volume"].groupby(day).cumsum()
    return pv / vv.replace(0, np.nan)


def opening_range(df: pd.DataFrame, minutes: int = 30):
    day = df.index.normalize()
    or_end = day + pd.Timedelta(hours=9, minutes=30 + minutes)
    in_or = df.index < or_end
    orh = df["High"].where(in_or).groupby(day).transform("max")
    orl = df["Low"].where(in_or).groupby(day).transform("min")
    done = pd.Series(df.index >= or_end, index=df.index)
    return orh, orl, done


def prev_day_values(daily: pd.DataFrame, bar_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Her bar için o günden ÖNCEKİ son tamamlanmış günün değerleri (geleceği görmez)."""
    d = daily.copy()
    d.index = pd.DatetimeIndex(d.index).normalize().astype("datetime64[ns]")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    days = pd.DatetimeIndex(bar_index).normalize().astype("datetime64[ns]")
    left = pd.DataFrame({"day": pd.DatetimeIndex(sorted(set(days)))})
    right = d.reset_index(names="day")
    m = pd.merge_asof(left, right, on="day", allow_exact_matches=False).set_index("day")
    out = m.reindex(days)
    out.index = bar_index
    return out


def daily_scores(df_daily: pd.DataFrame, s_daily: dict, weights: dict | None = None) -> pd.DataFrame:
    """Günlük grafikte long ve short teknik skorları + günlük ATR / likidite."""
    d, _ = analyze_frame(df_daily, s_daily, weights)
    di, _ = analyze_frame(invert_ohlc(df_daily), s_daily, candle_override=-d["candle_score"])
    return pd.DataFrame({
        "d_long": d["tech_score"], "d_short": di["tech_score"], "d_atr": d["atr"],
        "d_atr_pct": d["atr_pct"], "d_dvol": d["dollar_vol20"], "d_close": d["Close"],
    }, index=d.index)


def qqq_vwap_flag(qqq5: pd.DataFrame | None) -> pd.Series | None:
    if qqq5 is None or qqq5.empty:
        return None
    return (qqq5["Close"] > session_vwap(qqq5)).astype(float)


def build_intraday(df5: pd.DataFrame, ds: pd.DataFrame, qflag: pd.Series | None, li: dict,
                   weights: dict | None = None):
    """5 dk bar serisi için her barda L (long) ve S (short) kompozit skorları."""
    s5 = li["intraday_tech"]
    d, pats = analyze_frame(df5, s5, weights)
    di, _ = analyze_frame(invert_ohlc(df5), s5, candle_override=-d["candle_score"])
    f = d[["Open", "High", "Low", "Close", "Volume", "rsi", "rel_vol", "atr"]].copy()
    f["t_long"], f["t_short"] = d["tech_score"], di["tech_score"]
    f["vwap"] = session_vwap(df5)
    orh, orl, done = opening_range(df5, li["opening_range_minutes"])
    f["or_high"], f["or_low"] = orh, orl
    f = f.join(prev_day_values(ds, f.index))

    if qflag is not None:
        qq = qflag.reindex(f.index).ffill().fillna(1.0) > 0.5
    else:
        qq = pd.Series(True, index=f.index)

    c, vw = f["Close"], f["vwap"]
    vw_up = vw > vw.shift(3)
    vw_long = 8 * ((c > vw) & vw_up) - 10 * (c < vw)
    vw_short = 8 * ((c < vw) & ~vw_up) - 10 * (c > vw)
    orb_long = 7 * (done & (c > orh) & (f["rel_vol"] > 1.2))
    orb_short = 7 * (done & (c < orl) & (f["rel_vol"] > 1.2))
    mkt = np.where(qq, 5, -5)

    f["L"] = (0.45 * f["t_long"] + 0.35 * f["d_long"] + vw_long + orb_long + mkt).clip(0, 100)
    f["S"] = (0.45 * f["t_short"] + 0.35 * f["d_short"] + vw_short + orb_short - mkt).clip(0, 100)
    bad = f["t_long"].isna() | f["t_short"].isna() | f["d_long"].isna() | f["vwap"].isna()
    f.loc[bad, ["L", "S"]] = np.nan
    return f, pats


def pick_side(L: float, S: float, li: dict, mult_long: float = 1.0, mult_short: float = 1.0):
    """('long'|'short'|None, skor). İki yön arasında net bir fark (margin) şartı aranır."""
    lv, sv = L * mult_long, S * mult_short
    thr, margin = li["threshold"], li["min_margin"]
    if lv >= thr and lv - sv >= margin:
        return "long", lv
    if li.get("allow_short", True) and sv >= thr and sv - lv >= margin:
        return "short", sv
    return None, max(lv, sv)


def intraday_plan(side: str, price: float, d_atr: float, li: dict) -> dict:
    stop_d = li["stop_daily_atr"] * d_atr
    tgt_d = max(li["target_daily_atr"] * d_atr, price * li["min_target_pct"] / 100)
    if side == "long":
        stop, target = price - stop_d, price + tgt_d
    else:
        stop, target = price + stop_d, price - tgt_d
    return {"side": side, "entry": price, "stop": stop, "target": target,
            "stop_pct": stop_d / price * 100, "target_pct": tgt_d / price * 100,
            "rr": tgt_d / stop_d if stop_d > 0 else 0.0}
