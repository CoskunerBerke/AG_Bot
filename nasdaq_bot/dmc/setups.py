"""Setup durum makineleri (STRATEGY_SPEC §4 10AM, §7 Lunch).

Her makine LONG mantığıyla yazılır ve 1 dk barlarla, bar kapanışında beslenir. Short taraf, aynı makineye
aynalanmış barlar/seviyeler verilerek çalışır; çıkan niyetler `_unmirror` ile gerçek fiyatlara çevrilir.
Bir makine yalnızca o ana kadar kapanmış barları görür (lookahead yok).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import time as dtime

import pandas as pd

from .data import resample
from .levels import DayLevels
from .structure import FVG, cisd_level, find_fvgs, ote

ONE_MIN = pd.Timedelta(minutes=1)


@dataclass
class Intent:
    setup: str
    tag: str
    side: str                       # long | short
    kind: str                       # limit | market | cancel
    entry: float = 0.0
    stop: float = 0.0
    tp1: float | None = None
    tp2: float = 0.0
    created: pd.Timestamp | None = None     # bu barın kapanışında üretildi (bar başlangıcı + 1 dk)
    valid_until: pd.Timestamp | None = None
    cancel_if_through: float | None = None  # dolmadan bu fiyata ulaşılırsa iptal (kaçan işlem)
    confluence: list[str] = field(default_factory=list)
    targets: dict[str, str] = field(default_factory=dict)  # {'tp1': 'ten_open', 'tp2': 'pdh'} seviye adları

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


def _hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _unmirror(it: Intent) -> Intent:
    neg = lambda x: -x if x is not None else None
    return replace(it, side="short", entry=-it.entry, stop=-it.stop, tp1=neg(it.tp1), tp2=-it.tp2,
                   cancel_if_through=neg(it.cancel_if_through))


def pick_targets(entry: float, stop: float, cands: dict[str, float], minor: set[str],
                 tp1_min_rr: float, tp2_min_rr: float) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
    """Long için yukarıdaki likidite adaylarından TP1 (en yakın ≥ tp1_min_rr) ve TP2 (en yakın büyük likidite
    ≥ tp2_min_rr). minor = TP2 olamayacak iç seviyeler."""
    risk = entry - stop
    if risk <= 0:
        return None, None
    above = sorted(((p, n) for n, p in cands.items() if p is not None and p > entry), key=lambda x: x[0])
    tp1 = next(((n, p) for p, n in above if (p - entry) / risk >= tp1_min_rr), None)
    tp2 = next(((n, p) for p, n in above if n not in minor and (p - entry) / risk >= tp2_min_rr), None)
    return tp1, tp2


class _Base:
    """Ortak: bar kaydı, gün bağlamı. Fiyatlar makinenin kendi (gerekirse aynalı) uzayında."""

    def __init__(self, name: str, cfg: dict, levels: DayLevels, ctx: dict):
        self.name, self.cfg, self.lv, self.ctx = name, cfg, levels, ctx
        self.common = ctx["common"]
        self.rows: list[tuple] = []            # (ts, o, h, l, c, v) 09:30'dan itibaren
        self.phase = "idle"
        self.seq = 0
        self.reasons: set[str] = set()         # tanı: bugün neden işlem açılmadı / ne oldu

    def frame(self, since: pd.Timestamp | None = None) -> pd.DataFrame:
        df = pd.DataFrame(self.rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"]).set_index("ts")
        df.index.name = None
        return df if since is None else df[df.index >= since]

    def _tag(self) -> str:
        self.seq += 1
        return f"{self.name}-{self.side_label}-{self.seq}"

    def buffer(self, price: float) -> float:
        return abs(price) * self.common["stop_buffer_pct"]

    def stop_ok(self, entry: float, stop: float) -> bool:
        pct = (entry - stop) / abs(entry)
        return self.common["min_stop_pct"] <= pct <= self.common["max_stop_pct"]


class TenAMLong(_Base):
    """10AM modeli, manipülasyon dönüşü (long yön = aşağı manipülasyondan 10AM açılışına dönüş).

    seek  → 10:00 sonrası dip ten_open'ın altında ve manipülasyon geçerli (likidite süpürmesi ya da FVG teması)
    armed → 3 dk kapanış CISD seviyesinin üstünde: impulse bacağı (dip → tepe) belli; OTE'den giriş
    done  → işlem dolduruldu / gün bitti
    """

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("ten_am", cfg, levels, ctx)
        self.side_label = side_label
        self.open_t, self.end_t = _hm(cfg["open"]), _hm(cfg["window_end"])
        self.confirm_min = int(cfg.get("confirm_tf", 5))
        self.ten_open = None
        self.ten_ts = None
        self.wickless = False
        self.or_hi = self.or_lo = None
        self.lo = self.lo_ts = None
        self.leg_lo = self.leg_hi = None
        self.cisd = None
        self.closes5: list[float] = []
        self.pending: Intent | None = None
        self.hod = None

    # --------------------------------------------------------------- yardımcılar
    def _manip(self) -> tuple[bool, list[str]]:
        """Manipülasyon bacağının dibi: hangi likiditeyi süpürdü, hangi FVG'ye dokundu.
        [K] Geçerli manipülasyon bir FVG'ye dokunmuş olmalı; [Y] ya da büyük likidite (PDL / premarket / overnight /
        önceki hafta) süpürülmüş olmalı. Sadece 09:30-10:00 dibinin (orl) alınması tek başına yetmez."""
        hits, valid = [], False
        major = set(self.cfg.get("manip_sweep_levels", ["pdl", "pml", "onl", "pwl"]))
        for n, p in {**self.lv.below(), "orl": self.or_lo}.items():
            if p is not None and self.lo < p:
                hits.append(f"sweep:{n}")
                valid |= n in major
        for g in self._fvgs_before(self.lo_ts):
            if g.kind == "bull" and self.lo <= g.top:
                hits.append(f"fvg{g.tf}m")
                valid = True
                break
        return valid, hits

    def _fvgs_before(self, ts: pd.Timestamp) -> list[FVG]:
        """Hazırlıktaki HTF FVG'ler + bugün 09:30'dan ts'ye kadar oluşmuş 5 dk FVG'ler (geçersizleşmemiş)."""
        out = [g for g in self.lv.fvgs]
        today = self.frame()
        b5 = resample(today[today.index < ts], 5)
        b5 = b5[b5.index + pd.Timedelta(minutes=5) <= ts]
        out += [g for g in find_fvgs(b5, 5) if not g.closed_through]
        return out

    def _pd_in_zone(self, zb: float, zt: float) -> bool:
        """Bacak içinde oluşan (1 dk/3 dk) ya da HTF boğa FVG, OTE bölgesiyle kesişiyor mu."""
        leg = self.frame(self.lo_ts)
        for tf in (1, 3):
            bars = resample(leg, tf) if tf > 1 else leg
            for g in find_fvgs(bars, tf):
                if g.kind == "bull" and not g.closed_through and g.contains(zb, zt):
                    return True
        return any(g.kind == "bull" and g.contains(zb, zt) for g in self.lv.fvgs)

    def _plan(self, entry: float, now: pd.Timestamp, kind: str) -> Intent | None:
        stop = self.leg_lo - self.buffer(self.leg_lo)
        if not self.stop_ok(entry, stop):
            return self._no("stop_size")
        lv = self.lv
        cands = {"ten_open": self.ten_open, "orh": self.or_hi, "hod": self.hod, "ih": self.leg_hi, **lv.above()}
        tp1, tp2 = pick_targets(entry, stop, cands, {"ih"}, self.common["tp1_min_rr"], self.common["min_rr_tp2"])
        if tp2 is None:
            return self._no("no_tp2_rr")
        z = ote(self.leg_lo, self.leg_hi, **self.ctx["ote"])
        # [K] zorunlu üçlü: 10AM çizgisi (hedef/DOL) + OTE + OTE'de bir PD array; ayrıca geçerli manipülasyon
        if not self.ten_open > entry:
            return self._no("ten_open_not_above")
        if not self._pd_in_zone(z["zone_bottom"], z["zone_top"]):
            return self._no("no_pd_in_ote")
        valid, hits = self._manip()
        if not valid:
            return self._no("manip_invalid")
        # [K] "bias'ı arkamıza almak zorundayız": bias'a ters 10AM işlemi yok (neutral günde manipülasyon tersine serbest)
        if self.cfg.get("require_bias", True) and lv.bias == "short":
            return self._no("bias_against")
        # Her bağımsız sebep bir kez sayılır (aynı dibin süpürdüğü birden çok seviye tek confluence)
        conf = ["ten_open_draw", "ote", "pd_in_ote"]
        if any(x.startswith("sweep:") and x != "sweep:orl" for x in hits):
            conf.append("liquidity_sweep")
        if any(x.startswith("fvg") for x in hits):
            conf.append("manip_fvg_tap")
        if lv.bias == "long":
            conf.append("bias")
        if self.wickless:
            conf.append("wickless_open")
        if len(conf) < self.ctx["min_confluence"]:
            return self._no("confluence")
        mode = self.cfg.get("entry_mode", "auto")     # auto: ≥limit_min_confluence → limit, değilse onaylı
        if kind == "limit" and (mode == "confirm" or (mode == "auto" and len(conf) < self.ctx["limit_min_confluence"])):
            return None
        conf += [f"({x})" for x in hits]              # ayrıntı (sayıma girmez)
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        end = pd.Timestamp.combine(now.date(), self.end_t)
        return Intent("ten_am", self.pending.tag if self.pending else self._tag(), "long", kind, entry, stop,
                      tp1[1] if tp1 else None, tp2[1], now, end,
                      cancel_if_through=(tp1 or tp2)[1], confluence=conf,
                      targets={"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})

    # --------------------------------------------------------------- ana döngü
    def on_bar(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float, v: float) -> list[Intent]:
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        self.hod = h if self.hod is None else max(self.hod, h)
        now = ts + ONE_MIN
        if t < self.open_t:
            self.or_hi = h if self.or_hi is None else max(self.or_hi, h)
            self.or_lo = l if self.or_lo is None else min(self.or_lo, l)
            return []
        if self.ten_open is None:
            self.ten_open, self.ten_ts = o, ts
            self.wickless = (h - o) <= self.buffer(o) and c < o
            self.phase = "seek"
        if self.phase in ("done", "dead"):
            return []
        # kararsızlık: 5 dk kapanışlar ten_open'ın iki yanına defalarca geçiyorsa günü bırak
        if (now - self.ten_ts) % pd.Timedelta(minutes=5) == pd.Timedelta(0):
            self.closes5.append(c)
            sides = [x > self.ten_open for x in self.closes5]
            flips = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
            if flips >= self.cfg.get("chop_flips", 3):
                return self._kill()
        out: list[Intent] = []
        if self.lo is None or l < self.lo:
            self.lo, self.lo_ts = l, ts
            if self.phase == "armed":           # bacak bozuldu → yeniden ara
                self.phase, self.leg_lo, self.leg_hi = "seek", None, None
                if self.pending:
                    out.append(replace(self.pending, kind="cancel"))
                    self.pending = None
        if t >= self.end_t:
            return out + self._kill()
        if now < self.ten_ts + pd.Timedelta(minutes=self.confirm_min):
            return out
        if self.phase == "seek":
            deep = self.lo <= self.ten_open - abs(self.ten_open) * self.cfg.get("min_excursion_pct", 0.0005)
            on3 = (now - self.ten_ts) % pd.Timedelta(minutes=3) == pd.Timedelta(0)
            if deep and on3:
                self.cisd = cisd_level(self.frame(self.ten_ts), self.lo_ts)
                if self.cisd is not None and c > self.cisd and self._manip()[0]:
                    self.phase = "armed"
                    self.leg_lo = self.lo
                    self.leg_hi = float(self.frame(self.lo_ts)["High"].max())
            if self.phase != "armed":
                return out
        # armed
        if h > self.leg_hi:
            self.leg_hi = h
        z = ote(self.leg_lo, self.leg_hi, **self.ctx["ote"])
        plan = self._plan(round(z["entry"], 2), now, "limit")
        if plan is not None:
            if (self.pending is None or self.pending.kind != "limit" or abs(self.pending.entry - plan.entry) > 1e-9
                    or self.pending.tp2 != plan.tp2 or self.pending.tp1 != plan.tp1):
                self.pending = plan
                out.append(plan)
            return out
        if self.pending is not None and self.pending.kind == "limit":   # plan artık geçersiz → bekleyen limiti iptal
            out.append(replace(self.pending, kind="cancel"))
            self.pending = None
        # onaylı giriş: 3 dk mum OTE bölgesine girip boğa kapattı
        on3 = (now - self.ten_ts) % pd.Timedelta(minutes=3) == pd.Timedelta(0)
        if on3 and self.pending is None:
            b3 = self.frame(now - pd.Timedelta(minutes=3))
            if len(b3) and b3["Low"].min() <= z["zone_top"] and c > b3["Open"].iloc[0] and c >= z["zone_bottom"]:
                plan = self._plan(c, now, "market")
                if plan is not None:
                    self.pending = plan
                    out.append(plan)
        return out

    def _no(self, reason: str) -> None:
        self.reasons.add(reason)
        return None

    def _kill(self) -> list[Intent]:
        self.phase = "dead"
        if self.pending:
            p, self.pending = self.pending, None
            return [replace(p, kind="cancel")]
        return []

    def filled(self):
        self.phase, self.pending = "done", None


class LunchLong(_Base):
    """NY Lunch reversal (long yön = lunch low'un 5 dk iğneyle süpürülüp geri kapatılması)."""

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("lunch", cfg, levels, ctx)
        self.side_label = side_label
        self.rs, self.re, self.we = _hm(cfg["range_start"]), _hm(cfg["range_end"]), _hm(cfg["window_end"])
        self.sweep_tf = int(cfg.get("sweep_tf", 5))
        self.am_hi = self.am_lo = self.l_hi = self.l_lo = None
        self.hod = None
        self.phase = "range"

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        self.hod = h if self.hod is None else max(self.hod, h)
        if t < self.rs:
            self.am_hi = h if self.am_hi is None else max(self.am_hi, h)
            self.am_lo = l if self.am_lo is None else min(self.am_lo, l)
            return []
        if t < self.re:
            self.l_hi = h if self.l_hi is None else max(self.l_hi, h)
            self.l_lo = l if self.l_lo is None else min(self.l_lo, l)
            return []
        if self.phase in ("done", "dead") or self.l_lo is None:
            return []
        now = ts + ONE_MIN
        if t >= self.we:
            self.phase = "dead"
            return []
        start = pd.Timestamp.combine(ts.date(), self.re)
        if (now - start) % pd.Timedelta(minutes=self.sweep_tf) != pd.Timedelta(0):
            return []
        b = self.frame(now - pd.Timedelta(minutes=self.sweep_tf))
        bl, bc = float(b["Low"].min()), c
        if not (bl < self.l_lo < bc):
            return []
        self.reasons.add("sweep_seen")
        bias = self.lv.bias
        if self.cfg.get("require_bias", True) and bias == "short":
            self.reasons.add("bias_against")
            return []
        if self._guarded(bl, now):
            self.reasons.add("htf_guard")
            return []
        entry, stop = bc, bl - self.buffer(bl)
        if not self.stop_ok(entry, stop):
            self.reasons.add("stop_size")
            return []
        cands = {"lunch_mid": (self.l_hi + self.l_lo) / 2, "lunch_hi": self.l_hi, "am_hi": self.am_hi,
                 "hod": self.hod, **self.lv.above()}
        tp1, tp2 = pick_targets(entry, stop, cands, {"lunch_mid"}, self.common["tp1_min_rr"], self.common["min_rr_tp2"])
        if tp2 is None:
            self.reasons.add("no_tp2_rr")
            return []
        conf = ["lunch_5m_sweep"]
        if bias == "long":
            conf.append("bias")
        if self.am_lo is not None and bl < self.am_lo < bc:
            conf.append("am_sweep")
        med = self.ctx.get("am_range_median")
        if med and self.am_hi is not None and (self.am_hi - self.am_lo) < self.cfg.get("am_accum_ratio", 0.6) * med:
            conf.append("am_accumulation")
        if not any(g.kind == "bear" and entry < g.bottom < tp2[1] for g in self.lv.fvgs):
            conf.append("clean_target")
        if len(conf) < self.ctx["min_confluence"]:
            self.reasons.add("confluence")
            return []
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        self.phase = "done"
        return [Intent("lunch", self._tag(), "long", "market", entry, stop, tp1[1] if tp1 else None, tp2[1],
                       now, now + pd.Timedelta(minutes=2), None, conf,
                       {"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})]

    def _guarded(self, sweep_low: float, now) -> bool:
        """Süpürmenin hemen altında dokunulmamış bir HTF boğa FVG varsa fiyat oraya çekilebilir → pas (§7)."""
        g_pct = self.cfg.get("htf_guard_pct", 0.0015)
        today = self.frame()
        b15 = resample(today, 15)
        b15 = b15[b15.index + pd.Timedelta(minutes=15) <= now]
        fvgs = self.lv.fvgs + [g for g in find_fvgs(b15, 15) if not g.closed_through]
        for g in fvgs:
            if g.kind == "bull" and not g.tapped and g.top < sweep_low and (sweep_low - g.top) / abs(sweep_low) <= g_pct:
                return True
        return False

    def filled(self):
        self.phase = "done"


class SidePair:
    """Bir setup'ın long ve (aynalanmış) short makinesi."""

    def __init__(self, cls, cfg, levels: DayLevels, ctx: dict):
        self.name = cls.__name__
        self.long = cls(cfg, levels, ctx, "long")
        self.short = cls(cfg, levels.mirrored(), ctx, "short")
        self.setup = self.long.name

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        out = self.long.on_bar(ts, o, h, l, c, v)
        out += [_unmirror(i) for i in self.short.on_bar(ts, -o, -l, -h, -c, v)]
        return out

    def filled(self, intent: Intent):
        self.long.filled()
        self.short.filled()
