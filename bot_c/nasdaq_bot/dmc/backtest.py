"""DMC backtest: 1 dk barlarda emir/stop/TP simülasyonu (muhafazakâr dolum kuralları).

Kurallar:
  * Bir barın kapanışında üretilen emir en erken bir sonraki barda aktif olur.
  * Market giriş = sonraki barın açılışı + kayma. Limit long, Low ≤ limit − fill_ticks ise dolar (fiyat min(limit, Open)).
  * Aynı barda hem iptal seviyesi (TP1'e kaçış) hem limit görülürse önce iptal varsayılır.
  * Dolum barında sadece stop kontrol edilir (TP sayılmaz). Aynı barda stop ve TP varsa önce stop.
  * TP1'de pozisyonun `tp1_fraction` kadarı kapanır, stop girişe (BE) çekilir.
  * `flat_time` barının açılışında kalan her şey kapatılır.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

from .data import daily_bars, load_history, peer_symbol
from .engine import DayEngine
from .levels import am_range_median, am_ranges, prepare_day
from .setups import Intent
from .structure import swing_points

log = logging.getLogger(__name__)
PEER_MAP = {"NQ.v.0": "ES.v.0", "ES.v.0": "NQ.v.0", "QQQ": "SPY", "TQQQ": "SPY", "SPY": "QQQ", "DIA": "SPY", "GLD": "SLV", "SLV": "GLD"}


@dataclass
class Trade:
    day: date
    setup: str
    side: str
    tag: str
    signal_at: pd.Timestamp
    filled_at: pd.Timestamp
    entry: float
    stop: float
    tp1: float | None
    tp2: float
    confluence: list[str]
    targets: dict
    exits: list[tuple] = field(default_factory=list)   # (ts, price, fraction, reason)
    r: float = 0.0

    @property
    def stop_pct(self) -> float:
        return abs(self.entry - self.stop) / self.entry


def _slip(px: float, side: str, bps: float, adverse_for: str) -> float:
    """adverse_for='buy' fiyatı yukarı, 'sell' aşağı kaydırır."""
    k = bps / 1e4
    return px * (1 + k) if adverse_for == "buy" else px * (1 - k)


class _Pos:
    def __init__(self, it: Intent, fill: float, ts, frac1: float):
        self.it, self.fill, self.ts = it, fill, ts
        self.mfe = 0.0
        self.bars: list[tuple] = []
        self.stop = it.stop
        self.left = 1.0
        self.tp1_done = it.tp1 is None
        self.frac1 = frac1
        self.exits: list[tuple] = []
        self.dir = 1 if it.side == "long" else -1

    def r(self) -> float:
        risk = abs(self.fill - self.it.stop)
        if risk <= 0:
            return -1.0
        return sum(f * (p - self.fill) * self.dir for _, p, f, _ in self.exits) / risk


def simulate_day(cfg: dict, bars: pd.DataFrame, engine: DayEngine) -> list[Trade]:
    """bars: o günün 09:30–15:59 1 dk barları."""
    d = cfg["dmc"]
    bt = d.get("backtest", {})
    mg = d.get("manage", {})
    be_r, stall_min, stall_r, trail = (float(mg.get("be_at_r", 0)), int(mg.get("stall_min", 0)),
                                       float(mg.get("stall_r", 0.5)), bool(mg.get("trail_after_tp1", False)))
    slip, tick = bt.get("slippage_bps", 1.0), bt.get("fill_ticks", 1) * 0.01
    flat_t = dtime.fromisoformat(d["flat_time"])
    pending: dict[str, Intent] = {}
    filled_tags: set[str] = set()
    pos: _Pos | None = None
    trades: list[Trade] = []

    def close(p: _Pos, ts, px, reason):
        p.exits.append((ts, px, p.left, reason))
        p.left = 0.0
        r = p.r()
        trades.append(Trade(p.it.created.date(), p.it.setup, p.it.side, p.it.tag, p.it.created, p.ts, p.fill,
                            p.it.stop, p.it.tp1, p.it.tp2, p.it.confluence, p.it.targets, p.exits, r))
        engine.on_exit(p.it, r)

    for ts, o, h, l, c, v in bars[["Open", "High", "Low", "Close", "Volume"]].itertuples(name=None):
        if ts.time() >= flat_t:
            pending.clear()
            if pos:
                px = _slip(o, pos.it.side, slip, "sell" if pos.dir == 1 else "buy")
                close(pos, ts, px, "flat")
                pos = None
            continue
        # 1) bekleyen girişler
        for tag, it in list(pending.items()):
            if pos is not None:
                break
            if it.valid_until is not None and ts >= it.valid_until:
                pending.pop(tag)
                continue
            long = it.side == "long"
            fill = None
            if it.kind == "market":
                fill = _slip(o, it.side, slip, "buy" if long else "sell")
            else:
                cit = it.cancel_if_through
                if cit is not None and ((long and h >= cit) or (not long and l <= cit)):
                    pending.pop(tag)
                    continue
                if long and l <= it.entry - tick:
                    fill = min(it.entry, o)
                elif not long and h >= it.entry + tick:
                    fill = max(it.entry, o)
            if fill is None:
                continue
            pending.clear()
            filled_tags.add(tag)
            pos = _Pos(it, fill, ts, d["tp1_fraction"])
            engine.on_fill(it)
            if (long and (l <= it.stop or fill <= it.stop)) or (not long and (h >= it.stop or fill >= it.stop)):
                px = _slip(min(it.stop, fill) if long else max(it.stop, fill), it.side, slip, "sell" if long else "buy")
                close(pos, ts, px, "stop")
                pos = None
            else:
                pos.fresh = True
            break
        # 2) açık pozisyon yönetimi
        if pos is not None and not getattr(pos, "fresh", False):
            it, long = pos.it, pos.dir == 1
            risk = abs(pos.fill - it.stop)
            if stall_min and not any(e[3] == "tp1" for e in pos.exits) and \
                    (ts - pos.ts) >= pd.Timedelta(minutes=stall_min) and pos.mfe < stall_r * risk:
                close(pos, ts, _slip(o, it.side, slip, "sell" if long else "buy"), "stall")
                pos = None
        if pos is not None and not getattr(pos, "fresh", False):
            it, long = pos.it, pos.dir == 1
            risk = abs(pos.fill - it.stop)
            hit_stop = l <= pos.stop if long else h >= pos.stop
            if hit_stop:
                gap = (o < pos.stop) if long else (o > pos.stop)
                px = o if gap else pos.stop
                reason = "be" if any(e[3] == "tp1" for e in pos.exits) else "stop"
                close(pos, ts, _slip(px, it.side, slip, "sell" if long else "buy"), reason)
                pos = None
            else:
                if not pos.tp1_done and ((long and h >= it.tp1) or (not long and l <= it.tp1)):
                    pos.exits.append((ts, it.tp1, pos.frac1, "tp1"))
                    pos.left -= pos.frac1
                    pos.tp1_done = True
                    pos.stop = pos.fill
                if (long and h >= it.tp2) or (not long and l <= it.tp2):
                    close(pos, ts, it.tp2, "tp2")
                    pos = None
            if pos is not None:
                pos.mfe = max(pos.mfe, (h - pos.fill) if long else (pos.fill - l))
                pos.bars.append((ts, o, h, l, c))
                if be_r and pos.mfe >= be_r * risk and pos.stop != pos.fill and \
                        ((long and pos.stop < pos.fill) or (not long and pos.stop > pos.fill)):
                    pos.stop = pos.fill
                if trail and any(e[3] == "tp1" for e in pos.exits) and len(pos.bars) >= 15:
                    b5 = pd.DataFrame(pos.bars, columns=["ts", "Open", "High", "Low", "Close"]).set_index("ts")
                    b5 = b5.resample("5min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                    if len(b5) >= 3:
                        sh, sl = swing_points(b5, 1)
                        lv_ = b5["Low"][sl.to_numpy()] if long else b5["High"][sh.to_numpy()]
                        if len(lv_):
                            cand = float(lv_.iloc[-1])
                            if (long and cand > pos.stop and cand < c) or (not long and cand < pos.stop and cand > c):
                                pos.stop = cand
        elif pos is not None:
            pos.fresh = False
        # 3) motor
        for it in engine.on_bar(ts, o, h, l, c, v):
            if it.kind == "cancel":
                pending.pop(it.tag, None)
            elif it.tag not in filled_tags and pos is None:
                pending[it.tag] = it
    if pos is not None:
        close(pos, bars.index[-1], float(bars["Close"].iloc[-1]), "eod")
    return trades


def run_backtest(cfg: dict, df1m: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    d = cfg["dmc"]
    daily = daily_bars(df1m, d["symbol"])
    amr = am_ranges(df1m)
    t = df1m.index.time
    rth = df1m[(t >= dtime(9, 30)) & (t < dtime(16, 0))]
    pm_all = df1m[(t >= dtime(8, 0)) & (t < dtime(9, 30))]
    days = [x for x in sorted(set(rth.index.date)) if start <= x <= end]
    rows: list[Trade] = []
    by_day = {k: g for k, g in rth.groupby(rth.index.date)}
    pm_by_day = {k: g for k, g in pm_all.groupby(pm_all.index.date)}
    peer_df = None
    ps = peer_symbol(d["symbol"])
    if ps and any(d["setups"].get(n, {}).get("smt", "off") != "off" for n in d["setups"]):
        try:
            peer_df = load_history(cfg, ps, start - timedelta(days=2), end)
        except Exception as e:
            log.warning("SMT eş verisi yok (%s): %s", ps, e)
    peer_by_day = {k: g for k, g in peer_df.groupby(peer_df.index.date)} if peer_df is not None and len(peer_df) else {}
    for i, day in enumerate(days):
        win = df1m[(df1m.index >= pd.Timestamp(day) - pd.Timedelta(days=16)) &
                   (df1m.index < pd.Timestamp(datetime.combine(day, dtime(9, 30))))]
        lv = prepare_day(win, day, daily=daily[daily.index < pd.Timestamp(day)], fvg_cfg=d.get("fvg"))
        extra = {"am_range_median": am_range_median(amr, day)}
        if day in peer_by_day:
            extra["peer"] = peer_by_day[day]
        eng = DayEngine(cfg, lv, extra)
        day_bars = by_day[day]
        pm_bars = pm_by_day.get(day, pd.DataFrame())
        eng.attach_fast_frames(day_bars, pm_bars)
        rows += simulate_day(cfg, day_bars, eng)
        if i % 100 == 0:
            log.info("%s  (%d/%d) işlem=%d", day, i + 1, len(days), len(rows))
    df = pd.DataFrame([t.__dict__ | {"stop_pct": t.stop_pct, "exit_reason": t.exits[-1][3] if t.exits else ""}
                       for t in rows])
    return df


def stats(trades: pd.DataFrame, cfg: dict) -> dict:
    if trades.empty:
        return {"n": 0}
    d = cfg["dmc"]
    r = trades["r"].to_numpy()
    wins, losses = r[r > 0], r[r < 0]
    cum = np.cumsum(r)
    dd_r = float((np.maximum.accumulate(np.r_[0, cum]) - np.r_[0, cum]).max())
    risk_pct = d["risk_per_trade_pct"] / 100
    bp_cap = d.get("bp_multiplier", 4) * d.get("bp_usage", 0.9)
    eff = np.minimum(risk_pct, bp_cap * trades["stop_pct"].to_numpy())
    eq = np.cumprod(1 + r * eff)
    eq_dd = float((1 - eq / np.maximum.accumulate(eq)).max())
    return {
        "n": int(len(r)), "win_rate": float((r > 0).mean()), "avg_r": float(r.mean()), "total_r": float(r.sum()),
        "pf": float(wins.sum() / -losses.sum()) if len(losses) else float("inf"),
        "max_dd_r": dd_r, "avg_stop_pct": float(trades["stop_pct"].mean() * 100),
        "avg_eff_risk_pct": float(eff.mean() * 100), "equity_return_pct": float((eq[-1] - 1) * 100),
        "equity_max_dd_pct": eq_dd * 100,
    }
