"""Teknik göstergeler (harici kütüphane bağımlılığı olmadan, tamamen nedensel/geleceği görmeyen)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["Close"].shift(1)
    return pd.concat(
        [df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14):
    up = df["High"].diff()
    dn = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, n), pdi, mdi


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["Close"]
    d["ema9"], d["ema20"], d["ema50"] = ema(c, 9), ema(c, 20), ema(c, 50)
    d["sma50"], d["sma200"] = sma(c, 50), sma(c, 200)
    d["rsi"] = rsi(c)

    macd_line = ema(c, 12) - ema(c, 26)
    signal = macd_line.ewm(span=9, adjust=False).mean()
    d["macd"], d["macd_signal"], d["macd_hist"] = macd_line, signal, macd_line - signal

    d["atr"] = atr(d)
    d["atr_pct"] = d["atr"] / c * 100
    d["adx"], d["plus_di"], d["minus_di"] = adx(d)

    mid = sma(c, 20)
    sd = c.rolling(20, min_periods=20).std()
    d["bb_mid"], d["bb_up"], d["bb_low"] = mid, mid + 2 * sd, mid - 2 * sd
    d["bb_width"] = (d["bb_up"] - d["bb_low"]) / mid
    d["bb_pctb"] = (c - d["bb_low"]) / (d["bb_up"] - d["bb_low"])

    ll = d["Low"].rolling(14).min()
    hh = d["High"].rolling(14).max()
    d["stoch_k"] = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    d["stoch_d"] = d["stoch_k"].rolling(3).mean()

    d["obv"] = (np.sign(c.diff()).fillna(0) * d["Volume"]).cumsum()
    d["obv_ema"] = ema(d["obv"], 20)
    d["vol_avg20"] = d["Volume"].rolling(20).mean()
    d["rel_vol"] = d["Volume"] / d["vol_avg20"].replace(0, np.nan)
    d["dollar_vol20"] = (c * d["Volume"]).rolling(20).mean()

    # Önceki 20 günün direnci / desteği (bugünü içermez)
    d["high20"] = d["High"].rolling(20).max().shift(1)
    d["low20"] = d["Low"].rolling(20).min().shift(1)
    d["ret1"] = c.pct_change() * 100
    return d
