"""Gün motoru: setup'ları bar bar besler, gün filtrelerini ve günlük limitleri uygular (STRATEGY_SPEC §8–9).

Hem backtest hem canlı yürütücü bu sınıfı kullanır; tek fark barların nereden geldiği ve dolumları kimin
bildirdiğidir (`on_fill` / `on_exit`).
"""
from __future__ import annotations

from datetime import date

from .levels import DayLevels
from .setups import Intent, LunchLong, SidePair, TenAMLong

SETUP_CLASSES = {"ten_am": TenAMLong, "lunch": LunchLong}


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
    return {"min_confluence": min_conf, "limit_min_confluence": max(d["limit_entry_min_confluence"], min_conf),
            "max_trades": max_trades}


class DayEngine:
    def __init__(self, cfg: dict, levels: DayLevels, extra: dict | None = None):
        d = cfg["dmc"]
        rules = day_rules(cfg, levels.day)
        self.max_trades = rules["max_trades"]
        self.max_open = d.get("max_open_positions", 1)
        self.stop_halt = d.get("halt_after_stops", 2)
        ctx = {
            "common": {k: d[k] for k in ("stop_buffer_pct", "min_stop_pct", "max_stop_pct", "tp1_min_rr", "min_rr_tp2")},
            "ote": d["ote"], "min_confluence": rules["min_confluence"],
            "limit_min_confluence": rules["limit_min_confluence"], **(extra or {}),
        }
        self.pairs = {name: SidePair(cls, d["setups"][name], levels, ctx)
                      for name, cls in SETUP_CLASSES.items() if d["setups"].get(name, {}).get("enabled")}
        self.trades = self.stops = self.open_positions = 0
        self.done: set[str] = set()
        self.halted = False

    def on_bar(self, ts, o, h, l, c, v) -> list[Intent]:
        out: list[Intent] = []
        for name, pair in self.pairs.items():
            if name not in self.done:
                out += pair.on_bar(ts, o, h, l, c, v)
        if self.halted or self.trades >= self.max_trades or self.open_positions >= self.max_open:
            out = [i for i in out if i.kind == "cancel"]
        return out

    def on_fill(self, intent: Intent) -> None:
        self.trades += 1
        self.open_positions += 1
        self.done.add(intent.setup)
        self.pairs[intent.setup].filled(intent)

    def on_exit(self, intent: Intent, r: float) -> None:
        self.open_positions = max(0, self.open_positions - 1)
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
