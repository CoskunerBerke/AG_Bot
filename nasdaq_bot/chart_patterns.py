"""Grafik formasyonları: OBO, TOBO, İkili/Üçlü Tepe-Dip, Yükselen/Alçalan Üçgen, Boğa/Ayı Bayrağı.

Önemli: Tepe/dip (pivot) noktası ancak sağında `k` bar oluştuktan sonra kesinleşir. Tespit
yalnızca o ana kadar kesinleşmiş pivotları kullanır ve sinyal, BOYUN ÇİZGİSİNİN / direncin
kapanışla kırıldığı barda üretilir. Yani geleceği görmez; canlıda da aynı anda sinyal verir.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .rejection_blocks import RB_PATTERNS, detect_rejection_signals

# anahtar: (Türkçe ad, yön, varsayılan güç)
_CLASSIC: dict[str, tuple[str, int, int]] = {
    "head_shoulders": ("OBO (Omuz-Baş-Omuz)", -1, 3),
    "inv_head_shoulders": ("TOBO (Ters OBO)", +1, 3),
    "double_top": ("İkili Tepe", -1, 3),
    "double_bottom": ("İkili Dip", +1, 3),
    "triple_top": ("Üçlü Tepe", -1, 3),
    "triple_bottom": ("Üçlü Dip", +1, 3),
    "ascending_triangle": ("Yükselen Üçgen kırılımı", +1, 2),
    "descending_triangle": ("Alçalan Üçgen kırılımı", -1, 2),
    "bull_flag": ("Boğa Bayrağı", +1, 2),
    "bear_flag": ("Ayı Bayrağı", -1, 2),
}
# klasik grafik formasyonları + Rejection Block varyasyonları
CHART_PATTERNS: dict[str, tuple[str, int, int]] = {**_CLASSIC, **RB_PATTERNS}


def _pivots(high: np.ndarray, low: np.ndarray, k: int):
    n = len(high)
    hmax = pd.Series(high).rolling(2 * k + 1, center=True).max().to_numpy()
    lmin = pd.Series(low).rolling(2 * k + 1, center=True).min().to_numpy()
    ph = [i for i in range(k, n - k) if high[i] == hmax[i] and high[i] > high[i - 1]]
    pl = [i for i in range(k, n - k) if low[i] == lmin[i] and low[i] < low[i - 1]]
    return ph, pl


def detect_chart_patterns(df: pd.DataFrame, k: int = 3, max_span: int = 80, recent: int = 25) -> pd.DataFrame:
    """Her bar için formasyon kırılım sinyalleri (bool DataFrame)."""
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    n = len(c)
    if "atr" in df.columns:
        atr = df["atr"].to_numpy(float)
    else:
        tr = np.maximum(h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1)))
        atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    out = {key: np.zeros(n, dtype=bool) for key in _CLASSIC}
    if n < 50:
        return pd.DataFrame(out, index=df.index).join(detect_rejection_signals(df))

    ph_all, pl_all = _pivots(h, l, k)
    used: set = set()
    hi_ptr = lo_ptr = 0
    PH: list[int] = []
    PL: list[int] = []

    def fire(key, sig, t):
        if sig not in used:
            used.add(sig)
            out[key][t] = True

    for t in range(30, n):
        a_t = atr[t]
        if not np.isfinite(a_t) or a_t <= 0:
            continue
        # t anında kesinleşmiş pivotlar (pivot + k <= t)
        while hi_ptr < len(ph_all) and ph_all[hi_ptr] + k <= t:
            PH.append(ph_all[hi_ptr]); hi_ptr += 1
        while lo_ptr < len(pl_all) and pl_all[lo_ptr] + k <= t:
            PL.append(pl_all[lo_ptr]); lo_ptr += 1
        tol = 0.75 * a_t
        cross_dn = lambda lvl, lvl_prev=None: c[t] < lvl and c[t - 1] >= (lvl if lvl_prev is None else lvl_prev)
        cross_up = lambda lvl, lvl_prev=None: c[t] > lvl and c[t - 1] <= (lvl if lvl_prev is None else lvl_prev)

        # ---------------- İkili / Üçlü Tepe
        if len(PH) >= 2:
            a, b = PH[-2], PH[-1]
            if t - b <= recent and 5 <= b - a <= max_span and abs(h[a] - h[b]) <= tol:
                neck = l[a:b + 1].min()
                top = max(h[a], h[b])
                if (top - neck >= 1.5 * a_t and c[max(0, a - 20)] < neck
                        and h[b + 1:t + 1].max(initial=0) <= top + tol and cross_dn(neck)):
                    fire("double_top", ("dt", a, b), t)
            if len(PH) >= 3:
                a3, b3, c3 = PH[-3], PH[-2], PH[-1]
                tops = h[[a3, b3, c3]]
                if (t - c3 <= recent and c3 - a3 <= max_span + 30 and tops.max() - tops.min() <= tol):
                    neck = l[a3:c3 + 1].min()
                    if tops.mean() - neck >= 1.5 * a_t and h[c3 + 1:t + 1].max(initial=0) <= tops.max() + tol and cross_dn(neck):
                        fire("triple_top", ("tt", a3, b3, c3), t)
                # ---------------- OBO
                ls, hd, rs = a3, b3, c3
                if (t - rs <= recent and rs - ls <= max_span and h[hd] > h[ls] + 0.5 * a_t
                        and h[hd] > h[rs] + 0.5 * a_t and abs(h[ls] - h[rs]) <= 1.0 * a_t):
                    i1 = ls + int(np.argmin(l[ls:hd + 1]))
                    i2 = hd + int(np.argmin(l[hd:rs + 1]))
                    slope = (l[i2] - l[i1]) / max(i2 - i1, 1)
                    neck_t, neck_p = l[i2] + slope * (t - i2), l[i2] + slope * (t - 1 - i2)
                    if (h[hd] - neck_t >= 2 * a_t and c[max(0, ls - 20)] < min(l[i1], l[i2])
                            and h[rs + 1:t + 1].max(initial=0) <= h[rs] + tol and cross_dn(neck_t, neck_p)):
                        fire("head_shoulders", ("hs", ls, hd, rs), t)

        # ---------------- İkili / Üçlü Dip
        if len(PL) >= 2:
            a, b = PL[-2], PL[-1]
            if t - b <= recent and 5 <= b - a <= max_span and abs(l[a] - l[b]) <= tol:
                neck = h[a:b + 1].max()
                bot = min(l[a], l[b])
                if (neck - bot >= 1.5 * a_t and c[max(0, a - 20)] > neck
                        and l[b + 1:t + 1].min(initial=np.inf) >= bot - tol and cross_up(neck)):
                    fire("double_bottom", ("db", a, b), t)
            if len(PL) >= 3:
                a3, b3, c3 = PL[-3], PL[-2], PL[-1]
                bots = l[[a3, b3, c3]]
                if (t - c3 <= recent and c3 - a3 <= max_span + 30 and bots.max() - bots.min() <= tol):
                    neck = h[a3:c3 + 1].max()
                    if neck - bots.mean() >= 1.5 * a_t and l[c3 + 1:t + 1].min(initial=np.inf) >= bots.min() - tol and cross_up(neck):
                        fire("triple_bottom", ("tb", a3, b3, c3), t)
                # ---------------- TOBO
                ls, hd, rs = a3, b3, c3
                if (t - rs <= recent and rs - ls <= max_span and l[hd] < l[ls] - 0.5 * a_t
                        and l[hd] < l[rs] - 0.5 * a_t and abs(l[ls] - l[rs]) <= 1.0 * a_t):
                    i1 = ls + int(np.argmax(h[ls:hd + 1]))
                    i2 = hd + int(np.argmax(h[hd:rs + 1]))
                    slope = (h[i2] - h[i1]) / max(i2 - i1, 1)
                    neck_t, neck_p = h[i2] + slope * (t - i2), h[i2] + slope * (t - 1 - i2)
                    if (neck_t - l[hd] >= 2 * a_t and c[max(0, ls - 20)] > max(h[i1], h[i2])
                            and l[rs + 1:t + 1].min(initial=np.inf) >= l[rs] - tol and cross_up(neck_t, neck_p)):
                        fire("inv_head_shoulders", ("ihs", ls, hd, rs), t)

        # ---------------- Üçgenler (yatay direnç + yükselen dipler / yatay destek + alçalan tepeler)
        if len(PH) >= 2 and len(PL) >= 2:
            a, b = PH[-2], PH[-1]
            p1, p2 = PL[-2], PL[-1]
            if t - b <= recent and b - a <= 60 and abs(h[a] - h[b]) <= 0.5 * a_t:
                res = max(h[a], h[b])
                if a < p2 and p1 < p2 and l[p2] > l[p1] + 0.3 * a_t and t - p1 <= 70 and cross_up(res):
                    fire("ascending_triangle", ("at", a, b), t)
            if t - p2 <= recent and p2 - p1 <= 60 and abs(l[p1] - l[p2]) <= 0.5 * a_t:
                sup = min(l[p1], l[p2])
                if p1 < b and a < b and h[b] < h[a] - 0.3 * a_t and t - a <= 70 and cross_dn(sup):
                    fire("descending_triangle", ("dtri", p1, p2), t)

        # ---------------- Bayraklar (güçlü direk + dar, sığ konsolidasyon + kırılım)
        for m in range(4, 13):
            pe = t - m - 1           # direğin bitişi
            ps = pe - 8              # direğin başlangıç penceresi
            if ps < 0:
                break
            ch, cl = h[t - m:t].max(), l[t - m:t].min()
            gain = c[pe] - l[ps:pe + 1].min()
            if gain >= 3 * a_t and ch - cl <= 0.5 * gain and cl >= c[pe] - 0.6 * gain and cross_up(ch):
                fire("bull_flag", ("bf", pe), t)
                break
            drop = h[ps:pe + 1].max() - c[pe]
            if drop >= 3 * a_t and ch - cl <= 0.5 * drop and ch <= c[pe] + 0.6 * drop and cross_dn(cl):
                fire("bear_flag", ("bef", pe), t)
                break

    return pd.DataFrame(out, index=df.index).join(detect_rejection_signals(df))


def chart_score(cp: pd.DataFrame, weights: dict | None = None) -> pd.Series:
    from .candles import weight_of
    score = pd.Series(0.0, index=cp.index)
    for key, (_, direction, strength) in CHART_PATTERNS.items():
        w = weight_of(key, direction, strength, weights)
        if w and key in cp.columns:
            score += cp[key].astype(float) * w * 4
    return score
