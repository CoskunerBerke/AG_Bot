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
from .structure import FVG, RB, cisd_level, find_fvgs, find_rbs, ote, swing_points

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

    def pts(self, x: float, price: float) -> float:
        """Kaan'ın NQ puanını bu enstrümana çevirir: vadelide birebir; diğerlerinde 1 dk mum boyu oranıyla
        (NQ medyan 1 dk mum ≈ nq_atr1_ref puan). ATR yoksa fiyat oranı."""
        if self.ctx.get("futures"):
            return x
        if self.lv.atr1:
            return x * self.lv.atr1 / float(self.ctx.get("nq_atr1_ref") or 13.0)
        return x * abs(price) / 30000.0

    def buffer(self, price: float) -> float:
        return abs(price) * self.common["stop_buffer_pct"]

    def stop_ok(self, entry: float, stop: float) -> bool:
        pct = (entry - stop) / abs(entry) / self.ctx.get("vol_mult", 1)   # oynak fonda (TQQQ, SLV) stop % geniş
        return self.common["min_stop_pct"] <= pct <= self.common["max_stop_pct"]


class TenAMLong(_Base):
    """10AM modeli (knowledge/notes/10am_model_kareler.md, transcript1/2). Long yön; short aynalanarak çalışır.

    Ortak: 10AM çizgisi = 10:00 1 dk mumunun açılışı; 09:30-10:00 işlem yok; ilk 5 dk kapanışı beklenir;
    manipülasyon (çizginin altındaki bacak) geçerli olmalı: bir FVG'ye dokunmuş ya da büyük likidite süpürmüş.
    Stop: yapının `pad_pts` ötesi, en az `min_stop_pts`, en fazla `max_stop_pts` NQ puanı (uç noktaya stop yok).
    TP1: en yakın güvenli likidite (>= tp1_min_rr); TP2: karşı likidite (>= min_rr_tp2, videoda 4-9R).

    retest (A+) : 1 dk GÖVDE çizginin üstünde kapanır (bacakta boğa FVG = displacement) ve çizgi kırılım bacağının
                  OTE bandında (0.5-0.88) kalır → çizgiye LİMİT. Gövde tekrar altında kapanırsa iptal (yön değişimi).
    ote_pda     : dipten CISD dönüşü (3 dk kapanış) → dönüş bacağının OTE bölgesinde (0.62-0.79) boğa FVG/RB →
                  PDA'nın yakın kenarına giriş; hedef 10AM çizgisi ve ötesi.
    """

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("ten_am", cfg, levels, ctx)
        self.side_label = side_label
        self.open_t, self.end_t = _hm(cfg["open"]), _hm(cfg["window_end"])
        self.confirm_min = int(cfg.get("confirm_tf", 5))
        self.nq = float(cfg.get("nq_ref", 30000))
        self.min_stop_pts = float(cfg.get("min_stop_pts", 10))
        self.max_stop_pts = float(cfg.get("max_stop_pts", 25))
        self.pad_pts = float(cfg.get("pad_pts", 2))
        self.min_rr = float(cfg.get("min_rr_tp2", 4.0))
        self.ten_open = self.ten_ts = None
        self.wickless = False
        self.or_hi = self.or_lo = None
        self.lo = self.lo_ts = None
        self.leg_lo = self.leg_hi = None
        self.cisd = None
        self.brk_ts = self.brk_hi = None
        self.closes5: list[float] = []
        self.pending: Intent | None = None
        self.hod = None

    # --------------------------------------------------------------- yardımcılar
    def _pts(self, x: float) -> float:
        """x NQ puanının bu enstrümandaki fiyat karşılığı."""
        return self.pts(x, self.ten_open)

    def _manip(self) -> tuple[bool, list[str]]:
        hits, valid = [], False
        if self.cfg.get("stdv_valid"):                  # [K] manipülasyon bacağının STDV -1/-2/-2.5 seviyesine ulaşması
            first = self.frame(self.ten_ts)
            first = first[first.index < self.ten_ts + pd.Timedelta(minutes=5)]
            if len(first) == 5:
                from .structure import stdv_levels
                for m, x in stdv_levels(self.ten_open, float(first["Low"].min()), (-1.0, -2.0, -2.5)).items():
                    if abs(self.lo - x) <= self._pts(3):
                        hits.append(f"stdv{m}")
                        valid = True
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
        out = [g for g in self.lv.fvgs]
        today = self.frame()
        b5 = resample(today[today.index < ts], 5)
        b5 = b5[b5.index + pd.Timedelta(minutes=5) <= ts]
        out += [g for g in find_fvgs(b5, 5) if not g.closed_through]
        return out

    def _leg_pdas(self, since: pd.Timestamp) -> list[tuple[str, float, float]]:
        """Bacak içindeki (1/3 dk) geçerli boğa FVG ve RB'ler: (ad, alt, üst)."""
        leg = self.frame(since)
        out = []
        for tf in (1, 3):
            bars = resample(leg, tf) if tf > 1 else leg
            out += [(f"fvg{tf}m", g.bottom, g.top) for g in find_fvgs(bars, tf) if g.kind == "bull" and not g.closed_through]
            out += [(f"rb{tf}m", r.lo, r.hi) for r in find_rbs(bars, tf) if r.kind == "bull" and not r.invalid]
        if self.cfg.get("msnr_pda"):                    # [K] fresh MSNR desteği de PDA (±2 puan)
            out += [(f"msnr{tf}", p - self._pts(2), p + self._pts(2)) for p, role, tf in self.lv.msnr if role == "sup"]
        return out

    def _stop(self, entry: float, structure: list[float]) -> float | None:
        """Girişin altındaki en yakın yapının pad ötesi; en az min, en fazla max puan. Uygun değilse None."""
        pad, mn, mx = self._pts(self.pad_pts), self._pts(self.min_stop_pts), self._pts(self.max_stop_pts)
        for lvl in sorted((x for x in structure if x < entry), reverse=True):
            if entry - (lvl - pad) >= mn:
                stop = lvl - pad
                break
        else:
            stop = entry - mn
        return stop if entry - stop <= mx else None

    def _targets(self, entry, stop, internal):
        cands = {"ten_open": self.ten_open, "orh": self.or_hi, "hod": self.hod, "ih": internal, **self.lv.above()}
        if self.cfg.get("htf_swing_tp"):
            cands.update(self.lv.htf_above())
        return pick_targets(entry, stop, cands, {"ih"}, self.common["tp1_min_rr"], self.min_rr)

    def _bias_ok(self) -> bool:
        mode = self.cfg.get("require_bias", "not_against")
        if mode is True:
            mode = "not_against"
        return (mode in (False, "off")) or (mode == "aligned" and self.lv.bias == "long") or \
            (mode == "not_against" and self.lv.bias != "short")

    def _emit(self, plan: Intent | None, out: list) -> None:
        if plan is None:
            return
        p = self.pending
        if p is None or p.kind != plan.kind or abs(p.entry - plan.entry) > 1e-9 or p.stop != plan.stop \
                or p.tp1 != plan.tp1 or p.tp2 != plan.tp2:
            if p is not None and p.tag != plan.tag:
                out.append(replace(p, kind="cancel"))
            self.pending = plan
            out.append(plan)

    def _cancel(self, out: list, why: str) -> None:
        if self.pending is not None:
            out.append(replace(self.pending, kind="cancel"))
            self.pending = None
            self.reasons.add(why)

    def _intent(self, mode, kind, entry, stop, tp1, tp2, now, conf) -> Intent:
        tag = self.pending.tag if (self.pending and self.pending.tag.startswith(f"ten_am-{self.side_label}-{mode}")) \
            else f"ten_am-{self.side_label}-{mode}-{self._next()}"
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        return Intent("ten_am", tag, "long", kind, entry, stop, tp1[1] if tp1 else None, tp2[1], now,
                      pd.Timestamp.combine(now.date(), self.end_t), cancel_if_through=(tp1 or tp2)[1],
                      confluence=conf, targets={"tp1": tp1[0] if tp1 else "", "tp2": tp2[0], "mode": mode})

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def _extras(self, hits) -> list[str]:
        conf = [f"({x})" for x in hits]
        if self.lv.bias == "long":
            conf.append("bias")
        if self.wickless:
            conf.append("wickless_open")
        return conf

    # --------------------------------------------------------------- modlar
    def _plan_retest(self, now, hits) -> Intent | None:
        line, lo, hi = self.ten_open, self.lo, self.brk_hi
        r = hi - lo
        if r <= 0 or not (hi - 0.88 * r <= line <= hi - 0.5 * r):
            return self._no("retest_line_not_in_ote")
        pdas = [x for x in self._leg_pdas(self.lo_ts) if x[1] <= line + self._pts(2) and x[2] >= line - self._pts(2)]
        entry = round(line, 2)
        stop = self._stop(entry, [x[1] for x in pdas] + [hi - 0.79 * r])
        if stop is None:
            return self._no("stop_size")
        tp1, tp2 = self._targets(entry, stop, hi)
        if tp2 is None:
            return self._no("no_tp2_rr")
        conf = ["ten_open_retest", "ote", "body_break"] + (["pda_at_line"] if pdas else []) + self._extras(hits)
        return self._intent("retest", "limit", entry, stop, tp1, tp2, now, conf)

    def _plan_ote_pda(self, now, hits) -> Intent | None:
        z = ote(self.leg_lo, self.leg_hi, **self.ctx["ote"])
        pdas = [x for x in self._leg_pdas(self.lo_ts) if x[1] <= z["zone_top"] and x[2] >= z["zone_bottom"]]
        if not pdas:
            return self._no("no_pd_in_ote")
        name, pb, pt = max(pdas, key=lambda x: x[2])          # fiyatın ilk değeceği (en üstteki) PDA
        entry = round(min(pt, z["zone_top"]), 2)
        if not self.ten_open > entry:
            return self._no("ten_open_not_above")
        stop = self._stop(entry, [pb, z["zone_bottom"]])
        if stop is None:
            return self._no("stop_size")
        tp1, tp2 = self._targets(entry, stop, self.leg_hi)
        if tp2 is None:
            return self._no("no_tp2_rr")
        conf = ["ten_open_draw", "ote", f"pda:{name}"] + self._extras(hits)
        kind = "market" if self.cfg.get("entry_mode", "limit") == "confirm" else "limit"
        if kind == "market":                                  # onay: 3 dk mum PDA'ya değip boğa kapattı
            on3 = (now - self.ten_ts) % pd.Timedelta(minutes=3) == pd.Timedelta(0)
            b3 = self.frame(now - pd.Timedelta(minutes=3))
            if not (on3 and len(b3) and b3["Low"].min() <= pt and b3["Close"].iloc[-1] > b3["Open"].iloc[0]
                    and b3["Close"].iloc[-1] > pb):
                return None
            entry = round(float(b3["Close"].iloc[-1]), 2)
            stop = self._stop(entry, [pb, z["zone_bottom"]])
            if stop is None:
                return self._no("stop_size")
            tp1, tp2 = self._targets(entry, stop, self.leg_hi)
            if tp2 is None:
                return self._no("no_tp2_rr")
        return self._intent("ote", kind, entry, stop, tp1, tp2, now, conf)

    # --------------------------------------------------------------- ana döngü
    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
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
            self.wickless = (h - o) <= self._pts(1) and c < o
            self.phase = "seek"
        if self.phase in ("done", "dead"):
            return []
        out: list[Intent] = []
        if (now - self.ten_ts) % pd.Timedelta(minutes=5) == pd.Timedelta(0):
            self.closes5.append(c)
            sides = [x > self.ten_open for x in self.closes5]
            if sum(1 for a, b in zip(sides, sides[1:]) if a != b) >= self.cfg.get("chop_flips", 3):
                return self._kill()
        if self.lo is None or l < self.lo:                    # yeni dip: dönüş bacakları geçersiz
            self.lo, self.lo_ts = l, ts
            self.phase, self.leg_lo, self.leg_hi, self.brk_ts, self.brk_hi = "seek", None, None, None, None
            self._cancel(out, "new_low")
        if t >= self.end_t:
            return out + self._kill()
        if now < self.ten_ts + pd.Timedelta(minutes=self.confirm_min):
            return out
        line = self.ten_open
        if not self.lo <= line - self._pts(self.cfg.get("min_excursion_pts", 5)):
            return out
        valid, hits = self._manip()
        if not valid:
            self.reasons.add("manip_invalid")
            return out
        if not self._bias_ok():
            self.reasons.add("bias_against")
            return out
        prev_c = self.rows[-2][4] if len(self.rows) > 1 else o
        # ---- A+ retest: gövde kırılımı → çizgiye limit
        if self.brk_ts is None and prev_c <= line < c:
            if any(n.startswith("fvg1m") for n, _, _ in self._leg_pdas(self.lo_ts)):
                self.brk_ts, self.brk_hi = ts, h
            else:
                self.reasons.add("break_without_displacement")
        elif self.brk_ts is not None:
            if c < line:                                        # gövde tekrar altında: kırılım bozuldu
                self.brk_ts = self.brk_hi = None
                if self.pending and "-retest-" in self.pending.tag:
                    self._cancel(out, "break_failed")
            else:
                self.brk_hi = max(self.brk_hi, h)
        if self.brk_ts is not None:
            plan = self._plan_retest(now, hits)
            if plan is not None:
                self._emit(plan, out)
                return out
            if self.pending and "-retest-" in self.pending.tag:
                self._cancel(out, "retest_invalid")
        # ---- OTE + PDA: CISD dönüşü
        on3 = (now - self.ten_ts) % pd.Timedelta(minutes=3) == pd.Timedelta(0)
        if self.phase == "seek" and on3:
            self.cisd = cisd_level(self.frame(self.ten_ts), self.lo_ts)
            if self.cisd is not None and c > self.cisd:
                self.phase = "armed"
                self.leg_lo, self.leg_hi = self.lo, float(self.frame(self.lo_ts)["High"].max())
        if self.phase == "armed":
            self.leg_hi = max(self.leg_hi, h)
            plan = self._plan_ote_pda(now, hits)
            if plan is not None:
                self._emit(plan, out)
            elif self.pending and "-ote-" in self.pending.tag and self.pending.kind == "limit":
                self._cancel(out, "ote_invalid")
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
    """NY Lunch reversal (knowledge/notes/lunch_kareler.md). Long yön = süpürülen DİP.

    * Lunch = 12:00-12:59 range; NY AM = 09:30-10:59 (Kaan'ın killzone göstergesi)
    * 13:00-13:25 arası açılan bir 5 dk mum lunch low'un (ya da NY AM low'un) altına iğne atıp üstünde kapatır
    * giriş: bir sonraki 5 dk mumun açılışı (market); stop: iğne ucunun 1 tick ötesi
    * TP1: lunch range içindeki ilk dolmamış 5 dk FVG (yoksa range ortası); TP2: range'in karşı ucu, R yetmezse NY AM tepesi
    * filtre: sadece HTF bias yönü; süpürülen seviyenin hemen ötesinde dokunulmamış 15/30/45 dk FVG varsa ve iğne
      onun içine girmediyse pas (fiyat önce oraya çekilir)
    """

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("lunch", cfg, levels, ctx)
        self.side_label = side_label
        self.rs, self.re, self.we = _hm(cfg["range_start"]), _hm(cfg["range_end"]), _hm(cfg["window_end"])
        self.as_, self.ae = _hm(cfg.get("am_start", "09:30")), _hm(cfg.get("am_end", "11:00"))
        self.sweep_tf = int(cfg.get("sweep_tf", 5))
        self.tick = float(cfg.get("tick", 0.01)) * int(cfg.get("stop_buffer_ticks", 1))
        self.am_hi = self.am_lo = self.l_hi = self.l_lo = None
        self.phase = "range"

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        if self.as_ <= t < self.ae:
            self.am_hi = h if self.am_hi is None else max(self.am_hi, h)
            self.am_lo = l if self.am_lo is None else min(self.am_lo, l)
        if self.rs <= t < self.re:
            self.l_hi = h if self.l_hi is None else max(self.l_hi, h)
            self.l_lo = l if self.l_lo is None else min(self.l_lo, l)
            return []
        if t < self.re or self.phase in ("done", "dead") or self.l_lo is None:
            return []
        now = ts + ONE_MIN
        if t >= self.we:
            self.phase = "dead"
            return []
        a = getattr(self, "armed", None)
        if a is not None:                              # MSS bekleniyor: 1 dk kapanış süpürme mumunun tepesinin üstünde
            if l < a["low"] or now > a["until"]:
                self.armed = None                      # daha derin dip ya da süre doldu → yeni süpürme aranır
            elif c > a["trigger"]:
                self.armed = None
                out = self._enter(c, a["stop"], a["swept"], a["smt"], a["from_fvg"], now)
                if out:
                    out[0].confluence.append("mss")
                return out
            else:
                return []
        start = pd.Timestamp.combine(ts.date(), self.re)
        if (now - start) % pd.Timedelta(minutes=self.sweep_tf) != pd.Timedelta(0):
            return []
        b = self.frame(now - pd.Timedelta(minutes=self.sweep_tf))
        bl, bc, bo = float(b["Low"].min()), c, float(b["Open"].iloc[0])
        self._sweep_hi = float(b["High"].max())
        levels = {"lunch_low": self.l_lo, "am_low": self.am_lo}
        swept = [n for n, p in levels.items() if p is not None and bl < p < bc]
        if not swept:
            return []
        if self.cfg.get("require_close_against", False) and not bc > bo:
            return []
        self.reasons.add("sweep_seen")
        smt = self._smt(now)
        smt_mode = self.cfg.get("smt", "off")             # off | confluence | require
        if smt_mode == "require" and smt is False:
            self.reasons.add("no_smt")
            return []
        bias, mode = self.lv.bias, self.cfg.get("require_bias", "aligned")
        if (mode == "aligned" and bias != "long") or (mode == "not_against" and bias == "short"):
            self.reasons.add("bias_" + bias)
            return []
        veto, from_fvg = self._htf_fvg(min(levels[n] for n in swept), bl, now)
        if veto:
            self.reasons.add("htf_fvg_veto")
            return []
        entry, stop = bc, bl - self.tick
        # [K] iğnenin hemen ötesinde önemli likidite varsa stop onun ötesine (yoksa iğne ucu)
        near = [x for x in list(self.lv.below().values()) + [self.am_lo]
                if x is not None and x < bl and bl - x <= self.pts(float(self.cfg.get("liq_stop_pts", 10)), bl)]
        if near:
            stop = min(near) - self.tick
        if self.cfg.get("mss_confirm"):                # [K] canlı NQ işlemi: iğneden sonra MSS'i bekle, MSS mumunda gir
            self.armed = {"trigger": self._sweep_hi, "low": bl, "stop": stop, "swept": swept, "smt": smt,
                          "from_fvg": from_fvg, "until": now + pd.Timedelta(minutes=int(self.cfg.get("mss_wait_min", 15)))}
            self.reasons.add("armed_mss")
            return []
        return self._enter(entry, stop, swept, smt, from_fvg, now)

    def _enter(self, entry, stop, swept, smt, from_fvg, now):
        if not self.stop_ok(entry, stop):
            self.reasons.add("stop_size")
            return []
        risk = entry - stop
        tp1 = self._first_fvg_inside(entry, now)
        if tp1 is None:
            mid = (self.l_hi + self.l_lo) / 2
            tp1 = ("lunch_mid", mid) if mid > entry else None
        min_rr = float(self.cfg.get("min_rr_tp2", 1.0))
        tp2 = next(((n, p) for n, p in (("lunch_hi", self.l_hi), ("am_hi", self.am_hi))
                    if p is not None and (p - entry) / risk >= min_rr), None)
        if tp2 is None:
            self.reasons.add("no_tp2_rr")
            return []
        if tp1 is not None and (tp1[1] >= tp2[1] or (tp1[1] - entry) / risk < self.common["tp1_min_rr"]):
            tp1 = None
        conf = ["lunch_5m_sweep:" + "+".join(swept), "bias"] + (["smt"] if smt else [])
        if from_fvg:
            conf.append("sweep_from_htf_fvg")
        if self.am_hi is not None and self.ctx.get("am_range_median") and                 (self.am_hi - self.am_lo) < self.cfg.get("am_accum_ratio", 0.6) * self.ctx["am_range_median"]:
            conf.append("am_accumulation")
        self.phase = "done"
        return [Intent("lunch", self._tag(), "long", "market", entry, stop, tp1[1] if tp1 else None, tp2[1],
                       now, now + pd.Timedelta(minutes=2), None, conf,
                       {"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})]

    def _htf_fvg(self, level: float, wick: float, now) -> tuple[bool, bool]:
        """(veto, iğne_FVG_içinden_mi). Süpürülen seviyenin altında `htf_guard_pct` içinde dokunulmamış 15/30/45 dk
        boğa FVG varsa: iğne o FVG'ye girdiyse geçerli (içinden süpürme), girmediyse veto."""
        g_pct = self.cfg.get("htf_guard_pct", 0.0015)
        today = self.frame()
        fvgs = [g for g in self.lv.fvgs if g.kind == "bull" and not g.closed_through]
        for tf in (15, 30, 45):
            bt = resample(today, tf)
            bt = bt[bt.index + pd.Timedelta(minutes=tf) <= now]
            fvgs += [g for g in find_fvgs(bt, tf) if g.kind == "bull" and not g.closed_through]
        from_fvg = False
        for g in fvgs:
            if g.top < level and (level - g.top) / abs(level) <= g_pct:
                if wick <= g.top:
                    from_fvg = True
                elif not g.tapped:
                    return True, False
        return False, from_fvg

    def _smt(self, now) -> bool | None:
        """[K] SMT: bu enstrüman lunch low'u süpürdü ama eşi (NQ↔ES, QQQ↔SPY) kendi lunch low'unu süpürmedi.
        Eş verisi yoksa None."""
        peer = self.ctx.get("peer")
        if peer is None or not len(peer):
            return None
        if self.side_label == "short":
            from .structure import mirror
            peer = mirror(peer)
        d = now.date()
        p = peer[peer.index.date == d]
        lunch = p[(p.index >= pd.Timestamp.combine(d, self.rs)) & (p.index < pd.Timestamp.combine(d, self.re))]
        win = p[(p.index >= now - pd.Timedelta(minutes=self.sweep_tf)) & (p.index < now)]
        if not len(lunch) or not len(win):
            return None
        return bool(win["Low"].min() >= lunch["Low"].min())

    def _first_fvg_inside(self, entry: float, now) -> tuple[str, float] | None:
        """Lunch range içinde, girişin üstündeki ilk dolmamış 5 dk FVG'nin alt kenarı (fiyatın ilk çekileceği yer)."""
        rng = self.frame(pd.Timestamp.combine(now.date(), self.rs))
        b5 = resample(rng, 5)
        b5 = b5[b5.index + pd.Timedelta(minutes=5) <= now]
        cands = [g.bottom for g in find_fvgs(b5, 5)
                 if not g.closed_through and entry < g.bottom <= self.l_hi]
        return ("lunch_fvg5", min(cands)) if cands else None

    def filled(self):
        self.phase = "done"


class RBLong(_Base):
    """DMC Rejection Block girişi (knowledge/notes/rb_backtest_kareler.md, transcript1 25 Ağu / 4 Eyl).

    * RB'ler 15/5/3 dk'da (ve hazırlıktaki 60/15 dk RB'ler): likidite süpürmüş swing mumunun gövde ucu -> fitil ucu
    * 'önemli RB': süpürme + bias yönü + bir gün seviyesi (PDL/PML/ONL/OR low) ya da HTF FVG ile çakışma
    * giriş: henüz test edilmemiş RB'nin gövde ucuna LİMİT (retest'te dolar)
    * stop: max(10 NQ puanı karşılığı, fitil ucu + 0.25 puan)  [çıkarım: rb_backtest_kareler kural 4]
    * TP1: girişin üstündeki ilk 5 dk swing high; TP2: karşı taraftaki ilk ayı RB/FVG'nin hemen önü (>= min_rr_tp2)
    """

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("dmc_rb", cfg, levels, ctx)
        self.side_label = side_label
        self.ws, self.we = _hm(cfg.get("window_start", "09:30")), _hm(cfg.get("window_end", "14:00"))
        self.tfs = [int(x) for x in cfg.get("tfs", [15, 5, 3])]
        self.min_stop_pts = float(cfg.get("min_stop_pts", 10))
        self.wick_pad_pts = float(cfg.get("wick_pad_pts", 0.25))
        self.pending = None
        self.used = set()
        self.or_lo = None
        self.o930 = self.o10 = None

    def _pool(self, now):
        """Bugüne kadar oluşmuş, geçersizleşmemiş tüm boğa RB'ler (test edilmiş olanlar dahil)."""
        today = self.frame()
        out = [r for r in self.lv.rbs if r.kind == "bull"]
        tfs = self.tfs + ([1] if self.cfg.get("ltf_in_rb") else [])
        for tf in tfs:
            bt = resample(today, tf)
            bt = bt[bt.index + pd.Timedelta(minutes=tf) <= now]
            out += [r for r in find_rbs(bt, tf, min_wick_frac=float(self.cfg.get("min_wick_frac", 0.0)))
                    if r.kind == "bull"]
        return [r for r in out if not r.invalid and r.known_at <= now]

    def _rbs(self, now):
        return [r for r in self._pool(now) if (r.swept or r.tf == 1) and r.touches == 0]

    def _important(self, r):
        why = []
        lv = {**self.lv.below(), "orl": self.or_lo}
        if self.cfg.get("liq_opens"):                   # [K] açılışlar likiditedir: 18:00, 00:00, 09:30, 10:00
            lv.update({"tdo": self.lv.tdo, "mid_open": self.lv.mid_open, "o930": self.o930, "o10": self.o10})
        for n, p in lv.items():
            if p is not None and r.wick < p <= r.hi + (r.hi - r.lo):
                why.append(f"swept:{n}")
        if any(g.kind == "bull" and not g.closed_through and g.contains(r.lo, r.hi) for g in self.lv.fvgs):
            why.append("htf_fvg")
        if self.cfg.get("msnr_conf"):                   # [K] RB kırılmış destek/direnç (MSNR) seviyesine denk
            tol = r.hi - r.lo
            if any(role == "sup" and r.lo - tol <= p <= r.hi + tol for p, role, _ in self.lv.msnr):
                why.append("msnr")
        return why

    def on_bar(self, ts, o, h, l, c, v):
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        if t < dtime(10, 0):
            self.or_lo = l if self.or_lo is None else min(self.or_lo, l)
        if t == dtime(9, 30):
            self.o930 = o
        elif t == dtime(10, 0):
            self.o10 = o
        now = ts + ONE_MIN
        if self.phase == "done" or t < self.ws:
            return []
        if t >= self.we:
            self.phase = "done"
            if self.pending:
                p, self.pending = self.pending, None
                return [replace(p, kind="cancel")]
            return []
        if self.cfg.get("require_bias", "aligned") == "aligned" and self.lv.bias != "long":
            self.reasons.add("bias_" + self.lv.bias)
            return []
        if self.pending is not None:
            return []
        best = None
        rbs = self._rbs(now)
        htf_rule = self.cfg.get("htf_rule", "off")         # off | require: LTF RB bir HTF RB'nin içinde olmalı
        htf = [x for x in rbs if x.tf >= 30]
        if htf_rule != "off":
            f = self.frame()
            b30 = resample(f, 30)
            b30 = b30[b30.index + pd.Timedelta(minutes=30) <= now]
            htf += [x for x in find_rbs(b30, 30) if x.kind == "bull" and not x.invalid]
        outer = [x for x in self._pool(now) if x.tf >= 5 and x.swept] if self.cfg.get("ltf_in_rb") else []
        for r in sorted(rbs, key=lambda r: -r.hi):     # fiyata en yakın (en yüksek) boğa RB
            key = (r.t, r.tf)
            if key in self.used or r.hi >= c:
                continue
            if r.tf == 1:                               # [K] RB içinde RB: önemli bir HTF RB'ye dokunup oluşan 1 dk RB
                box = next((x for x in outer if x.lo <= r.hi and x.hi >= r.lo and self._important(x)), None)
                if box is None:
                    continue
                why = [f"outer:rb{box.tf}m"] + self._important(box)
            else:
                why = self._important(r)
            if not why:
                self.reasons.add("rb_not_important")
                continue
            inside = r.tf >= 30 or any(x.lo <= r.hi and x.hi >= r.lo and x is not r for x in htf)
            if htf_rule == "require" and not inside:
                self.reasons.add("rb_not_in_htf_rb")
                continue
            if inside and r.tf < 30:
                why = why + ["rb_in_rb"]
            best = (r, why)
            break
        if best is None:
            return []
        r, why = best
        self.used.add((r.t, r.tf))
        entry = round(r.mid if self.cfg.get("entry_mode") == "mid" else r.body, 2)   # [K] #21: "ortasından döner"
        if r.tf == 1:                                   # iç RB: stop iç fitilin hemen ötesi (Kaan: 2.75 puan, 7R)
            stop = r.wick - self.pts(self.wick_pad_pts, entry)
        elif self.cfg.get("stop_mode", "wick") == "mid":   # [K] büyük RB: stop C-level (orta) ötesi
            stop = r.mid - self.pts(0.5, entry)
        else:
            stop = min(entry - self.pts(self.min_stop_pts, entry), r.wick - self.pts(self.wick_pad_pts, entry))
        if not self.stop_ok(entry, stop):
            self.reasons.add("stop_size")
            return []
        risk = entry - stop
        tp1 = next(((n, p) for n, p in self._swing_highs_above(entry, now)
                    if (p - entry) / risk >= self.common["tp1_min_rr"]), None)
        min_rr = float(self.cfg.get("min_rr_tp2", 3.0))
        tp2 = next(((n, p) for n, p in self._opposite_above(entry, now) if (p - entry) / risk >= min_rr), None)
        if tp2 is None:
            self.reasons.add("no_tp2_rr")
            return []
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        conf = [f"rb{r.tf}m", "swept:" + "+".join(r.swept), "bias"] + why
        self.pending = Intent("dmc_rb", self._tag(), "long", "limit", entry, stop, tp1[1] if tp1 else None, tp2[1],
                              now, pd.Timestamp.combine(now.date(), self.we),
                              cancel_if_through=(tp1 or tp2)[1], confluence=conf,
                              targets={"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})
        return [self.pending]

    def _swing_highs_above(self, entry, now):
        b5 = resample(self.frame(), 5)
        b5 = b5[b5.index + pd.Timedelta(minutes=5) <= now]
        if len(b5) < 3:
            return []
        sh, _ = swing_points(b5, self.ctx.get("swing_k", 1))
        return [("swing_high", x) for x in sorted({float(x) for x in b5["High"][sh.to_numpy()] if x > entry})]

    def _opposite_above(self, entry, now):
        """Karşı taraftaki ayı RB / ayı FVG'nin alt kenarının hemen önü; ardından gün seviyeleri."""
        pad = self.pts(self.wick_pad_pts * 2, entry)
        f = self.frame()
        lv = []
        for tf in (15, 5):
            bt = resample(f, tf)
            bt = bt[bt.index + pd.Timedelta(minutes=tf) <= now]
            lv += [(f"bear_rb{tf}m", r.lo - pad) for r in find_rbs(bt, tf)
                   if r.kind == "bear" and not r.invalid and r.lo > entry]
            lv += [(f"bear_fvg{tf}m", g.bottom - pad) for g in find_fvgs(bt, tf)
                   if g.kind == "bear" and not g.closed_through and g.bottom > entry]
        lv += [(f"bear_fvg{g.tf}m", g.bottom - pad) for g in self.lv.fvgs if g.kind == "bear" and g.bottom > entry]
        lv += [(n, x) for n, x in self.lv.above().items() if x > entry]
        if self.cfg.get("htf_swing_tp"):
            lv += [(n, x) for n, x in self.lv.htf_above().items() if x > entry]
        return sorted(lv, key=lambda x: x[1])

    def filled(self):
        self.phase, self.pending = "done", None

    def exited(self):                                   # repeat: true → işlem kapanınca yeni (kullanılmamış) RB aranır
        self.phase = "idle"


class KaanLong(_Base):
    """Kaan'ın evrensel taktiği (tüm videoların ortak çekirdeği) — saatten ve sembolden bağımsız.

    1) önemli likidite süpürülür: PDL / premarket / overnight / önceki hafta dibi, 09:30-10:00 dibi, bugünkü 15 dk swing dipleri
    2) dönüş: 3 dk kapanış CISD seviyesinin üstünde + dönüş bacağında 1 dk boğa FVG (displacement)
    3) giriş: dönüş bacağının OTE bölgesindeki (0.62-0.79) boğa FVG/RB'nin yakın kenarına LİMİT
    4) stop: yapının (PDA dibi / 0.79) ötesi, 1-3 ATR(1 dk) — NQ'da Kaan'ın 10-25 puanına denk
    5) TP1 en yakın likidite (>= tp1_min_rr), TP2 karşı büyük likidite (>= min_rr_tp2); bias'a ters işlem yok
    """

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__(cfg.get("_name", "kaan"), cfg, levels, ctx)
        self.side_label = side_label
        self.ws, self.we = _hm(cfg.get("window_start", "09:35")), _hm(cfg.get("window_end", "15:30"))
        self.atr_min, self.atr_max = float(cfg.get("atr_min", 1.0)), float(cfg.get("atr_max", 3.0))
        self.atr_pad, self.min_rr = float(cfg.get("atr_pad", 0.25)), float(cfg.get("min_rr_tp2", 3.0))
        self.stale = pd.Timedelta(minutes=int(cfg.get("stale_min", 60)))
        self.or_lo = self.hod = self.ny_open = self.ten_open = None
        self.swept: set[str] = set()
        self._reset()

    def _reset(self):
        self.phase, self.pending = "idle", None
        self.sw_lo = self.sw_ts = self.leg_lo = self.leg_hi = self.armed_ts = None
        self.sw_names: list[str] = []

    def _atr(self) -> float:
        if self.lv.atr1:
            return self.lv.atr1
        f = self.frame()
        return float((f["High"] - f["Low"]).median()) if len(f) else 0.0

    def _liq_below(self, now) -> dict[str, float]:
        """Süpürülebilecek likidite. [K] açılışlar (TDO 18:00, gece yarısı, 09:30, 10AM) de likiditedir."""
        liq = {k: v for k, v in self.lv.below().items()}
        if self.or_lo is not None and now.time() >= dtime(10, 0):
            liq["orl"] = self.or_lo
        for n, x in (("tdo", self.lv.tdo), ("mid_open", self.lv.mid_open), ("ny_open", self.ny_open),
                     ("ten_open", self.ten_open)):
            if x is not None:
                liq[n] = x
        b15 = resample(self.frame(), 15)
        b15 = b15[b15.index + pd.Timedelta(minutes=15) <= now]
        if len(b15) >= 3:
            _, sl = swing_points(b15, self.ctx.get("swing_k", 1))
            for t15, x in b15["Low"][sl.to_numpy()].items():
                liq[f"sl15@{t15:%H:%M}"] = float(x)
        if self.cfg.get("msnr_liq"):                     # [K] fresh MSNR desteğine temas + MSS
            for p, role, tf in self.lv.msnr:
                if role == "sup":
                    liq[f"msnr{tf}@{p:.2f}"] = p
        return liq

    def _pdas(self) -> list[tuple[str, float, float]]:
        leg = self.frame(self.sw_ts)
        out = []
        for tf in (1, 3):
            bars = resample(leg, tf) if tf > 1 else leg
            out += [(f"fvg{tf}m", g.bottom, g.top) for g in find_fvgs(bars, tf) if g.kind == "bull" and not g.closed_through]
            out += [(f"rb{tf}m", r.lo, r.hi) for r in find_rbs(bars, tf) if r.kind == "bull" and not r.invalid]
        if self.cfg.get("breaker"):
            b = self._breaker()
            if b is not None:
                out.append(b)
        return out

    def _breaker(self) -> tuple[str, float, float] | None:
        """[K] Kaanermi modeli: süpürmeden önceki son boğa mumu (ayı OB) gövdeyle yukarı kırıldıysa → breaker."""
        pre = self.frame(self.sw_ts - pd.Timedelta(minutes=30))
        pre = pre[pre.index <= self.sw_ts]
        for tf in (3, 1):
            bars = resample(pre, tf) if tf > 1 else pre
            up = bars[bars["Close"] > bars["Open"]]
            if not len(up):
                continue
            ob = up.iloc[-1]
            after = self.frame(self.sw_ts)
            if len(after) and float(after["Close"].max()) > float(ob["High"]):
                return (f"breaker{tf}m", float(ob["Low"]), float(ob["High"]))
        return None

    def _sweep_rb(self) -> tuple[str, float, float] | None:
        """[K] Süpürmeyi yapan swing mumunun RB'si (gövde ucu → fitil); 15/5/3/1 dk, en yüksek TF tercih."""
        f = self.frame(self.sw_ts - pd.Timedelta(minutes=30))
        for tf in (15, 5, 3, 1):
            bars = resample(f, tf) if tf > 1 else f
            for r in find_rbs(bars, tf):
                if r.kind == "bull" and not r.invalid and abs(r.wick - self.sw_lo) <= 1e-9:
                    return (f"rb{tf}m", r.lo, r.hi)
        return None

    def _plan(self, now) -> Intent | None:
        if self.cfg.get("require_session_liq") and self.sw_names and all(x.startswith("sl15@") for x in self.sw_names):
            return self._no("only_sl15")               # [K] önemli likidite: seans seviyeleri; tek iç 15 dk swing yetmez
        atr = self._atr()
        if atr <= 0:
            return None
        pad, mn, mx = self.atr_pad * atr, self.atr_min * atr, self.atr_max * atr
        entry = stop = None
        rb = self._sweep_rb()
        if rb is not None:                       # [K] RB gövde ucuna limit; stop fitilin ötesi, büyükse C-level ötesi
            name, lo, hi = rb
            entry = round(hi, 2)
            for s_ in (lo - pad, (lo + hi) / 2 - pad):
                d = entry - s_
                if d <= mx:
                    stop = s_ if d >= mn else entry - mn
                    name = name + ("" if s_ == lo - pad else "_clevel")
                    break
        if stop is None:                         # OTE bölgesindeki FVG/RB'nin yakın kenarı
            z = ote(self.leg_lo, self.leg_hi, **self.ctx["ote"])
            pdas = [x for x in self._pdas() if x[1] <= z["zone_top"] and x[2] >= z["zone_bottom"]]
            if not pdas:
                return self._no("no_pd_in_ote")
            name, pb, pt = max(pdas, key=lambda x: x[2])
            entry = round(min(pt, z["zone_top"]), 2)
            for lvl in sorted((x for x in (pb, z["zone_bottom"]) if x < entry), reverse=True):
                if entry - (lvl - pad) >= mn:
                    stop = lvl - pad
                    break
            if stop is None:
                stop = entry - mn
        if entry - stop > mx or not self.stop_ok(entry, stop):
            return self._no("stop_size")
        above = {**self.lv.above(), "hod": self.hod, "ih": self.leg_hi}
        if self.cfg.get("htf_swing_tp"):
            above.update(self.lv.htf_above())
        b15 = resample(self.frame(), 15)
        b15 = b15[b15.index + pd.Timedelta(minutes=15) <= now]
        if len(b15) >= 3:
            sh, _ = swing_points(b15, self.ctx.get("swing_k", 1))
            for t15, x in b15["High"][sh.to_numpy()].items():
                above[f"sh15@{t15:%H:%M}"] = float(x)
        tp1, tp2 = pick_targets(entry, stop, above, {"ih"}, self.common["tp1_min_rr"], self.min_rr)
        if tp2 is None:
            return self._no("no_tp2_rr")
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        conf = ["sweep:" + "+".join(self.sw_names), "cisd+displacement", "ote", f"pda:{name}"]
        if self.lv.bias == "long":
            conf.append("bias")
        tag = self.pending.tag if self.pending else self._tag()
        return Intent(self.name, tag, "long", "limit", entry, stop, tp1[1] if tp1 else None, tp2[1], now,
                      now + self.stale, cancel_if_through=(tp1 or tp2)[1], confluence=conf,
                      targets={"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        self.hod = h if self.hod is None else max(self.hod, h)
        if t < dtime(10, 0):
            self.or_lo = l if self.or_lo is None else min(self.or_lo, l)
        if self.ny_open is None:
            self.ny_open = o
        if self.ten_open is None and t >= dtime(10, 0):
            self.ten_open = o
        now = ts + ONE_MIN
        out: list[Intent] = []
        if self.phase in ("trade", "done") or t < self.ws:
            return out
        if t >= self.we:
            if self.pending:
                out.append(replace(self.pending, kind="cancel"))
            self.phase, self.pending = "done", None
            return out
        mode = self.cfg.get("require_bias", "not_against")
        if (mode == "aligned" and self.lv.bias != "long") or (mode == "not_against" and self.lv.bias == "short"):
            self.reasons.add("bias_" + self.lv.bias)
            return out
        prev_l = self.rows[-2][3] if len(self.rows) > 1 else None
        hit = [n for n, x in self._liq_below(now).items()
               if n not in self.swept and l < x and (prev_l is None or prev_l >= x)]
        self.swept.update(hit)
        if hit or (self.sw_lo is not None and l < self.sw_lo):           # yeni / daha derin süpürme
            if hit or self.phase != "idle":
                if self.pending:
                    out.append(replace(self.pending, kind="cancel"))
                names = (self.sw_names if not hit else hit)
                self._reset()
                self.phase, self.sw_lo, self.sw_ts, self.sw_names = "swept", l, ts, names
            return out
        if self.phase == "swept":
            if now - self.sw_ts > self.stale:
                self._reset()
                return out
            if (now.hour * 60 + now.minute) % 3 == 0:
                cisd = cisd_level(self.frame(self.sw_ts - pd.Timedelta(minutes=30)), self.sw_ts)
                disp = any(n == "fvg1m" for n, _, _ in self._pdas())
                if cisd is not None and c > cisd and disp:
                    self.phase, self.armed_ts = "armed", now
                    self.leg_lo, self.leg_hi = self.sw_lo, float(self.frame(self.sw_ts)["High"].max())
        if self.phase == "armed":
            self.leg_hi = max(self.leg_hi, h)
            if now - self.armed_ts > self.stale:
                if self.pending:
                    out.append(replace(self.pending, kind="cancel"))
                self._reset()
                return out
            plan = self._plan(now)
            if plan is not None:
                pd_ = self.pending
                if pd_ is None or abs(pd_.entry - plan.entry) > 1e-9 or pd_.stop != plan.stop or pd_.tp2 != plan.tp2:
                    self.pending = plan
                    out.append(plan)
            elif self.pending is not None:
                out.append(replace(self.pending, kind="cancel"))
                self.pending = None
        return out

    def _no(self, reason):
        self.reasons.add(reason)
        return None

    def filled(self):
        self.phase, self.pending = "trade", None

    def exited(self):
        self._reset()


class BoxLong(_Base):
    """[K] SIFIRDAN #15 Box setup (AMD kalıbı). Dar akümülasyon kutusu (5 dk, en az `box_bars` mum, yükseklik <=
    `box_atr_mult` x ortalama 5 dk mum boyu) -> kutunun dibi süpürülür (manipülasyon) -> 5 dk kapanış kutunun tepesinin
    üstünde (kırılım/displacement) -> kutunun dibine LİMİT (short'ta aynalı: tepesi). Stop: manipülasyon dibinin
    ötesi (en az `min_stop_pts` NQ puanı karşılığı); hedef: yukarıdaki likidite (>= min_rr_tp2)."""

    def __init__(self, cfg, levels, ctx, side_label="long"):
        super().__init__("box", cfg, levels, ctx)
        self.side_label = side_label
        self.ws, self.we = _hm(cfg.get("window_start", "09:30")), _hm(cfg.get("window_end", "15:00"))
        self.n, self.k = int(cfg.get("box_bars", 8)), float(cfg.get("box_atr_mult", 3.0))
        self.min_stop_pts = float(cfg.get("min_stop_pts", 10))
        self.wick_pad_pts = float(cfg.get("wick_pad_pts", 0.25))
        self.max_wait = int(cfg.get("break_within_bars", 12))
        self.box = None                                 # {"lo", "hi", "mlow", "age"}
        self.pending = None
        self.hod = None

    def on_bar(self, ts, o, h, l, c, v):
        t = ts.time()
        self.rows.append((ts, o, h, l, c, v))
        self.hod = h if self.hod is None else max(self.hod, h)
        now = ts + ONE_MIN
        if self.phase == "done":
            return []
        if t >= self.we:
            self.phase = "done"
            if self.pending:
                p, self.pending = self.pending, None
                return [replace(p, kind="cancel")]
            return []
        if self.pending is not None or t < self.ws or now.minute % 5:
            return []
        b5 = resample(self.frame(), 5)
        b5 = b5[b5.index + pd.Timedelta(minutes=5) <= now]
        if len(b5) < self.n + 1:
            return []
        bar = b5.iloc[-1]
        if self.box is None:                            # akümülasyon: son n mum dar bir kutuda
            win = b5.iloc[-self.n:]
            lo, hi = float(win["Low"].min()), float(win["High"].max())
            if hi - lo <= self.k * float((b5["High"] - b5["Low"]).mean()):
                self.box = {"lo": lo, "hi": hi, "mlow": None, "age": 0}
            return []
        bx = self.box
        if bx["mlow"] is None:
            if bar.Close > bx["hi"]:                    # manipülasyonsuz kırılım -> kutu geçersiz
                self.box = None
            elif bar.Low < bx["lo"]:
                bx["mlow"] = float(bar.Low)
            return []
        bx["mlow"] = min(bx["mlow"], float(bar.Low))
        bx["age"] += 1
        depth = bx["hi"] - bx["lo"]
        if bx["mlow"] < bx["lo"] - 2 * depth or bx["age"] > self.max_wait:
            self.box = None                             # manipülasyon değil gerçek kırılım ya da kırılım gelmedi
            return []
        if bar.Close <= bx["hi"]:
            return []
        self.box = None
        entry = round(bx["lo"], 2)
        if c <= entry:
            self.reasons.add("late")
            return []
        stop = min(entry - self.pts(self.min_stop_pts, entry), bx["mlow"] - self.pts(self.wick_pad_pts, entry))
        if not self.stop_ok(entry, stop):
            self.reasons.add("stop_size")
            return []
        tp1, tp2 = pick_targets(entry, stop, {**self.lv.above(), "hod": self.hod}, set(),
                                self.common["tp1_min_rr"], float(self.cfg.get("min_rr_tp2", 2.0)))
        if tp2 is None:
            self.reasons.add("no_tp2_rr")
            return []
        if tp1 is not None and tp1[1] >= tp2[1]:
            tp1 = None
        conf = ["box", f"box{self.n * 5}m", "manip_sweep", "break"]
        self.pending = Intent("box", self._tag(), "long", "limit", entry, stop, tp1[1] if tp1 else None, tp2[1],
                              now, pd.Timestamp.combine(now.date(), self.we),
                              cancel_if_through=(tp1 or tp2)[1], confluence=conf,
                              targets={"tp1": tp1[0] if tp1 else "", "tp2": tp2[0]})
        return [self.pending]

    def filled(self):
        self.phase, self.pending = "done", None


class SidePair:
    """Bir setup'ın long ve (aynalanmış) short makinesi."""

    def __init__(self, cls, cfg, levels: DayLevels, ctx: dict):
        self.name = cls.__name__
        self.long = cls(cfg, levels, ctx, "long")
        self.short = cls(cfg, levels.mirrored(), ctx, "short")
        self.setup = self.long.name

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        sides = self.long.cfg.get("sides") or ("long", "short")     # ör. sides: [long] → sadece long
        out = self.long.on_bar(ts, o, h, l, c, v) if "long" in sides else []
        if "short" in sides:
            out += [_unmirror(i) for i in self.short.on_bar(ts, -o, -l, -h, -c, v)]
        return out

    def filled(self, intent: Intent):
        self.long.filled()
        self.short.filled()

    def exited(self):
        for m in (self.long, self.short):
            if hasattr(m, "exited"):
                m.exited()
