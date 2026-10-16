"""Gün motoru: setup'ları bar bar besler, gün filtrelerini ve günlük limitleri uygular (STRATEGY_SPEC §8–9).

Hem backtest hem canlı yürütücü bu sınıfı kullanır; tek fark barların nereden geldiği ve dolumları kimin
bildirdiğidir (`on_fill` / `on_exit`).
"""
from __future__ import annotations

from datetime import date, time as dtime

import pandas as pd

from .levels import DayLevels
from .setups import BoxLong, Intent, KaanLong, LunchLong, RBLong, SidePair, TenAMLong

SETUP_CLASSES = {"kaan": KaanLong, "kaanermi": KaanLong, "ten_am": TenAMLong, "dmc_rb": RBLong, "lunch": LunchLong, "box": BoxLong}


def day_rules(cfg: dict, day: date) -> dict:
    """Gün filtreleri: güne göre min confluence ve günlük işlem sınırı."""
    d = cfg["dmc"]
    f = d.get("filters", {})
    min_conf = d["min_confluence"]
    if day.weekday() == 0:
        min_conf += f.get("monday_extra_confluence", 0)
    if day.weekday() == 4:
        min_conf = max(min_conf, f.get("friday_min_confluence", min_conf))
    max_trades = d["max_trades_per_day"]
    if day.month == 8 and f.get("august_max_trades"):
        max_trades = min(max_trades, f["august_max_trades"])
    from ..broker import NYSE_HOLIDAYS
    if f.get("skip_us_holidays", True) and day in NYSE_HOLIDAYS:
        max_trades = 0
    return {"min_confluence": min_conf, "limit_min_confluence": max(d["limit_entry_min_confluence"], min_conf),
            "max_trades": max_trades}


class DayEngine:
    def __init__(self, cfg: dict, levels: DayLevels, extra: dict | None = None, symbol: str | None = None):
        d = cfg["dmc"]
        sym = (symbol or d.get("symbol", "")).upper()
        rules = day_rules(cfg, levels.day)
        self.max_trades = rules["max_trades"]
        self.max_open = d.get("max_open_positions", 1)
        self.stop_halt = d.get("halt_after_stops", 2)
        ctx = {
            "common": {k: d[k] for k in ("stop_buffer_pct", "min_stop_pct", "max_stop_pct", "tp1_min_rr", "min_rr_tp2")},
            "ote": d["ote"], "min_confluence": rules["min_confluence"],
            "limit_min_confluence": rules["limit_min_confluence"],
            "futures": sym.startswith("NQ"), "symbol": sym, "nq_atr1_ref": d.get("nq_atr1_ref", 13.0),
            "swing_k": d.get("swing_k", 1),
            "vol_mult": (d.get("vol_mult") or {}).get(sym, 1),
            **(extra or {}),
        }
        self.ctx = ctx
        raw_watch = d.get("watch_setups") or ()
        watch = set(raw_watch.get(sym, ()) if isinstance(raw_watch, dict) else raw_watch)
        self.pairs = {name: SidePair(cls, {**d["setups"][name], "_name": name}, levels, ctx)
                      for name, cls in SETUP_CLASSES.items()
                      if d["setups"].get(name, {}).get("enabled") and name not in watch}
        self.trades = self.stops = self.open_positions = 0
        self.repeat = {n: bool(d["setups"][n].get("repeat")) for n in self.pairs}
        self.done: set[str] = set()
        self.halted = False
        f = d.get("filters", {})
        self.news = (extra or {}).get("news")
        self.news_min, self.speech_min = f.get("red_news_block_minutes", 5), f.get("speech_block_minutes", 30)
        self.blocked_by: set[str] = set()
        g = (d.get("filters") or {}).get("trend_gate", "off")
        self.gate = "off" if g in (False, None, "off") else str(g)
        self.o930 = self.o10 = None
        self.live: dict[str, tuple[str, str]] = {}

    def set_premarket(self, pm_df: pd.DataFrame) -> None:
        for pair in self.pairs.values():
            pair.set_premarket(pm_df)

    def attach_fast_frames(self, rth_bars: pd.DataFrame, pm_bars: pd.DataFrame | None = None) -> None:
        from .data import _raw_fast_resample
        from .structure import mirror
        pm = pm_bars if pm_bars is not None else pd.DataFrame()
        m_bars = mirror(rth_bars)
        full_bars = pd.concat([pm, rth_bars]) if len(pm) else rth_bars
        m_full_bars = mirror(full_bars)
        for b in (rth_bars, m_bars, full_bars, m_full_bars):
            b.attrs["_tf_map"] = {tf: _raw_fast_resample(b, tf) for tf in (3, 5, 15, 30, 45)}
        pm_len = len(pm)
        for pair in self.pairs.values():
            pair.long._day_df = rth_bars
            pair.long._full_day_df = full_bars
            pair.long._pm_len = pm_len
            pair.short._day_df = m_bars
            pair.short._full_day_df = m_full_bars
            pair.short._pm_len = pm_len

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        out: list[Intent] = []
        for name, pair in self.pairs.items():
            if name not in self.done:
                out += pair.on_bar(ts, o, h, l, c, v)
        if self.halted or self.trades >= self.max_trades or self.open_positions >= self.max_open:
            out = [i for i in out if i.kind == "cancel"]
        elif self.news is not None and any(i.kind != "cancel" for i in out):
            from .news import blocked
            why = blocked(self.news, ts + pd.Timedelta(minutes=1), self.news_min, self.speech_min)
            if why and why != "news_unavailable":
                self.blocked_by.add(why)
                out = [i for i in out if i.kind == "cancel"]
        if self.gate != "off":
            out = self._trend_gate(ts, o, c, out)
        return out

    def _trend_gate(self, ts, o, c, out: list[Intent]) -> list[Intent]:
        t = ts.time()
        if t == dtime(9, 30):
            self.o930 = o
        elif t == dtime(10, 0):
            self.o10 = o
        ref = self.o10 if self.o10 is not None else self.o930
        kept = []
        for i in out:
            if i.kind == "cancel":
                self.live.pop(i.tag, None)
                kept.append(i)
            elif ref is None or (c >= ref if i.side == "long" else c <= ref):
                self.live[i.tag] = (i.setup, i.side)
                kept.append(i)
            else:
                self.blocked_by.add("trend_gate")
        if ref is not None:
            for tag, (setup, side) in list(self.live.items()):
                if (side == "long" and c < ref) or (side == "short" and c > ref):
                    kept.append(Intent(setup, tag, side, "cancel"))
                    del self.live[tag]
        return kept

    def on_fill(self, intent: Intent) -> None:
        self.live.pop(intent.tag, None)
        self.trades += 1
        self.open_positions += 1
        self.last_setup = intent.setup
        if not self.repeat.get(intent.setup):
            self.done.add(intent.setup)
        self.pairs[intent.setup].filled(intent)

    def on_exit(self, intent: Intent, r: float) -> None:
        self.open_positions = max(0, self.open_positions - 1)
        if self.repeat.get(intent.setup):
            self.pairs[intent.setup].exited()
        if r <= -0.9:
            self.stops += 1
            if self.stops >= self.stop_halt:
                self.halted = True

    def reasons(self) -> dict[str, list[str]]:
        """Tanı: setup/yön başına bugün görülen ret sebepleri."""
        return {f"{n}:{side}": sorted(getattr(p, side).reasons) for n, p in self.pairs.items() for side in ("long", "short")}

    @property
    def accepting(self) -> bool:
        return not self.halted and self.trades < self.max_trades and self.open_positions < self.max_open
