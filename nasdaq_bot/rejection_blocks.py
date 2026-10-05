"""Rejection Block (Ret Bloğu) tespiti - ICT / Smart Money kavramı.

TANIM
  Ayı RB : bir TEPE (swing high) mumunun uzun ÜST fitili. Fiyat oraya çıkmış ama satıcılar geri
           itmiş. Bölge = fitilin tepesi (High) ile gövdenin üst kenarı arası.
  Boğa RB: bir DİP (swing low) mumunun uzun ALT fitili. Bölge = fitilin dibi (Low) ile gövdenin
           alt kenarı arası.

  Bölgeyi oluşturan "küme": pivot mumu ve iki komşusu. Gövde kenarı olarak kümedeki EN UÇ gövde
  kullanılır (ayıda en yüksek gövde, boğada en düşük gövde) - böylece bölge sadece saf fitil alanıdır.

YAŞAM DÖNGÜSÜ (her şey o ana kadarki veriyle; geleceği görmez)
  1. Pivot ancak sağında `k` bar oluşunca kesinleşir -> bölge o zaman AKTİF olur.
  2. Fiyat bölgeden en az `depart_atr` x ATR uzaklaşmalı (yoksa "test" sayılmaz).
  3. Fiyat geri dönüp bölgeye girince  -> ilk temas  (touch)
  4. Bölgeye girip gövde kenarının dışında kapanırsa -> RET (reject) = asıl sinyal.
  5. Kapanış fitilin ucunu (High/Low) geçerse bölge GEÇERSİZ olur. `max_age` bar sonra süresi dolar.

VARYASYONLAR (Formasyon Laboratuvarı hepsini ayrı ayrı test eder)
  touch  : bölgeye ilk temas
  reject : ret kapanışı (temel sinyal)
  sweep  : ret + RB mumu bir önceki tepe/dibin likiditesini süpürmüş (fitil eski tepenin üstüne
           çıkıp gövde altında kapanmış) - ICT'nin en güçlü kabul ettiği tip
  trend  : ret + ana trend yönünde (ayı RB için fiyat SMA50 altında ve SMA50 düşüyor)
  *_yapi : (sadece laboratuvar) aynı sinyal ama stop = fitil ucu + tampon, hedef = 2R
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# anahtar: (Türkçe ad, yön, ders kitabı gücü) - varyasyonlar üst üste binebildiği için güçler küçük
RB_PATTERNS: dict[str, tuple[str, int, int]] = {
    "rb_bull_touch": ("RB Boğa · ilk temas", +1, 1),
    "rb_bull_reject": ("RB Boğa · ret kapanışı", +1, 2),
    "rb_bull_sweep": ("RB Boğa · likidite süpürmeli ret", +1, 1),
    "rb_bull_trend": ("RB Boğa · trend yönünde ret", +1, 1),
    "rb_bear_touch": ("RB Ayı · ilk temas", -1, 1),
    "rb_bear_reject": ("RB Ayı · ret kapanışı", -1, 2),
    "rb_bear_sweep": ("RB Ayı · likidite süpürmeli ret", -1, 1),
    "rb_bear_trend": ("RB Ayı · trend yönünde ret", -1, 1),
}
# sadece laboratuvar: (ad, yön, temel sinyal anahtarı) - yapısal stop + R katı hedef ile test
RB_STRUCT: dict[str, tuple[str, int, str]] = {
    "rb_bull_reject_yapi": ("RB Boğa · ret + yapı stopu", +1, "rb_bull_reject"),
    "rb_bull_sweep_yapi": ("RB Boğa · süpürme + yapı stopu", +1, "rb_bull_sweep"),
    "rb_bull_trend_yapi": ("RB Boğa · trend + yapı stopu", +1, "rb_bull_trend"),
    "rb_bear_reject_yapi": ("RB Ayı · ret + yapı stopu", -1, "rb_bear_reject"),
    "rb_bear_sweep_yapi": ("RB Ayı · süpürme + yapı stopu", -1, "rb_bear_sweep"),
    "rb_bear_trend_yapi": ("RB Ayı · trend + yapı stopu", -1, "rb_bear_trend"),
}

DEFAULTS = {
    "pivot_k": 3,            # pivot onayı için sağda/solda bar sayısı
    "min_wick_ratio": 0.5,   # fitil / mum boyu (en az)
    "min_wick_atr": 0.25,    # fitil en az bu kadar ATR olmalı
    "depart_atr": 1.0,       # fiyat bölgeden en az bu kadar ATR uzaklaşmalı
    "max_age": 60,           # bar; sonrasında bölge eskir
    "max_zones": 6,          # her yönde aynı anda izlenen en fazla bölge
    "stop_buffer_atr": 0.1,  # yapısal stop = fitil ucu + 0.1 ATR
    "rr": 2.0,               # yapısal hedef = 2 x risk
}
PARAMS = dict(DEFAULTS)


def configure(cfg_section: dict | None) -> None:
    """config.yaml -> rejection: bölümünü uygular (load_config çağırır)."""
    PARAMS.clear()
    PARAMS.update(DEFAULTS)
    PARAMS.update({k: v for k, v in (cfg_section or {}).items() if k in DEFAULTS})


@dataclass
class Zone:
    side: int                # +1 boğa, -1 ayı
    pivot: int               # pivot mumunun indeksi
    active_from: int
    top: float
    bot: float
    sweep: bool
    departed: bool = False
    touched: int | None = None
    signal: int | None = None
    end: int | None = None
    status: str = "bekliyor"   # bekliyor / aktif / temas / RET / geçersiz / eskidi
    events: list = field(default_factory=list)

    @property
    def extreme(self) -> float:      # fitilin ucu (stop tarafı)
        return self.top if self.side < 0 else self.bot

    @property
    def edge(self) -> float:         # gövde kenarı (giriş tarafı)
        return self.bot if self.side < 0 else self.top


def _pivots(h: np.ndarray, l: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    w = 2 * k + 1
    hmax = pd.Series(h).rolling(w, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(w, center=True).min().to_numpy()
    prev_h, prev_l = np.r_[np.inf, h[:-1]], np.r_[-np.inf, l[:-1]]
    ph = (h == hmax) & (h > prev_h)     # eşit tepelerde sadece ilki
    pl = (l == lmin) & (l < prev_l)
    return ph, pl


def find_rejection_blocks(df: pd.DataFrame, params: dict | None = None):
    """-> (signals: bool DataFrame, stops: DataFrame [yapısal stop seviyesi], zones: list[Zone])."""
    p = {**PARAMS, **(params or {})}
    k = int(p["pivot_k"])
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    n = len(c)
    if "atr" in df.columns:
        atr = df["atr"].to_numpy(float)
    else:
        pc = np.r_[c[0], c[:-1]]
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    sma50 = df["sma50"].to_numpy(float) if "sma50" in df.columns else pd.Series(c).rolling(50).mean().to_numpy()

    sig = {key: np.zeros(n, dtype=bool) for key in RB_PATTERNS}
    stops = {key: np.full(n, np.nan) for key in RB_PATTERNS}
    zones: list[Zone] = []
    if n < 2 * k + 20:
        return pd.DataFrame(sig, index=df.index), pd.DataFrame(stops, index=df.index), zones

    ph, pl = _pivots(h, l, k)
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    rng = np.maximum(h - l, 1e-12)
    last_ph: list[int] = []   # onaylanmış pivot tepeler (süpürme kontrolü için)
    last_pl: list[int] = []
    alive: list[Zone] = []

    def fire(z: Zone, key: str, t: int, stop_lvl: float | None = None):
        if not sig[key][t]:
            sig[key][t] = True
            if stop_lvl is not None:
                stops[key][t] = stop_lvl

    for t in range(n):
        a_t = atr[t]
        # ---- t anında kesinleşen pivot (i = t - k) -> yeni bölge
        i = t - k
        if i >= 1 and np.isfinite(atr[i]) and atr[i] > 0:
            lo_i, hi_i = max(0, i - 1), min(n, i + 2)
            if ph[i]:
                wick = h[i] - body_hi[i]
                bot = body_hi[lo_i:hi_i].max()
                if wick / rng[i] >= p["min_wick_ratio"] and wick >= p["min_wick_atr"] * atr[i] and h[i] - bot >= 0.15 * atr[i]:
                    sweep = any(h[i] > h[j] > body_hi[i] for j in last_ph[-3:] if i - j <= 60)
                    z = Zone(-1, i, t, h[i], bot, sweep)
                    z.departed = bool(l[i + 1:t + 1].min() <= bot - p["depart_atr"] * atr[i])
                    zones.append(z); alive.append(z)
                last_ph.append(i)
            if pl[i]:
                wick = body_lo[i] - l[i]
                top = body_lo[lo_i:hi_i].min()
                if wick / rng[i] >= p["min_wick_ratio"] and wick >= p["min_wick_atr"] * atr[i] and top - l[i] >= 0.15 * atr[i]:
                    sweep = any(l[i] < l[j] < body_lo[i] for j in last_pl[-3:] if i - j <= 60)
                    z = Zone(+1, i, t, top, l[i], sweep)
                    z.departed = bool(h[i + 1:t + 1].max() >= top + p["depart_atr"] * atr[i])
                    zones.append(z); alive.append(z)
                last_pl.append(i)
            # her yönde en fazla max_zones bölge
            for side in (-1, 1):
                same = [z for z in alive if z.side == side]
                for z in same[:-int(p["max_zones"])]:
                    z.status, z.end = "eskidi", t
                    alive.remove(z)

        if not np.isfinite(a_t) or a_t <= 0:
            continue
        for z in list(alive):
            if z.active_from == t and z.status == "bekliyor":
                z.status = "aktif" if z.departed else "bekliyor"
                continue
            if t - z.pivot > p["max_age"]:
                z.status, z.end = "eskidi", t
                alive.remove(z)
                continue
            if z.side < 0:
                if c[t] > z.top:
                    z.status, z.end = "geçersiz", t
                    alive.remove(z)
                    continue
                if not z.departed:
                    if l[t] <= z.bot - p["depart_atr"] * a_t:
                        z.departed, z.status = True, "aktif"
                    continue
                if h[t] >= z.bot:
                    if z.touched is None:
                        z.touched, z.status = t, "temas"
                        fire(z, "rb_bear_touch", t)
                    if c[t] < z.bot:
                        stop_lvl = z.top + p["stop_buffer_atr"] * a_t
                        fire(z, "rb_bear_reject", t, stop_lvl)
                        if z.sweep:
                            fire(z, "rb_bear_sweep", t, stop_lvl)
                        if t >= 5 and c[t] < sma50[t] and sma50[t] < sma50[t - 5]:
                            fire(z, "rb_bear_trend", t, stop_lvl)
                        z.signal, z.status, z.end = t, "RET", t
                        alive.remove(z)
            else:
                if c[t] < z.bot:
                    z.status, z.end = "geçersiz", t
                    alive.remove(z)
                    continue
                if not z.departed:
                    if h[t] >= z.top + p["depart_atr"] * a_t:
                        z.departed, z.status = True, "aktif"
                    continue
                if l[t] <= z.top:
                    if z.touched is None:
                        z.touched, z.status = t, "temas"
                        fire(z, "rb_bull_touch", t)
                    if c[t] > z.top:
                        stop_lvl = z.bot - p["stop_buffer_atr"] * a_t
                        fire(z, "rb_bull_reject", t, stop_lvl)
                        if z.sweep:
                            fire(z, "rb_bull_sweep", t, stop_lvl)
                        if t >= 5 and c[t] > sma50[t] and sma50[t] > sma50[t - 5]:
                            fire(z, "rb_bull_trend", t, stop_lvl)
                        z.signal, z.status, z.end = t, "RET", t
                        alive.remove(z)

    return pd.DataFrame(sig, index=df.index), pd.DataFrame(stops, index=df.index), zones


def detect_rejection_signals(df: pd.DataFrame) -> pd.DataFrame:
    return find_rejection_blocks(df)[0]


def plot_zones(df: pd.DataFrame, zones: list[Zone], sigs: pd.DataFrame, path, title: str, last: int = 160) -> None:
    """Mum grafiği + RB bölgeleri (yeşil boğa, kırmızı ayı) + ret sinyalleri. PNG kaydeder."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    start = max(0, len(df) - last)
    d = df.iloc[start:]
    x = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(16, 8), dpi=110)
    up = d["Close"] >= d["Open"]
    for xi, (_, r), u in zip(x, d.iterrows(), up):
        col = "#26a69a" if u else "#ef5350"
        ax.vlines(xi, r["Low"], r["High"], color=col, linewidth=0.8)
        ax.add_patch(Rectangle((xi - 0.35, min(r["Open"], r["Close"])), 0.7,
                               max(abs(r["Close"] - r["Open"]), 1e-9), color=col))
    for z in zones:
        if z.pivot < start - 60:
            continue
        x0 = max(z.pivot, start) - start
        x1 = (z.end if z.end is not None else len(df) - 1) - start
        if x1 < 0:
            continue
        col = "#2e7d32" if z.side > 0 else "#c62828"
        ls = "-" if z.status in ("aktif", "temas", "RET", "bekliyor") else "--"
        ax.add_patch(Rectangle((x0, z.bot), max(x1 - x0, 1), z.top - z.bot, facecolor=col,
                               alpha=0.18 if z.status != "geçersiz" else 0.07, edgecolor=col, linestyle=ls))
        if z.pivot >= start:
            ax.annotate("S" if z.sweep else "", (z.pivot - start, z.extreme), color=col, fontsize=8,
                        ha="center", va="bottom" if z.side < 0 else "top")
    for key, mk, col in (("rb_bull_reject", "^", "#1b5e20"), ("rb_bear_reject", "v", "#b71c1c")):
        idx = np.where(sigs[key].to_numpy()[start:])[0]
        if len(idx):
            yy = d["Low"].to_numpy()[idx] * 0.997 if mk == "^" else d["High"].to_numpy()[idx] * 1.003
            ax.scatter(idx, yy, marker=mk, s=90, color=col, zorder=5,
                       label="Boğa RB ret (AL)" if mk == "^" else "Ayı RB ret (SAT)")
    step = max(1, len(d) // 12)
    lo, hi = float(d["Low"].min()), float(d["High"].max())
    pad = (hi - lo) * 0.04
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, len(d) + 1)
    ax.set_xticks(x[::step])
    fmt = "%m-%d %H:%M" if (d.index[-1] - d.index[0]).days < 120 and len(d) > 1 and d.index[1] - d.index[0] < pd.Timedelta("1D") else "%Y-%m-%d"
    ax.set_xticklabels([t.strftime(fmt) for t in d.index[::step]], rotation=30, fontsize=8)
    ax.set_title(title + "   (yeşil = boğa RB, kırmızı = ayı RB, kesikli = geçersiz/eskimiş, S = likidite süpürmeli)")
    ax.grid(alpha=0.2)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
