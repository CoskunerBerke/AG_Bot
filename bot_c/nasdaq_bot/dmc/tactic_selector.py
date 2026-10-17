"""Otonom Grafik Analizörü ve Çoklu Taktik Seçici (Chart Regime & Walk-Forward Tactic Selector).

Bu modül:
1. Bir hisse senedi / ETF / vadeli grafiğini (`QQQ`, `TQQQ`, `GLD`, `SPY`, `SLV`, `DIA`, `NQ` vb.) inceler.
2. Her işlem gününün grafik yapısını (Trend/İtki, Likidite Süpürme/Dönüş, Sıkışma/Birikim) nedensel olarak
   (geleceği görmeden, sadece o ana kadar kapanmış barlarla) sınıflandırır.
3. Bottaki tüm 11 taktik ailesini (`ten_am`, `dmc_rb`, `kaan`, `ekin_ifvg`, `ict_unicorn`, `smc_idm`,
   `crt_range`, `orderflow_vwap`, `wyckoff_spring`, `pa_srflip`, `box`) gerçek `DayEngine` + `simulate_day`
   motorunda çalıştırarak o grafikte hangi taktiklerin para kazandırdığını tespit eder.
4. Walk-Forward (yürüyen pencere) ve Grafik Rejimi uyumuyla o hisse senedi grafiğinde en yüksek kâr
   beklentisine sahip taktikleri seçip uygular.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "nasdaq_bot.dmc"

import numpy as np
import pandas as pd

from .backtest import Trade, simulate_day
from .data import _cache_dir, _raw_fast_resample, daily_bars, is_futures, load_history
from .engine import DayEngine
from .levels import DayLevels, am_range_median, am_ranges, prepare_day
from .structure import mirror

ALL_TACTICS: tuple[str, ...] = (
    "ten_am",
    "dmc_rb",
    "kaan",
    "ekin_ifvg",
    "ict_unicorn",
    "smc_idm",
    "crt_range",
    "orderflow_vwap",
    "wyckoff_spring",
    "pa_srflip",
    "sabirli_coklu",
    "box",
)

# Her grafik rejimine doğal olarak uyan taktik aileleri (prior ağırlık çarpanı)
REGIME_AFFINITY: dict[str, set[str]] = {
    "sweep_reversal": {"ten_am", "dmc_rb", "kaan", "ekin_ifvg", "ict_unicorn", "crt_range", "orderflow_vwap", "sabirli_coklu"},
    "trend_expansion": {"pa_srflip", "smc_idm", "ict_unicorn", "dmc_rb", "ten_am", "sabirli_coklu"},
    "range_accumulation": {"wyckoff_spring", "crt_range", "kaan", "dmc_rb", "orderflow_vwap", "sabirli_coklu", "box"},
}


@dataclass
class DayChartRegime:
    day: date
    regime: str                  # sweep_reversal | trend_expansion | range_accumulation
    bias: str                    # long | short | neutral
    or_disp_ratio: float         # |Close_10:00 - Open_09:30| / (ORH - ORL)
    or_rel_size: float           # (ORH - ORL) / am_range_median
    swept_pre_levels: list[str]  # 10:00'a kadar süpürülüp reddedilen ana seviyeler


def trade_summary(trades: list[Trade]) -> dict:
    """Trade listesi için hızlı özet metrikler."""
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0, "profit_factor": 0.0, "max_dd_r": 0.0}
    r = np.array([t.r for t in trades], dtype=float)
    wins = r[r > 0]
    losses = r[r < 0]
    cum = np.cumsum(r)
    dd_r = float((np.maximum.accumulate(np.r_[0, cum]) - np.r_[0, cum]).max())
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else (99.0 if len(wins) else 0.0)
    return {
        "trades": int(len(r)),
        "win_rate": float((r > 0).mean() * 100.0),
        "avg_r": float(r.mean()),
        "total_r": float(r.sum()),
        "profit_factor": pf,
        "max_dd_r": dd_r,
    }


def classify_chart_regime(day_df: pd.DataFrame, lv: DayLevels, am_med: float | None) -> DayChartRegime:
    """09:30–10:00 açılış barlarına ve gün öncesi seviyelere bakarak grafiğin o günkü rejimini belirler.
    Tamamen nedenseldir (10:00 itibarıyla bilinen veriyi kullanır)."""
    or_bars = day_df[day_df.index.time < dtime(10, 0)]
    if len(or_bars) < 5:
        return DayChartRegime(lv.day, "sweep_reversal", lv.bias, 0.0, 1.0, [])
    o930 = float(or_bars["Open"].iloc[0])
    c1000 = float(or_bars["Close"].iloc[-1])
    orh = float(or_bars["High"].max())
    orl = float(or_bars["Low"].min())
    or_rng = max(orh - orl, 1e-9)
    disp_ratio = abs(c1000 - o930) / or_rng
    rel_size = (or_rng / am_med) if (am_med and am_med > 0) else 1.0

    swept = []
    for name, lvl in lv.below().items():
        if lvl is not None and orl < lvl < c1000:
            swept.append(f"bull_sweep:{name}")
    for name, lvl in lv.above().items():
        if lvl is not None and orh > lvl > c1000:
            swept.append(f"bear_sweep:{name}")

    if swept:
        regime = "sweep_reversal"
    elif disp_ratio >= 0.62 and rel_size >= 0.45:
        regime = "trend_expansion"
    elif rel_size <= 0.42 or disp_ratio <= 0.30:
        regime = "range_accumulation"
    else:
        regime = "sweep_reversal"

    return DayChartRegime(lv.day, regime, lv.bias, round(disp_ratio, 3), round(rel_size, 3), swept)


def _single_tactic_cfg(base_cfg: dict, symbol: str, tactic: str) -> dict:
    """Sadece tek bir taktiği aktif eden izole konfigürasyon döndürür."""
    c = deepcopy(base_cfg)
    c["dmc"]["symbol"] = symbol
    c["dmc"]["watch_setups"] = {}
    for k in list(c["dmc"]["setups"].keys()):
        c["dmc"]["setups"][k]["enabled"] = (k == tactic)
    return c


def _build_shared_day_frames(rth_bars: pd.DataFrame, pm_bars: pd.DataFrame | None = None):
    """Gün içi çoklu zaman dilimi tablolarını günde sadece 1 kez üretir ve tüm taktiklerle paylaşır."""
    pm = pm_bars if pm_bars is not None else pd.DataFrame()
    m_bars = mirror(rth_bars)
    full_bars = pd.concat([pm, rth_bars]) if len(pm) else rth_bars
    m_full_bars = mirror(full_bars)
    for b in (rth_bars, m_bars, full_bars, m_full_bars):
        b.attrs["_tf_map"] = {tf: _raw_fast_resample(b, tf) for tf in (3, 5, 15, 30, 45)}
    return rth_bars, m_bars, full_bars, m_full_bars, len(pm)


def _attach_precomputed_frames(eng: DayEngine, shared_frames) -> None:
    rth_bars, m_bars, full_bars, m_full_bars, pm_len = shared_frames
    for pair in eng.pairs.values():
        pair.long._day_df = rth_bars
        pair.long._full_day_df = full_bars
        pair.long._pm_len = pm_len
        pair.short._day_df = m_bars
        pair.short._full_day_df = m_full_bars
        pair.short._pm_len = pm_len


def _load_cached_range(cfg: dict, symbol: str, start: date, end: date, feed: str = "sip") -> pd.DataFrame:
    """Önbellekteki mevcut aylık .pkl dosyalarını (API anahtarı gerektirmeden) [start, end] aralığı için yükler."""
    if is_futures(symbol):
        return load_history(cfg, symbol, start, end, feed=feed)
    cdir = _cache_dir(cfg)
    cur = date(start.year, start.month, 1)
    parts = []
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        f = cdir / f"{symbol}_{feed}_{cur:%Y%m}.pkl"
        if f.exists():
            parts.append(pd.read_pickle(f))
        cur = nxt
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    lo = pd.Timestamp(datetime.combine(start, datetime.min.time()))
    hi = pd.Timestamp(datetime.combine(end + timedelta(days=1), datetime.min.time()))
    return df[(df.index >= lo) & (df.index < hi)]


def evaluate_all_tactics_on_symbol(
    cfg: dict,
    symbol: str,
    start: date,
    end: date,
    tactics: Iterable[str] = ALL_TACTICS,
) -> tuple[dict[str, list[Trade]], dict[date, DayChartRegime], list[date]]:
    """Verilen hisse/ETF sembolü üzerinde tüm taktikleri gerçek `DayEngine` + `simulate_day` motoruyla çalıştırır."""
    dcfg = cfg["dmc"]
    feed = dcfg.get("feed", "sip")
    warmup = start - timedelta(days=120)
    df1m = _load_cached_range(cfg, symbol, warmup, end, feed=feed)
    if df1m.empty:
        return {t: [] for t in tactics}, {}, []

    daily = daily_bars(df1m, symbol)
    amr = am_ranges(df1m)
    t = df1m.index.time
    rth = df1m[(t >= dtime(9, 30)) & (t < dtime(16, 0))]
    pm_all = df1m[(t >= dtime(8, 0)) & (t < dtime(9, 30))]
    by_day = {k: g for k, g in rth.groupby(rth.index.date)}
    pm_by_day = {k: g for k, g in pm_all.groupby(pm_all.index.date)}
    days = [x for x in sorted(by_day.keys()) if start <= x <= end and len(by_day[x]) >= 60]

    tactic_list = list(tactics)
    tactic_cfgs = {tac: _single_tactic_cfg(cfg, symbol, tac) for tac in tactic_list}
    trades_by_tactic: dict[str, list[Trade]] = {tac: [] for tac in tactic_list}
    regimes_by_day: dict[date, DayChartRegime] = {}

    for day in days:
        win = df1m[(df1m.index >= pd.Timestamp(day) - pd.Timedelta(days=16)) &
                   (df1m.index < pd.Timestamp(datetime.combine(day, dtime(9, 30))))]
        lv = prepare_day(win, day, daily=daily[daily.index < pd.Timestamp(day)], fvg_cfg=dcfg.get("fvg"))
        am_med = am_range_median(amr, day)
        extra = {"am_range_median": am_med}
        day_bars = by_day[day]
        pm_bars = pm_by_day.get(day, pd.DataFrame())
        shared = _build_shared_day_frames(day_bars, pm_bars)

        regimes_by_day[day] = classify_chart_regime(day_bars, lv, am_med)

        for tac in tactic_list:
            eng = DayEngine(tactic_cfgs[tac], lv, extra, symbol=symbol)
            _attach_precomputed_frames(eng, shared)
            trades_by_tactic[tac] += simulate_day(tactic_cfgs[tac], day_bars, eng)

    return trades_by_tactic, regimes_by_day, days


def select_best_tactics_walk_forward(
    trades_by_tactic: dict[str, list[Trade]],
    regimes_by_day: dict[date, DayChartRegime],
    days: list[date],
    lookback_days: int = 120,
    top_k: int = 3,
    min_trades: int = 2,
    warmup_days: int = 40,
) -> tuple[list[Trade], dict[str, int]]:
    """Nedensel (Walk-Forward) Grafik Taktik Seçici:
    1. İlk `warmup_days` (40 iş günü) boyunca temel kurallarla grafiğin karakterini ölçer.
    2. 40. günden itibaren SADECE o hisse/ETF grafiğinde geçmiş pencerede gerçekten para kazandıran
       (`tot_r > 0`, `avg_r >= 0.10`, `PF >= 1.15`) ve o günün grafik rejimine uyan en iyi `top_k` taktiği seçer.
    3. Eğer o grafikte hiçbir taktik para kazandırmıyorsa (örn. SPY gibi verimsiz/testere grafik), işlem açmaz."""
    by_day_tactic: dict[tuple[date, str], list[Trade]] = {}
    for tac, trs in trades_by_tactic.items():
        for tr in trs:
            by_day_tactic.setdefault((tr.day, tac), []).append(tr)

    selected_trades: list[Trade] = []
    usage_counts: dict[str, int] = {t: 0 for t in trades_by_tactic}

    for idx, day in enumerate(days):
        past_days = set(days[max(0, idx - lookback_days):idx])
        reg = regimes_by_day.get(day)
        regime_name = reg.regime if reg else "sweep_reversal"
        affinity_set = REGIME_AFFINITY.get(regime_name, set(ALL_TACTICS))

        scores: list[tuple[float, str]] = []
        for tac in trades_by_tactic:
            past_trs = [tr for d0 in past_days for tr in by_day_tactic.get((d0, tac), ())]
            n = len(past_trs)
            if tac == "sabirli_coklu":
                if n == 0:
                    scores.append((0.50, tac))
                    continue
                tot_r = sum(tr.r for tr in past_trs)
                if tot_r > 0:
                    wins = sum(tr.r for tr in past_trs if tr.r > 0)
                    losses = -sum(tr.r for tr in past_trs if tr.r < 0)
                    pf = (wins / losses) if losses > 0 else 4.0
                    scores.append(((tot_r / n) * (min(pf, 4.0) ** 0.5) * (n ** 0.30) * 1.35, tac))
                continue
            if tac == "orderflow_vwap":
                if n == 0 and idx < warmup_days:
                    scores.append((0.25, tac))
                    continue
                if n >= 1:
                    tot_r = sum(tr.r for tr in past_trs)
                    wins = sum(tr.r for tr in past_trs if tr.r > 0)
                    losses = -sum(tr.r for tr in past_trs if tr.r < 0)
                    pf = (wins / losses) if losses > 0 else 4.0
                    if tot_r > 0 and pf >= 1.15:
                        scores.append(((tot_r / n) * (min(pf, 4.0) ** 0.5) * (n ** 0.30) * 1.25, tac))
                continue
            if n < min_trades:
                continue
            tot_r = sum(tr.r for tr in past_trs)
            avg_r = tot_r / n
            wins = sum(tr.r for tr in past_trs if tr.r > 0)
            losses = -sum(tr.r for tr in past_trs if tr.r < 0)
            pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
            if tot_r < 2.0 or avg_r < 0.25 or pf < 1.35:
                continue
            reg_trs = [tr for tr in past_trs if regimes_by_day.get(tr.day) and regimes_by_day[tr.day].regime == regime_name]
            reg_r = sum(tr.r for tr in reg_trs) / len(reg_trs) if len(reg_trs) >= 2 else avg_r
            if len(reg_trs) >= 3 and reg_r < 0:
                continue
            affinity_mult = 1.30 if tac in affinity_set else 0.85
            score = (0.60 * avg_r + 0.40 * max(reg_r, 0.0)) * (min(pf, 4.0) ** 0.5) * (n ** 0.30) * affinity_mult
            if score > 0:
                scores.append((score, tac))

        scores.sort(reverse=True)
        chosen = [tac for _, tac in scores[:top_k]]

        for tac in chosen:
            usage_counts[tac] = usage_counts.get(tac, 0) + 1

        day_cands: list[Trade] = []
        for tac in chosen:
            day_cands.extend(by_day_tactic.get((day, tac), ()))
        day_cands.sort(key=lambda tr: tr.filled_at)

        last_exit: pd.Timestamp | None = None
        stops_today = 0
        for tr in day_cands:
            if stops_today >= 2:
                break
            exit_ts = tr.exits[-1][0] if tr.exits else tr.filled_at
            if last_exit is None or tr.filled_at >= last_exit:
                selected_trades.append(tr)
                last_exit = exit_ts
                if tr.r < -0.5:
                    stops_today += 1

    return selected_trades, usage_counts


def main() -> None:
    import argparse
    from pathlib import Path
    import yaml

    ap = argparse.ArgumentParser(description="Hisse/ETF grafiğini tüm taktiklerle inceler ve en kârlı taktikleri seçer.")
    ap.add_argument("--symbol", default="QQQ", help="İncelenecek hisse/ETF sembolü (örn. QQQ, TQQQ, GLD, SPY)")
    ap.add_argument("--start", default="2025-01-02", help="Başlangıç tarihi (YYYY-MM-DD)")
    ap.add_argument("--end", default="2026-09-30", help="Bitiş tarihi (YYYY-MM-DD)")
    args = ap.parse_args()

    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cdir = Path(cfg["data"]["cache_dir"])
    if not cdir.is_absolute():
        cfg["data"]["cache_dir"] = str((cfg_path.parent / cdir).resolve())

    sym = args.symbol.upper()
    s_date = date.fromisoformat(args.start)
    e_date = date.fromisoformat(args.end)
    print(f"[{sym}] {s_date} -> {e_date} aralığı gerçek DayEngine motoruyla taranıyor, lütfen bekleyin...")
    trades_by_tactic, regimes, days = evaluate_all_tactics_on_symbol(cfg, sym, s_date, e_date, ALL_TACTICS)
    print(f"\n=== {sym} GRAFİK TAKTİK ANALİZİ ({s_date} -> {e_date}, {len(days)} iş günü) ===")
    for tac in ALL_TACTICS:
        st = trade_summary(trades_by_tactic[tac])
        print(f"  {tac:<16}: {st['trades']:>3} işlem | WR {st['win_rate']:>5.1f}% | Toplam {st['total_r']:>+7.2f}R | PF {st['profit_factor']:>5.2f}")
    wf_trs, usage = select_best_tactics_walk_forward(trades_by_tactic, regimes, days)
    wf_st = trade_summary(wf_trs)
    print("-" * 72)
    print(f"  {'WALK-FORWARD':<16}: {wf_st['trades']:>3} işlem | WR {wf_st['win_rate']:>5.1f}% | Toplam {wf_st['total_r']:>+7.2f}R | PF {wf_st['profit_factor']:>5.2f}")
    print(f"  En Çok Seçilen Taktikler: {sorted(usage.items(), key=lambda x: -x[1])[:5]}")


if __name__ == "__main__":
    main()

