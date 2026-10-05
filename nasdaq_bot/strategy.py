"""Skorlama motoru: teknik + mum + hacim + piyasa rejimi (+ canlıda haber ve analist).

Canlı tarama ve backtest AYNI teknik skor fonksiyonunu kullanır; böylece backtest sonuçları
canlı sinyallerin gerçek davranışını yansıtır.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .candles import detect_patterns, patterns_on
from .chart_patterns import chart_score, detect_chart_patterns
from .data import download_ohlcv, drop_partial_bar, get_nasdaq100
from .fundamentals import analyst_view
from .indicators import add_indicators, sma
from .sentiment import news_sentiment
from .weights import load_weights

log = logging.getLogger(__name__)
MIN_BARS = 220


# ----------------------------------------------------------------------------- teknik skor
def technical_scores(d: pd.DataFrame, candle_score: pd.Series, s: dict) -> pd.DataFrame:
    """0-100 arası teknik skor ve bileşenleri (vektörel, geleceği görmez)."""
    c = d["Close"]

    trend = (8 * (c > d["ema20"]) + 7 * (d["ema20"] > d["ema50"]) + 7 * (c > d["sma200"])
             + 8 * ((d["adx"] > 20) & (d["plus_di"] > d["minus_di"]))
             - 8 * ((d["adx"] > 25) & (d["minus_di"] > d["plus_di"]))).astype(float)

    rsi_up = d["rsi"] > d["rsi"].shift(1)
    macd_cross = (d["macd"] > d["macd_signal"]) & (d["macd"].shift(1) <= d["macd_signal"].shift(1))
    stoch_cross = ((d["stoch_k"] > d["stoch_d"]) & (d["stoch_k"].shift(1) <= d["stoch_d"].shift(1))
                   & (d["stoch_k"].shift(1) < 25))
    momentum = (8 * d["rsi"].between(45, 68) + 8 * ((d["rsi"] < 35) & rsi_up) - 8 * (d["rsi"] > 75)
                + 5 * (d["macd_hist"] > 0) + 5 * (d["macd_hist"] > d["macd_hist"].shift(1))
                + 4 * macd_cross.rolling(3).max().fillna(0).astype(bool)
                + 3 * stoch_cross).astype(float).clip(upper=25)

    candle = candle_score.clip(-20, 20)

    up_day = c > d["Open"]
    volume = (5 * ((d["rel_vol"] > 1.3) & up_day) + 5 * (d["obv"] > d["obv_ema"])
              - 5 * ((d["rel_vol"] > 1.3) & ~up_day)).astype(float)

    atr_ok = d["atr_pct"].between(s["min_atr_pct"], s["max_atr_pct"])
    squeeze = d["bb_width"] <= d["bb_width"].rolling(120, min_periods=60).quantile(0.2)
    recent_squeeze = squeeze.rolling(5).max().fillna(0).astype(bool)
    breakout = (c > d["high20"]) & (d["rel_vol"] > 1.2)
    support_bounce = (d["Low"] <= d["low20"] * 1.02) & (candle_score > 0)
    setup = (5 * atr_ok - 10 * ~atr_ok + 5 * (recent_squeeze & breakout) + 5 * breakout
             + 5 * support_bounce).astype(float).clip(upper=15)

    overextended = -10.0 * (c > d["ema20"] + 2.5 * d["atr"])

    total = (trend + momentum + candle + volume + setup + overextended).clip(0, 100)
    total[d["sma200"].isna() | d["atr"].isna() | d["adx"].isna()] = np.nan
    return pd.DataFrame({
        "sc_trend": trend, "sc_momentum": momentum, "sc_candle": candle, "sc_volume": volume,
        "sc_setup": setup + overextended, "tech_score": total,
    })


def analyze_frame(df: pd.DataFrame, s: dict, weights: dict | None = None,
                  candle_override: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Göstergeler + mum ve grafik formasyonları + teknik skor.

    weights         : Formasyon Laboratuvarı ağırlıkları (None -> ders kitabı ağırlıkları)
    candle_override : formasyon skorunu dışarıdan ver (short tarafı için: -orijinal skor)
    """
    d = add_indicators(df)
    pats, cscore = detect_patterns(d, weights)
    cp = detect_chart_patterns(d)
    cscore = cscore + chart_score(cp, weights)
    pats = pats.join(cp)
    if candle_override is not None:
        cscore = candle_override.reindex(d.index).fillna(0.0)
    d["candle_score"] = cscore
    d = d.join(technical_scores(d, cscore, s))
    return d, pats


# ----------------------------------------------------------------------------- piyasa rejimi
def regime_series(qqq: pd.DataFrame | None, vix: pd.DataFrame | None) -> pd.Series:
    """Her gün için skor çarpanı (0.64-1.0). Zayıf piyasada sinyaller zorlaşır."""
    if qqq is None or qqq.empty:
        return pd.Series(dtype=float)
    c = qqq["Close"]
    s50, s200 = sma(c, 50), sma(c, 200)
    mult = pd.Series(1.0, index=c.index)
    mult[c < s200] *= 0.8
    mult[(c >= s200) & (c < s50)] *= 0.9
    if vix is not None and not vix.empty:
        v = vix["Close"].reindex(c.index).ffill()
        mult[v > 30] *= 0.8
        mult[(v > 22) & (v <= 30)] *= 0.92
    return mult


def regime_short_series(qqq: pd.DataFrame | None) -> pd.Series:
    """Short için çarpan: QQQ güçlü yükseliş trendindeyse (50 ve 200 g. üstü) short zorlaşır."""
    if qqq is None or qqq.empty:
        return pd.Series(dtype=float)
    c = qqq["Close"]
    mult = pd.Series(1.0, index=c.index)
    mult[(c > sma(c, 50)) & (c > sma(c, 200))] = 0.85
    return mult


def regime_notes(qqq: pd.DataFrame | None, vix: pd.DataFrame | None) -> dict:
    out = {"multiplier": 1.0, "notes": [], "qqq": None, "vix": None}
    if qqq is None or qqq.empty:
        out["notes"].append("QQQ verisi yok - rejim filtresi devre dışı")
        return out
    c = qqq["Close"]
    s50, s200 = sma(c, 50).iloc[-1], sma(c, 200).iloc[-1]
    out["qqq"] = float(c.iloc[-1])
    out["multiplier"] = float(regime_series(qqq, vix).iloc[-1])
    if c.iloc[-1] < s200:
        out["notes"].append("QQQ 200 günlük ortalamanın ALTINDA -> ayı piyasası riski")
    elif c.iloc[-1] < s50:
        out["notes"].append("QQQ 50 günlük ortalamanın altında -> zayıflama")
    else:
        out["notes"].append("QQQ yükseliş trendinde (50 ve 200 g. ortalamanın üstünde)")
    if vix is not None and not vix.empty:
        v = float(vix["Close"].iloc[-1])
        out["vix"] = v
        out["notes"].append(f"VIX {v:.1f} -> " + ("yüksek korku" if v > 30 else "artmış oynaklık" if v > 22 else "sakin piyasa"))
    return out


# ----------------------------------------------------------------------------- işlem planı
def trade_plan(close: float, atr_val: float, s: dict) -> dict:
    stop_dist = s["stop_atr_mult"] * atr_val
    target_dist = max(s["target_atr_mult"] * atr_val, close * s["min_target_pct"] / 100)
    return {
        "entry": close,
        "limit": close * (1 + s["max_entry_gap_pct"] / 100),
        "stop": close - stop_dist,
        "target": close + target_dist,
        "target_pct": target_dist / close * 100,
        "stop_pct": stop_dist / close * 100,
        "rr": target_dist / stop_dist if stop_dist > 0 else 0.0,
    }


# ----------------------------------------------------------------------------- canlı tarama
def get_universe(cfg: dict) -> list[str]:
    if cfg.get("universe") == "custom":
        return [t.upper() for t in cfg.get("custom_tickers", [])]
    return get_nasdaq100(cfg["data"]["cache_dir"])


def load_data(cfg: dict, tickers: list[str], period: str | None = None) -> dict[str, pd.DataFrame]:
    dc = cfg["data"]
    data = download_ohlcv(tickers, period=period or dc["period"], interval=dc["interval"],
                          cache_dir=dc["cache_dir"], ttl_minutes=dc["cache_ttl_minutes"])
    if not dc.get("use_partial_bar", False):
        data = {t: drop_partial_bar(df) for t, df in data.items()}
    return data


def _enrich(t: str, s: dict) -> tuple[str, dict, dict]:
    news = news_sentiment(t, s.get("news_max_age_hours", 72))
    an = analyst_view(t, s.get("avoid_earnings_days", 2))
    return t, news, an


def scan(cfg: dict, tickers: list[str] | None = None, enrich: bool = True, progress=None) -> tuple[list[dict], dict]:
    s = cfg["strategy"]
    tickers = tickers or get_universe(cfg)
    data = load_data(cfg, tickers + ["QQQ", "^VIX"])
    regime = regime_notes(data.get("QQQ"), data.get("^VIX"))
    w1d = load_weights(cfg, "1d")

    results: list[dict] = []
    for t in tickers:
        df = data.get(t)
        if df is None or len(df) < MIN_BARS:
            continue
        try:
            d, pats = analyze_frame(df, s, w1d)
        except Exception as e:  # noqa: BLE001
            log.warning("%s analiz edilemedi: %s", t, e)
            continue
        last = d.iloc[-1]
        if pd.isna(last["tech_score"]) or last["dollar_vol20"] < s["min_avg_dollar_volume"]:
            continue
        results.append({
            "ticker": t, "date": d.index[-1].date(), "close": float(last["Close"]),
            "tech": float(last["tech_score"]), "rsi": float(last["rsi"]), "atr": float(last["atr"]),
            "atr_pct": float(last["atr_pct"]), "adx": float(last["adx"]), "rel_vol": float(last["rel_vol"]),
            "patterns": patterns_on(pats), "components": {k: float(last[k]) for k in
                ("sc_trend", "sc_momentum", "sc_candle", "sc_volume", "sc_setup")},
            "news": None, "analyst": None, "news_pts": 0.0, "analyst_pts": 0.0, "flags": [],
        })

    results.sort(key=lambda r: r["tech"], reverse=True)

    if enrich and results:
        cand = [r for r in results[: s["enrich_top_n"]]]
        by_t = {r["ticker"]: r for r in cand}
        with ThreadPoolExecutor(max_workers=6) as ex:
            for t, news, an in ex.map(lambda x: _enrich(x, s), list(by_t)):
                r = by_t[t]
                r["news"], r["analyst"] = news, an
                r["news_pts"] = 10 * news["score"]
                r["analyst_pts"] = an["score"]
                if an["earnings_soon"]:
                    r["flags"].append(f"BİLANÇO {an['earnings_date']}")
                if progress:
                    progress(t)

    for r in results:
        raw = r["tech"] + r["news_pts"] + r["analyst_pts"]
        r["score"] = float(np.clip(raw * regime["multiplier"], 0, 100))
        r.update(trade_plan(r["close"], r["atr"], s))
        ok = (r["score"] >= s["buy_threshold"] and r["rr"] >= s["min_reward_risk"]
              and s["min_atr_pct"] <= r["atr_pct"] <= s["max_atr_pct"] and not r["flags"])
        if r["news"] is None and enrich:
            ok = False  # haber/analist kontrolünden geçmemiş adaya AL verilmez
        r["signal"] = "AL" if ok else ("İZLE" if r["score"] >= s["buy_threshold"] - 10 else "-")
    results.sort(key=lambda r: r["score"], reverse=True)
    return results, regime
