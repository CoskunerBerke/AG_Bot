"""Japon mum formasyonları: tespit, trend bağlamı, hacim teyidi ve hisse bazında geçmiş başarı ölçümü."""
from __future__ import annotations

import numpy as np
import pandas as pd

# anahtar: (Türkçe ad, yön (+1 boğa / -1 ayı / 0 nötr), güç 1-3)
PATTERNS: dict[str, tuple[str, int, int]] = {
    "hammer": ("Çekiç", +1, 2),
    "inverted_hammer": ("Ters Çekiç", +1, 1),
    "bullish_engulfing": ("Yutan Boğa", +1, 3),
    "piercing_line": ("Delen Mum", +1, 2),
    "bullish_harami": ("Boğa Harami", +1, 1),
    "morning_star": ("Sabah Yıldızı", +1, 3),
    "three_white_soldiers": ("Üç Beyaz Asker", +1, 3),
    "tweezer_bottom": ("Cımbız Dip", +1, 2),
    "dragonfly_doji": ("Yusufçuk Doji", +1, 1),
    "bullish_marubozu": ("Boğa Marubozu", +1, 2),
    "hanging_man": ("Asılı Adam", -1, 2),
    "shooting_star": ("Kayan Yıldız", -1, 2),
    "bearish_engulfing": ("Yutan Ayı", -1, 3),
    "dark_cloud_cover": ("Kara Bulut Örtüsü", -1, 2),
    "bearish_harami": ("Ayı Harami", -1, 1),
    "evening_star": ("Akşam Yıldızı", -1, 3),
    "three_black_crows": ("Üç Kara Karga", -1, 3),
    "tweezer_top": ("Cımbız Tepe", -1, 2),
    "gravestone_doji": ("Mezar Taşı Doji", -1, 1),
    "bearish_marubozu": ("Ayı Marubozu", -1, 2),
    "doji": ("Doji (kararsızlık)", 0, 0),
    # --- kullanıcının paylaştığı görsellerden eklenenler
    "three_inside_up": ("Üç İçeride Yukarı (Üç Yukarı Dönüş)", +1, 2),
    "three_inside_down": ("Üç İçeride Aşağı (Üç Aşağı Dönüş)", -1, 2),
    "three_outside_up": ("Üç Dışarıda Yukarı", +1, 3),
    "three_outside_down": ("Üç Dışarıda Aşağı", -1, 3),
    "bullish_counter_attack": ("Boğa Karşı Saldırı", +1, 1),
    "bearish_counter_attack": ("Ayı Karşı Saldırı", -1, 1),
    # Klasik literatürde On-Neck düşüş devamıdır; paylaşılan görsel AL diyor. Laboratuvar karar verir.
    "on_neck": ("On-Neck", -1, 1),
    "top_reversal": ("Tepe Dönüş", -1, 2),
    "bottom_reversal": ("Dip Dönüş", +1, 2),
}


def detect_patterns(df: pd.DataFrame, weights: dict | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """Her bar için formasyon bayrakları (bool DataFrame) ve ağırlıklı mum skoru döndürür.

    weights: Formasyon Laboratuvarı'nın doğruladığı ağırlıklar ({key: {"weight": w}}).
             Verilirse yalnızca testi geçen formasyonlar skora katkı yapar.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    top = pd.concat([o, c], axis=1).max(axis=1)
    bot = pd.concat([o, c], axis=1).min(axis=1)
    upper, lower = h - top, bot - l
    green, red = c > o, c < o
    avg_body = body.rolling(20, min_periods=5).mean()
    long_body = body >= avg_body
    sma10 = c.rolling(10).mean()

    def down(k: int) -> pd.Series:  # k bar önce düşüş trendi
        return (c.shift(k) < sma10.shift(k)) & (c.shift(k) < c.shift(k + 5))

    def up(k: int) -> pd.Series:
        return (c.shift(k) > sma10.shift(k)) & (c.shift(k) > c.shift(k + 5))

    o1, c1, h1, l1 = o.shift(1), c.shift(1), h.shift(1), l.shift(1)
    o2, c2 = o.shift(2), c.shift(2)
    body1, body2 = body.shift(1), body.shift(2)
    green1, red1 = green.shift(1, fill_value=False), red.shift(1, fill_value=False)
    green2, red2 = green.shift(2, fill_value=False), red.shift(2, fill_value=False)
    long1 = long_body.shift(1, fill_value=False)
    long2 = long_body.shift(2, fill_value=False)
    mid1 = (o1 + c1) / 2
    mid2 = (o2 + c2) / 2

    hammer_shape = (lower >= 2 * body) & (upper <= 0.15 * rng) & (body > 0.1 * rng)
    inv_shape = (upper >= 2 * body) & (lower <= 0.15 * rng) & (body > 0.1 * rng)
    doji = body <= 0.1 * rng
    marubozu = (body >= 0.9 * rng) & long_body

    p = pd.DataFrame(index=df.index)
    p["hammer"] = hammer_shape & down(1)
    p["hanging_man"] = hammer_shape & up(1)
    p["inverted_hammer"] = inv_shape & down(1)
    p["shooting_star"] = inv_shape & up(1)
    p["doji"] = doji
    p["dragonfly_doji"] = doji & (lower >= 0.6 * rng) & (upper <= 0.1 * rng) & down(1)
    p["gravestone_doji"] = doji & (upper >= 0.6 * rng) & (lower <= 0.1 * rng) & up(1)
    p["bullish_engulfing"] = red1 & green & (o <= c1) & (c >= o1) & (body > body1) & down(1)
    p["bearish_engulfing"] = green1 & red & (o >= c1) & (c <= o1) & (body > body1) & up(1)
    p["piercing_line"] = red1 & long1 & green & (o < c1) & (c > mid1) & (c < o1) & down(1)
    p["dark_cloud_cover"] = green1 & long1 & red & (o > c1) & (c < mid1) & (c > o1) & up(1)
    p["bullish_harami"] = red1 & long1 & green & (o >= c1) & (c <= o1) & (body < 0.6 * body1) & down(1)
    p["bearish_harami"] = green1 & long1 & red & (o <= c1) & (c >= o1) & (body < 0.6 * body1) & up(1)
    small1 = body1 <= 0.3 * body2
    p["morning_star"] = (red2 & long2 & small1 & (top.shift(1) <= mid2) & green & (c > mid2) & down(2))
    p["evening_star"] = (green2 & long2 & small1 & (bot.shift(1) >= mid2) & red & (c < mid2) & up(2))
    soldier = green & (upper <= 0.3 * body) & (body >= 0.5 * avg_body)
    crow = red & (lower <= 0.3 * body) & (body >= 0.5 * avg_body)
    p["three_white_soldiers"] = (
        soldier & soldier.shift(1, fill_value=False) & soldier.shift(2, fill_value=False)
        & (c > c1) & (c1 > c2) & (o > o1) & (o < c1) & (o1 > o2) & (o1 < c2)
    )
    p["three_black_crows"] = (
        crow & crow.shift(1, fill_value=False) & crow.shift(2, fill_value=False)
        & (c < c1) & (c1 < c2) & (o < o1) & (o > c1) & (o1 < o2) & (o1 > c2)
    )
    p["bullish_marubozu"] = marubozu & green
    p["bearish_marubozu"] = marubozu & red
    p["tweezer_bottom"] = red1 & green & ((l - l1).abs() <= 0.002 * c) & down(1)
    p["tweezer_top"] = green1 & red & ((h - h1).abs() <= 0.002 * c) & up(1)
    # ---- Görsellerden eklenen formasyonlar
    inside1 = (o1 >= c2) & (c1 <= o2)  # boğa harami gövdesi (t-1, t-2 içinde)
    p["three_inside_up"] = red2 & long2 & green1 & inside1 & green & (c > o2) & down(3)
    p["three_inside_down"] = green2 & long2 & red1 & (o1 <= c2) & (c1 >= o2) & red & (c < o2) & up(3)
    p["three_outside_up"] = red2 & green1 & (o1 <= c2) & (c1 >= o2) & green & (c > c1) & down(2)
    p["three_outside_down"] = green2 & red1 & (o1 >= c2) & (c1 <= o2) & red & (c < c1) & up(2)
    p["bullish_counter_attack"] = red1 & long1 & green & long_body & (o < l1) & ((c - c1).abs() <= 0.1 * body1) & down(1)
    p["bearish_counter_attack"] = green1 & long1 & red & long_body & (o > h1) & ((c - c1).abs() <= 0.1 * body1) & up(1)
    p["on_neck"] = red1 & long1 & green & (o < l1) & ((c - l1).abs() <= 0.1 * body1) & down(1)
    rising3 = (c1 > c2) & (c2 > c.shift(3)) & (c.shift(3) > c.shift(4))
    falling3 = (c1 < c2) & (c2 < c.shift(3)) & (c.shift(3) < c.shift(4))
    p["top_reversal"] = rising3 & green1 & red & long_body & (c < o1) & (body > body1)
    p["bottom_reversal"] = falling3 & red1 & green & long_body & (c > o1) & (body > body1)
    p = p.fillna(False).astype(bool)

    # Hacim teyidi: ortalamanın 1.3 katı hacimle oluşan formasyon %50 daha değerli
    rel_vol = df["Volume"] / df["Volume"].rolling(20).mean()
    vol_mult = np.where(rel_vol > 1.3, 1.5, 1.0)
    score = pd.Series(0.0, index=df.index)
    for key, (_, direction, strength) in PATTERNS.items():
        w = weight_of(key, direction, strength, weights)
        if w:
            score += p[key].astype(float) * w * 4
    score = score * vol_mult
    return p, score


def weight_of(key: str, direction: int, strength: float, weights: dict | None) -> float:
    """İşaretli ağırlık. Laboratuvar sonucu varsa onu, yoksa ders kitabı gücünü kullanır."""
    if weights is not None:
        return float(weights.get(key, {}).get("weight", 0.0))
    return float(direction * strength)


def patterns_on(p: pd.DataFrame, i: int = -1) -> list[str]:
    from .chart_patterns import CHART_PATTERNS
    names = {k: v[0] for k, v in {**PATTERNS, **CHART_PATTERNS}.items()}
    row = p.iloc[i]
    return [names.get(k, k) for k in p.columns if row[k]]


def pattern_stats(df: pd.DataFrame, p: pd.DataFrame, horizon: int = 3, target_pct: float = 1.0) -> pd.DataFrame:
    """Bu hissenin geçmişinde her formasyondan sonra ne olduğunu ölçer.

    Giriş: formasyondan sonraki günün açılışı. 'Hedef %' = sonraki `horizon` gün içinde
    fiyatın en az target_pct yükselme oranı. Baz oran (tüm günler) ile karşılaştırılır.
    """
    entry = df["Open"].shift(-1)
    fwd_max = df["High"].iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1].shift(-1)
    fwd_close = df["Close"].shift(-horizon)
    hit = (fwd_max / entry - 1) * 100 >= target_pct
    ret = (fwd_close / entry - 1) * 100
    valid = entry.notna() & fwd_max.notna() & fwd_close.notna()

    rows = [{
        "Formasyon": "TÜM GÜNLER (baz)", "Yön": "", "Adet": int(valid.sum()),
        "Hedef%": hit[valid].mean() * 100, "Kazanma%": (ret[valid] > 0).mean() * 100,
        "OrtGetiri%": ret[valid].mean(),
    }]
    from .chart_patterns import CHART_PATTERNS
    for key, (name, direction, _) in {**PATTERNS, **CHART_PATTERNS}.items():
        if key not in p.columns:
            continue
        m = p[key] & valid
        n = int(m.sum())
        if n == 0:
            continue
        rows.append({
            "Formasyon": name, "Yön": {1: "Boğa", -1: "Ayı", 0: "Nötr"}[direction], "Adet": n,
            "Hedef%": hit[m].mean() * 100, "Kazanma%": (ret[m] > 0).mean() * 100,
            "OrtGetiri%": ret[m].mean(),
        })
    return pd.DataFrame(rows)
