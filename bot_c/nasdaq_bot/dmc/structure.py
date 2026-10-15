"""Piyasa yapısı primitifleri (STRATEGY_SPEC §1). Saf ve vektörel fonksiyonlar; hepsi sadece verilen barlara bakar.

Fiyat yönü konvansiyonu: setup kodu her zaman LONG mantığıyla yazılır; short taraf barların
aynalanmasıyla (`mirror`) aynı kodla işlenir.
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
    if n >= 2 * k + 1:
        if k == 1:
            sh[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:])
            sl[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
        else:
            ch = np.ones(n - 2 * k, dtype=bool)
            cl = np.ones(n - 2 * k, dtype=bool)
            mid_h, mid_l = h[k:n - k], l[k:n - k]
            for d in range(1, k + 1):
                ch &= (mid_h > h[k - d:n - k - d]) & (mid_h > h[k + d:n - k + d])
                cl &= (mid_l < l[k - d:n - k - d]) & (mid_l < l[k + d:n - k + d])
            sh[k:n - k] = ch
            sl[k:n - k] = cl
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
    """Bar listesindeki FVG'ler ve df sonuna kadar dokunma/geçersizlik durumları (vektörel)."""
    n = len(df)
    if n < 3:
        return []
    h, l, c = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    idx = df.index
    bull_mask = (l[2:] - h[:-2]) > min_size
    bear_mask = (l[:-2] - h[2:]) > min_size
    cand_indices = np.flatnonzero(bull_mask | bear_mask) + 2
    out: list[FVG] = []
    for i in cand_indices:
        if bull_mask[i - 2]:
            bot, top = float(h[i - 2]), float(l[i])
            after_l, after_c = l[i + 1:], c[i + 1:]
            closed = bool(np.any(after_c < bot)) if len(after_c) else False
            if closed:
                cut = int(np.argmax(after_c < bot))
                tapped = bool(np.any(after_l[:cut + 1] <= top))
            else:
                tapped = bool(np.any(after_l <= top)) if len(after_l) else False
            out.append(FVG("bull", bot, top, idx[i], tf, closed, tapped))
        else:
            bot, top = float(h[i]), float(l[i - 2])
            after_h, after_c = h[i + 1:], c[i + 1:]
            closed = bool(np.any(after_c > top)) if len(after_c) else False
            if closed:
                cut = int(np.argmax(after_c > top))
                tapped = bool(np.any(after_h[:cut + 1] >= bot))
            else:
                tapped = bool(np.any(after_h >= bot)) if len(after_h) else False
            out.append(FVG("bear", bot, top, idx[i], tf, closed, tapped))
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
    while j >= 0 and not (c[j] < o[j]):
        j -= 1
    if j < 0:
        return None
    while j - 1 >= 0 and c[j - 1] < o[j - 1]:
        j -= 1
    return float(o[j])


@dataclass
class RB:
    """Rejection Block (Kaan/DMC): likidite süpüren swing mumunun GÖVDE UCU ile FİTİL UCU arası."""
    kind: str
    lo: float
    hi: float
    t: pd.Timestamp
    known_at: pd.Timestamp
    tf: int
    swept: list[str]
    touches: int = 0
    invalid: bool = False

    @property
    def body(self) -> float:
        return self.hi if self.kind == "bull" else self.lo

    @property
    def wick(self) -> float:
        return self.lo if self.kind == "bull" else self.hi

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2


def find_rbs(df: pd.DataFrame, tf: int, k: int = 1, lookback: int = 30, eq_tol: float = 0.0002,
             min_wick_frac: float = 0.0) -> list[RB]:
    """df içindeki RB'ler; dokunma/geçersizlik durumu df sonuna kadar izlenir."""
    if len(df) < 2 * k + 2:
        return []
    o, h, l, c = (df[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    idx = df.index
    sh_s, sl_s = swing_points(df, k)
    sh, sl = sh_s.to_numpy(), sl_s.to_numpy()
    out: list[RB] = []
    step = pd.Timedelta(minutes=tf)
    sw_indices = np.flatnonzero(sh[k:len(df) - k] | sl[k:len(df) - k]) + k
    for i in sw_indices:
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        for kind in ("bull", "bear"):
            if (kind == "bull" and not sl[i]) or (kind == "bear" and not sh[i]):
                continue
            if kind == "bull":
                lo, hi = float(l[i]), float(min(o[i], c[i]))
                if (hi - lo) / rng < min_wick_frac:
                    continue
                prev = [j for j in range(max(0, i - lookback), i - k) if sl[j]]
                swept = ["swing"] if any(l[i] < l[j] for j in prev) else []
                if any(abs(l[j] - l[i]) <= eq_tol * l[i] for j in prev):
                    swept.append("eql")
            else:
                lo, hi = float(max(o[i], c[i])), float(h[i])
                if (hi - lo) / rng < min_wick_frac:
                    continue
                prev = [j for j in range(max(0, i - lookback), i - k) if sh[j]]
                swept = ["swing"] if any(h[i] > h[j] for j in prev) else []
                if any(abs(h[j] - h[i]) <= eq_tol * h[i] for j in prev):
                    swept.append("eqh")
            rb = RB(kind, lo, hi, idx[i], idx[i + k] + step, tf, swept)
            inside = True
            for j in range(i + k + 1, len(df)):
                if kind == "bull":
                    if c[j] < lo:
                        rb.invalid = True
                        break
                    touching = l[j] <= hi
                else:
                    if c[j] > hi:
                        rb.invalid = True
                        break
                    touching = h[j] >= lo
                if touching and not inside:
                    rb.touches += 1
                inside = touching
            out.append(rb)
    return out


def stdv_levels(leg_start: float, leg_end: float, mults=(-1.0, -2.0, -2.5, -4.0)) -> dict[float, float]:
    """Manipülasyon bacağının standart sapma projeksiyonları (ICT/Kaan 'STDV')."""
    r = leg_end - leg_start
    return {m: leg_end + (-m) * r for m in mults}


def wickless_down_open(bar: pd.Series, tol: float) -> bool:
    """Mum tepesinden açılıp aşağı gitti (üst fitil yok): aşağı hareket manipülasyon adayı (long-dönüş)."""
    return bar["High"] - bar["Open"] <= tol and bar["Close"] < bar["Open"]
