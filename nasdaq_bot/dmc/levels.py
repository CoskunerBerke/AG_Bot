"""Gün öncesi hazırlık (STRATEGY_SPEC §3): likidite seviyeleri, açık HTF FVG'ler ve günlük bias.

`prepare_day` sadece hazırlık anından (varsayılan 09:30) ÖNCE kapanmış barları kullanır.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd

from .data import resample, rth_daily
from .structure import FVG, RB, find_fvgs, find_rbs, mirror_fvg, swing_points


@dataclass
class DayLevels:
    day: date
    prev_day: date | None
    pdh: float | None = None
    pdl: float | None = None
    pdc: float | None = None
    pmh: float | None = None          # premarket 04:00–09:29
    pml: float | None = None
    onh: float | None = None          # önceki 16:00 → 09:29 (overnight)
    onl: float | None = None
    pwh: float | None = None          # önceki haftanın RTH high/low
    pwl: float | None = None
    bias: str = "neutral"             # long | short | neutral
    atr1: float | None = None         # önceki günün medyan 1 dk mum boyu (stop ölçeği)
    asl: float | None = None          # Asya seansı (20:00-00:00) dibi / tepesi — sadece vadeli veri
    ash: float | None = None
    lnl: float | None = None          # Londra (02:00-05:00)
    lnh: float | None = None
    mid_open: float | None = None     # 00:00 gece yarısı açılışı
    tdo: float | None = None          # 18:00 gerçek gün açılışı (vadeli)
    fvgs: list[FVG] = field(default_factory=list)   # hazırlık anında açık (geçersizleşmemiş) HTF FVG'ler
    rbs: list[RB] = field(default_factory=list)     # hazırlık anında geçerli, test edilmemiş HTF RB'ler
    msnr: list[tuple] = field(default_factory=list) # (fiyat, rol 'sup'|'res', tf) — fresh MSNR seviyeleri
    htf_sh: list[float] = field(default_factory=list)  # [K] alınmamış 1 saatlik swing high'lar (sıradaki yakıt)
    htf_sl: list[float] = field(default_factory=list)

    def above(self) -> dict[str, float]:
        """Long hedef adayları için yukarıdaki likidite."""
        return {k: v for k, v in {"pdh": self.pdh, "pmh": self.pmh, "onh": self.onh, "pwh": self.pwh,
                                  "ash": self.ash, "lnh": self.lnh}.items() if v}

    def below(self) -> dict[str, float]:
        return {k: v for k, v in {"pdl": self.pdl, "pml": self.pml, "onl": self.onl, "pwl": self.pwl,
                                  "asl": self.asl, "lnl": self.lnl}.items() if v}

    def mirrored(self) -> "DayLevels":
        """Short tarafı long koduyla işlemek için fiyatları ters çevrilmiş kopya (high↔low yer değiştirir)."""
        neg = lambda x: -x if x is not None else None
        return replace(self, pdh=neg(self.pdl), pdl=neg(self.pdh), pdc=neg(self.pdc), pmh=neg(self.pml),
                       pml=neg(self.pmh), onh=neg(self.onl), onl=neg(self.onh), pwh=neg(self.pwl), pwl=neg(self.pwh),
                       ash=neg(self.asl), asl=neg(self.ash), lnh=neg(self.lnl), lnl=neg(self.lnh),
                       mid_open=neg(self.mid_open), tdo=neg(self.tdo), bias={"long": "short", "short": "long"}.get(self.bias, "neutral"),
                       fvgs=[mirror_fvg(g) for g in self.fvgs], rbs=[mirror_rb(r) for r in self.rbs],
                       msnr=[(-p, 'res' if r == 'sup' else 'sup', tf) for p, r, tf in self.msnr],
                       htf_sh=[-x for x in self.htf_sl], htf_sl=[-x for x in self.htf_sh])

    def htf_above(self) -> dict[str, float]:
        """Long hedefi olarak alınmamış 1 saatlik swing high'lar (Kaan: fiyat swing'den sıradaki swing'e gider)."""
        return {f"sh60_{i}": x for i, x in enumerate(self.htf_sh)}


def mirror_rb(r: RB) -> RB:
    return RB("bear" if r.kind == "bull" else "bull", -r.hi, -r.lo, r.t, r.known_at, r.tf, list(r.swept),
              r.touches, r.invalid)


def daily_bias(daily: pd.DataFrame, lookback: int = 15) -> str:
    """[Y] Son geçerli günlük MSS yönü: kapanış son onaylı swing high'ın üstüne çıktıysa long,
    son swing low'un altına indiyse short. Son `lookback` günde kırılım yoksa neutral."""
    if len(daily) < 5:
        return "neutral"
    sh, sl = swing_points(daily, k=1)
    hi = lo = None
    bias, last_break = "neutral", None
    rows = list(daily.itertuples())
    for i, r in enumerate(rows):
        # i. barın swing olduğu ancak i+1'de bilinir → i-1'i şimdi onayla
        if i >= 2:
            if sh.iloc[i - 1]:
                hi = rows[i - 1].High
            if sl.iloc[i - 1]:
                lo = rows[i - 1].Low
        if hi is not None and r.Close > hi:
            bias, last_break, hi = "long", i, None
        elif lo is not None and r.Close < lo:
            bias, last_break, lo = "short", i, None
    if last_break is None or len(rows) - 1 - last_break > lookback:
        return "neutral"
    return bias


def msnr_levels(bars: pd.DataFrame, tf: int, tol_frac: float = 0.15) -> list[tuple]:
    """Malaysian SNR (transcript1transcript1): iki mumun gövdelerinin buluştuğu seviye.
    V (destek): ayı mumu + boğa mumu, kapanış≈açılış, gövde dibi komşulardan düşük. A (direnç): tersi.
    Fresh/unfresh: fitil değerse unfresh; gövde kapanışıyla kırılırsa rol değişir ve yeniden fresh olur.
    Dönüş: hazırlık anında FRESH olan seviyeler (fiyat, 'sup'|'res', tf)."""
    o, h, l, c = (bars[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    n = len(bars)
    out = []
    for i in range(2, n - 1):
        rng = max(h[i - 1] - l[i - 1], h[i] - l[i], 1e-9)
        if abs(c[i - 1] - o[i]) > tol_frac * rng:
            continue
        lvl = (c[i - 1] + o[i]) / 2
        if c[i - 1] < o[i - 1] and c[i] > o[i] and min(o[i - 2], c[i - 2]) > lvl and min(o[i + 1], c[i + 1]) > lvl:
            role = "sup"
        elif c[i - 1] > o[i - 1] and c[i] < o[i] and max(o[i - 2], c[i - 2]) < lvl and max(o[i + 1], c[i + 1]) < lvl:
            role = "res"
        else:
            continue
        fresh = True
        for j in range(i + 2, n):
            if role == "sup":
                if c[j] < lvl:
                    role, fresh = "res", True
                elif l[j] <= lvl:
                    fresh = False
            else:
                if c[j] > lvl:
                    role, fresh = "sup", True
                elif h[j] >= lvl:
                    fresh = False
        if fresh:
            out.append((float(lvl), role, tf))
    return out


def am_ranges(df1m: pd.DataFrame) -> pd.Series:
    """Her günün NY AM (09:30–11:59) range'i — Lunch 'NY AM akümüle geçti mi' karşılaştırması için."""
    t = df1m.index.time
    am = df1m[(t >= dtime(9, 30)) & (t < dtime(12, 0))]
    g = am.groupby(am.index.date)
    s = g["High"].max() - g["Low"].min()
    s.index = pd.to_datetime(s.index)
    return s


def am_range_median(ranges: pd.Series, day: date, n: int = 20) -> float | None:
    prev = ranges[ranges.index < pd.Timestamp(day)].tail(n)
    return float(prev.median()) if len(prev) >= 5 else None


def prepare_day(df1m: pd.DataFrame, day: date, daily: pd.DataFrame | None = None,
                prep_time: dtime = dtime(9, 30), fvg_cfg: dict | None = None) -> DayLevels:
    """`day` için seviyeler. df1m en az önceki ~10 günü içermeli. `daily` verilirse (önceden hesaplanmış RTH
    günlükleri) tekrar hesaplanmaz."""
    fvg_cfg = fvg_cfg or {}
    t0 = pd.Timestamp(datetime.combine(day, prep_time))
    hist = df1m[df1m.index < t0]
    if daily is None:
        daily = rth_daily(hist)
    prev = daily[daily.index < pd.Timestamp(day)]
    lv = DayLevels(day, prev.index[-1].date() if len(prev) else None)
    if len(prev):
        p = prev.iloc[-1]
        lv.pdh, lv.pdl, lv.pdc = float(p.High), float(p.Low), float(p.Close)
        pdb = hist[(hist.index >= pd.Timestamp(datetime.combine(lv.prev_day, dtime(9, 30)))) &
                   (hist.index < pd.Timestamp(datetime.combine(lv.prev_day, dtime(16, 0))))]
        if len(pdb):
            lv.atr1 = float((pdb["High"] - pdb["Low"]).median())
        lv.bias = daily_bias(prev.tail(60))
        wk = prev[prev.index.to_period("W") == (pd.Timestamp(day) - pd.Timedelta(days=7)).to_period("W")]
        if len(wk):
            lv.pwh, lv.pwl = float(wk.High.max()), float(wk.Low.min())
    d0 = pd.Timestamp(datetime.combine(day, dtime(0, 0)))
    asia = hist[(hist.index >= d0 - pd.Timedelta(hours=4)) & (hist.index < d0)]
    if len(asia):
        lv.ash, lv.asl = float(asia.High.max()), float(asia.Low.min())
    lon = hist[(hist.index >= d0 + pd.Timedelta(hours=2)) & (hist.index < d0 + pd.Timedelta(hours=5))]
    if len(lon):
        lv.lnh, lv.lnl = float(lon.High.max()), float(lon.Low.min())
    t18 = hist[hist.index >= d0 - pd.Timedelta(hours=6)]
    if len(t18) and t18.index[0] < d0 - pd.Timedelta(hours=6) + pd.Timedelta(minutes=5):
        lv.tdo = float(t18.Open.iloc[0])
    mo = hist[hist.index >= d0]
    if len(mo) and mo.index[0] < d0 + pd.Timedelta(minutes=5):
        lv.mid_open = float(mo.Open.iloc[0])
    pm = hist[hist.index >= pd.Timestamp(datetime.combine(day, dtime(4, 0)))]
    if len(pm):
        lv.pmh, lv.pml = float(pm.High.max()), float(pm.Low.min())
    if lv.prev_day:
        on = hist[hist.index >= pd.Timestamp(datetime.combine(lv.prev_day, dtime(16, 0)))]
        if len(on):
            lv.onh, lv.onl = float(on.High.max()), float(on.Low.min())
    # Açık HTF FVG'ler: 15 dk son N gün, 60 dk son M gün (RTH + extended dahil)
    for tf, days in ((15, fvg_cfg.get("lookback_days_15m", 3)), (60, fvg_cfg.get("lookback_days_60m", 10))):
        start = pd.Timestamp(day) - pd.Timedelta(days=days * 7 // 5 + 2)
        bars = resample(hist[hist.index >= start], tf)
        bars = bars[bars.index + pd.Timedelta(minutes=tf) <= t0]
        lv.fvgs += [g for g in find_fvgs(bars, tf) if not g.closed_through]
        lv.rbs += [r for r in find_rbs(bars, tf) if r.swept and not r.invalid and r.touches == 0]
    for tf, days in ((60, 15), (240, 30)):
        start = pd.Timestamp(day) - pd.Timedelta(days=days)
        bars = resample(hist[hist.index >= start], tf)
        bars = bars[bars.index + pd.Timedelta(minutes=tf) <= t0]
        lv.msnr += msnr_levels(bars, tf)
    b60 = resample(hist[hist.index >= pd.Timestamp(day) - pd.Timedelta(days=HTF_SWING_DAYS)], 60)
    b60 = b60[b60.index + pd.Timedelta(minutes=60) <= t0]
    lv.htf_sh, lv.htf_sl = untaken_swings(b60, HTF_SWING_K)
    return lv


HTF_SWING_K, HTF_SWING_DAYS = 2, 7


def untaken_swings(bars: pd.DataFrame, k: int) -> tuple[list[float], list[float]]:
    """[K] SIFIRDAN #2/#3: swing = ortadaki mum komşularından uzun; alınmamış swing = sıradaki likidite (yakıt).
    Sonrasında fiyatın aşmadığı swing high'lar ve kırmadığı swing low'lar."""
    if len(bars) < 2 * k + 1:
        return [], []
    sh, sl = swing_points(bars, k)
    h, l = bars["High"].to_numpy(), bars["Low"].to_numpy()
    highs = [float(h[i]) for i in sh.to_numpy().nonzero()[0] if i + 1 >= len(h) or h[i + 1:].max() < h[i]]
    lows = [float(l[i]) for i in sl.to_numpy().nonzero()[0] if i + 1 >= len(l) or l[i + 1:].min() > l[i]]
    return sorted(highs), sorted(lows, reverse=True)
