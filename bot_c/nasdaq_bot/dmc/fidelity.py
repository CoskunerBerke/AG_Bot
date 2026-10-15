"""Kaan uyum testi: botun kararlarını Kaan ekibinin tarihli kararlarıyla karşılaştırır.

Kaynak: knowledge/fixtures/*.yaml. Ölçüt P&L değil, karar uyumu:
  * none günleri  : bot hiç işlem açmamalı
  * işlem günleri : bot beklenen yönde, beklenen pencere (±tolerans) içinde işlem açmalı; setup da aynıysa tam uyum
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from .backtest import simulate_day
from .data import daily_bars, load_history, peer_symbol
from .engine import DayEngine
from .levels import am_range_median, am_ranges, prepare_day


@dataclass
class CaseResult:
    day: date
    expect: str
    exp_setup: str
    exp_bias: str
    bot_bias: str
    bot: str              # "short ten_am 10:12 R=+1.2" ...
    verdict: str          # TAM | YÖN | YANLIŞ | KAÇIRDI | FAZLA | DOĞRU-YOK
    reasons: dict


PRIORITY = {"kaan_telegram.yaml": 3, "kaan_own.yaml": 2, "kaan_cases.yaml": 1}


def load_cases(root: Path) -> list[dict]:
    """Tüm fixture'lar; aynı gün daha yüksek öncelikli kaynakta varsa düşük öncelikliler atılır.
    Penceresi 09:30'dan önce başlayan (premarket) vakalar kapsam dışıdır — bot RTH'de işlem yapar."""
    out = []
    for f in sorted((root / "knowledge" / "fixtures").glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for c in doc.get("cases", []):
            c.setdefault("scope", doc.get("scope"))
            c["_prio"] = PRIORITY.get(f.name, 0)
            c["_src"] = f.name
            out.append(c)
    best = {}
    for c in out:
        d = str(c["date"])
        best[d] = max(best.get(d, 0), c["_prio"])
    keep = [c for c in out if c["_prio"] == best[str(c["date"])]]
    return [c for c in keep if not (c.get("window") and dtime.fromisoformat(str(c["window"][0])) < dtime(9, 30))]


def _events(case: dict, day: date):
    from .news import Event
    return [Event(pd.Timestamp(datetime.combine(day, dtime.fromisoformat(str(n["t"])))), n.get("title", ""),
                  n.get("impact", "High")) for n in case.get("news", [])]


def _in_window(ts: pd.Timestamp, win: list[str] | None, tol_min: int) -> bool:
    if not win:
        return True
    a = datetime.combine(ts.date(), dtime.fromisoformat(win[0])) - timedelta(minutes=tol_min)
    b = datetime.combine(ts.date(), dtime.fromisoformat(win[1])) + timedelta(minutes=tol_min)
    return a <= ts.to_pydatetime() <= b


def run_case(cfg: dict, df: pd.DataFrame, case: dict, daily, amr, peer_df: pd.DataFrame | None = None,
             tol_min: int = 30) -> CaseResult:
    day = case["date"] if isinstance(case["date"], date) else date.fromisoformat(str(case["date"]))
    t = df.index.time
    bars = df[(df.index.date == day) & (t >= dtime(9, 30)) & (t < dtime(16, 0))]
    pm = df[(df.index.date == day) & (t >= dtime(8, 0)) & (t < dtime(9, 30))]
    exp, exp_setup, exp_bias = case["expect"], case.get("setup", ""), case.get("bias", "")
    if bars.empty:
        return CaseResult(day, exp, exp_setup, exp_bias, "-", "veri yok/tatil",
                          "DOĞRU-YOK" if exp == "none" else "KAÇIRDI", {})
    lv = prepare_day(df[(df.index >= pd.Timestamp(day) - pd.Timedelta(days=16)) &
                        (df.index < pd.Timestamp(datetime.combine(day, dtime(9, 30))))], day,
                     daily=daily[daily.index < pd.Timestamp(day)], fvg_cfg=cfg["dmc"].get("fvg"))
    extra = {"am_range_median": am_range_median(amr, day), "news": _events(case, day)}
    if peer_df is not None and len(peer_df):
        extra["peer"] = peer_df[peer_df.index.date == day]
    eng = DayEngine(cfg, lv, extra)
    eng.attach_fast_frames(bars, pm)
    trades = simulate_day(cfg, bars, eng)
    sc = case.get("scope") or {}
    if sc:
        until = dtime.fromisoformat(sc.get("until", "23:59"))
        trades = [x for x in trades if x.setup in sc.get("setups", [x.setup]) and x.filled_at.time() < until]
    desc = "; ".join(f"{x.side} {x.setup} {x.filled_at:%H:%M} R={x.r:+.1f}" for x in trades) or "işlem yok"
    if exp == "none":
        verdict = "DOĞRU-YOK" if not trades else "FAZLA"
    else:
        hits = [x for x in trades if x.side == exp and _in_window(x.filled_at, case.get("window"), tol_min)]
        if any(x.setup == exp_setup for x in hits):
            verdict = "TAM"
        elif hits:
            verdict = "YÖN"
        elif trades:
            verdict = "YANLIŞ"
        else:
            verdict = "KAÇIRDI"
    return CaseResult(day, exp, exp_setup, exp_bias, lv.bias, desc, verdict,
                      {k: v for k, v in eng.reasons().items() if v})


def run_all(cfg: dict, root: Path) -> list[CaseResult]:
    cases = load_cases(root)
    days = [date.fromisoformat(str(c["date"])) for c in cases]
    sym = cfg["dmc"]["symbol"]
    df = load_history(cfg, sym, min(days) - timedelta(days=60), max(days))
    daily, amr = daily_bars(df, sym), am_ranges(df)
    peer_df = None
    ps = peer_symbol(sym)
    if ps:
        try:
            peer_df = load_history(cfg, ps, min(days) - timedelta(days=2), max(days))
        except Exception:
            peer_df = None
    return [run_case(cfg, df, c, daily, amr, peer_df=peer_df) for c in cases]


def score(results: list[CaseResult]) -> dict:
    n = len(results)
    trade_days = [r for r in results if r.expect != "none"]
    none_days = [r for r in results if r.expect == "none"]
    return {
        "gün": n,
        "karar_uyumu": sum(r.verdict in ("TAM", "YÖN", "DOĞRU-YOK") for r in results) / n if n else 0,
        "işlem_günü_yön": f"{sum(r.verdict in ('TAM', 'YÖN') for r in trade_days)}/{len(trade_days)}",
        "işlem_günü_tam": f"{sum(r.verdict == 'TAM' for r in trade_days)}/{len(trade_days)}",
        "işlemsiz_gün_doğru": f"{sum(r.verdict == 'DOĞRU-YOK' for r in none_days)}/{len(none_days)}",
        "bias_uyumu": f"{sum(r.exp_bias == r.bot_bias for r in results if r.exp_bias)}/{sum(1 for r in results if r.exp_bias)}",
    }
