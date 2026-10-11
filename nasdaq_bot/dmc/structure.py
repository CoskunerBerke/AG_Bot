"""Piyasa yapısı primitifleri (STRATEGY_SPEC §1). Saf fonksiyonlar; hepsi sadece verilen barlara bakar.

Fiyat yönü konvansiyonu: setup kodu her zaman LONG mantığıyla yazılır; short taraf barların
aynalanmasıyla (`mirror`) aynı kodla işlenir. Bu yüzden buradaki fonksiyonlar yön bilmez ya da
'bull'/'bear' parametresi alır.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Fiyatları ters çevirir: short mantığını long koduyla çalıştırmak için (High' = -Low ...)."""
    return pd.DataFrame({"Open": -df["Open"], "High": -df["Low"], "Low": -df["High"],
                         "Close": -df["Close"], "Volume": df["Volume"]}, index=df.index)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


def swing_points(df: pd.DataFrame, k: int = 1) -> tuple[pd.Series, pd.Series]:
    """3 mum fraktalı (k=1): bar i, k sol ve k sağ komşusundan yüksekse swing high. Bar i ancak i+k'da bilinir."""
    h, l = df["High"].to_numpy(), df["Low"].to_numpy()
    n = len(df)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(k, n - k):
        win_h, win_l = h[i - k:i + k + 1], l[i - k:i + k + 1]
        sh[i] = h[i] == win_h.max() and (win_h == h[i]).sum() == 1
        sl[i] = l[i] == win_l.min() and (win_l == l[i]).sum() == 1
    return pd.Series(sh, df.index), pd.Series(sl, df.index)


@dataclass
class FVG:
    kind: str            # 'bull' (boşluk altta destek) | 'bear'
    bottom: float
    top: float
    t: pd.Timestamp      # 3. mumun başlangıç zamanı (FVG bu mumun kapanışında bilinir)
    tf: int              # dakika
    closed_through: bool = False   # gövde kapanışla tamamen geçildi (geçersiz / IFVG)
    tapped: bool = False

    def contains(self, lo: float, hi: float) -> bool:
        return lo <= self.top and hi >= self.bottom


def find_fvgs(df: pd.DataFrame, tf: int, min_size: float = 0.0) -> list[FVG]:
    """Bar listesindeki FVG'ler ve df sonuna kadar dokunma/geçersizlik durumları."""
    h, l, c = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    idx = df.index
    out: list[FVG] = []
    for i in range(2, len(df)):
        if l[i] > h[i - 2] and l[i] - h[i - 2] > min_size:
            g = FVG("bull", h[i - 2], l[i], idx[i], tf)
        elif h[i] < l[i - 2] and l[i - 2] - h[i] > min_size:
            g = FVG("bear", h[i], l[i - 2], idx[i], tf)
        else:
            continue
        for j in range(i + 1, len(df)):
            if g.kind == "bull":
                g.tapped |= l[j] <= g.top
                if c[j] < g.bottom:
                    g.closed_through = True
                    break
            else:
                g.tapped |= h[j] >= g.bottom
                if c[j] > g.top:
                    g.closed_through = True
                    break
        out.append(g)
    return out


def mirror_fvg(g: FVG) -> FVG:
    return FVG("bear" if g.kind == "bull" else "bull", -g.top, -g.bottom, g.t, g.tf, g.closed_through, g.tapped)


def ote(leg_lo: float, leg_hi: float, lo: float = 0.62, mid: float = 0.705, hi: float = 0.79) -> dict:
    """Yukarı impulse bacağının (leg_lo → leg_hi) geri çekilme seviyeleri (long için)."""
    r = leg_hi - leg_lo
    return {"zone_top": leg_hi - lo * r, "entry": leg_hi - mid * r, "zone_bottom": leg_hi - hi * r,
            "eq": leg_hi - 0.5 * r, "range": r}


def cisd_level(bars: pd.DataFrame, low_ts: pd.Timestamp) -> float | None:
    """Change in State of Delivery: dibi yapan ardışık düşüş mumlarının ilkinin açılışı.
    Bu seviyenin üstünde gelen kapanış = yukarı yönlü teslimat değişimi (long tetik)."""
    upto = bars[bars.index <= low_ts]
    if upto.empty:
        return None
    o, c = upto["Open"].to_numpy(), upto["Close"].to_numpy()
    j = len(upto) - 1
    while j >= 0 and not (c[j] < o[j]):      # dip mumu boğa olabilir: son ayı mumunu bul
        j -= 1
    if j < 0:
        return None
    while j - 1 >= 0 and c[j - 1] < o[j - 1]:
        j -= 1
    return float(o[j])


def wickless_down_open(bar: pd.Series, tol: float) -> bool:
    """Mum tepesinden açılıp aşağı gitti (üst fitil yok): aşağı hareket manipülasyon adayı (long-dönüş)."""
    return bar["High"] - bar["Open"] <= tol and bar["Close"] < bar["Open"]
