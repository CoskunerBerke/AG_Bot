"""Hafta Sonu (ve Seans Sonu) Antrenman & Öz-Öğrenme Motoru (`weekend_learner.py`).

Piyasa kapalıyken (özellikle Cumartesi-Pazar hafta sonları ve Cuma kapanış sonrası) boş beklemek yerine:
1. En güncel 1 dakikalık piyasa verilerini (ör. son kapanan seans `16.10.2026` ve son 20 iş günü) indirir.
2. Tüm semboller (`QQQ`, `TQQQ`, `GLD`, `SPY`, `DIA`, `SLV`) üzerinde hem aktif bot kurulumuyla hem de
   taktik kütüphanesindeki modellerle (`sabirli_coklu`, `dmc_rb`, `ten_am`, `kaan`, `orderflow_vwap`, `crt_range`)
   sanal işlemler (paper-replay trade) atar.
3. Hangi sembolde hangi taktiğin güncel piyasa rejiminde (son gün + son hafta + son 20 iş günü) kazandığını
   veya stop olduğunu analiz eder; aşırı uyuma (overfitting) düşmeyen Bayesçi ağırlıklandırma ile
   Pazartesi seansı için sembol bazlı taktik önceliklerini (`learned_priority`) ve rejim notlarını
   `state/weekend_learning.json` dosyasına kaydeder.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

from ..common import NY
from .backtest import run_backtest, stats
from .data import load_history
from .levels import am_range_median, am_ranges

log = logging.getLogger("dmc.learner")

CORE_TACTICS = (
    "sabirli_coklu", "dmc_rb", "ten_am", "kaan",
    "orderflow_vwap", "crt_range", "ekin_ifvg", "ict_unicorn", "smc_idm", "wyckoff_spring", "pa_srflip"
)

# 4 yıllık (2020-2026) kör testten gelen sarsılmaz taban beklentisi (aşırı uyumu önleyen Prior)
PRIOR_AVG_R = {
    "sabirli_coklu": 1.24,
    "dmc_rb": 0.45,
    "ten_am": 0.25,
    "kaan": 0.30,
    "orderflow_vwap": 0.65,
    "crt_range": 0.35,
    "ekin_ifvg": 0.10,
    "ict_unicorn": 0.10,
    "smc_idm": 0.15,
    "wyckoff_spring": 0.15,
    "pa_srflip": 0.10,
}


def _isolated_cfg(base_cfg: dict, symbol: str, tactic: str) -> dict:
    c = copy.deepcopy(base_cfg)
    c["dmc"]["symbol"] = symbol
    c["dmc"]["watch_only"] = []
    c["dmc"]["watch_setups"] = {}
    for k in c["dmc"]["setups"]:
        en = (k == tactic)
        c["dmc"]["setups"][k]["enabled"] = en
        if not en:
            c["dmc"]["setups"][k]["smt"] = "off"
    return c


def run_weekend_learning(cfg: dict, now_dt: datetime | pd.Timestamp | None = None) -> dict:
    """En son kapanan işlem günü (ör. 16.10.2026) ve son 20 iş günü verisiyle simülasyon işlemleri atar,
    sonuçlardan sembol bazlı taktik performansını ve Pazartesi öncelik puanlarını öğrenip kaydeder.
    """
    if now_dt is None:
        now_ny = datetime.now(NY)
    elif isinstance(now_dt, pd.Timestamp):
        now_ny = now_dt.to_pydatetime()
    else:
        now_ny = now_dt

    end_date = now_ny.date()
    start_fetch = end_date - timedelta(days=40)
    syms = cfg["dmc"].get("symbols") or [cfg["dmc"]["symbol"]]

    last_session_day: date | None = None
    last_day_trades: list[dict] = []
    week_trades: list[dict] = []
    symbol_learnings: dict[str, dict] = {}
    learned_priorities: dict[str, dict[str, int]] = {}

    for sym in syms:
        try:
            df = load_history(cfg, sym, start_fetch, end_date)
        except Exception as e:
            log.warning("Hafta sonu öğrenme verisi alınamadı (%s): %s", sym, e)
            continue
        if df.empty:
            continue

        t = df.index.time
        rth = df[(t >= dtime(9, 30)) & (t < dtime(16, 0))]
        rth_days = sorted(set(rth.index.date))
        if not rth_days:
            continue

        sym_last_day = rth_days[-1]
        if last_session_day is None or sym_last_day > last_session_day:
            last_session_day = sym_last_day

        week_days = rth_days[-5:] if len(rth_days) >= 5 else rth_days
        month_days = rth_days[-20:] if len(rth_days) >= 20 else rth_days
        w_start = week_days[0]
        m_start = month_days[0]

        # 1) Aktif Bot C kurulumu ile son 20 günü (ve son günü) simüle et
        active_cfg = copy.deepcopy(cfg)
        active_cfg["dmc"]["symbol"] = sym
        for k in active_cfg["dmc"]["setups"]:
            if not active_cfg["dmc"]["setups"][k].get("enabled"):
                active_cfg["dmc"]["setups"][k]["smt"] = "off"
        try:
            tr_active = run_backtest(active_cfg, df, m_start, sym_last_day)
        except Exception as e:
            log.warning("Aktif backtest hatası (%s): %s", sym, e)
            tr_active = pd.DataFrame()

        if not tr_active.empty:
            for _, row in tr_active.iterrows():
                item = {
                    "day": str(row["day"]),
                    "time": pd.Timestamp(row["filled_at"]).strftime("%H:%M") if pd.notna(row.get("filled_at")) else "",
                    "symbol": sym,
                    "setup": str(row["setup"]),
                    "side": str(row["side"]),
                    "entry": round(float(row["entry"]), 2),
                    "stop": round(float(row["stop"]), 2),
                    "r": round(float(row["r"]), 2),
                    "exit": str(row.get("exit_reason", "")),
                }
                if row["day"] == sym_last_day:
                    last_day_trades.append(item)
                if row["day"] >= w_start:
                    week_trades.append(item)

        # 2) Her taktiği tek tek son 20 iş gününde yarıştır ve öğren
        tactic_evals: dict[str, dict] = {}
        for tac in CORE_TACTICS:
            if tac not in cfg["dmc"]["setups"]:
                continue
            icfg = _isolated_cfg(cfg, sym, tac)
            try:
                tr_t = run_backtest(icfg, df, m_start, sym_last_day)
            except Exception:
                continue

            st_m = stats(tr_t, icfg)
            tr_w = tr_t[tr_t["day"] >= w_start] if not tr_t.empty else pd.DataFrame()
            tr_d = tr_t[tr_t["day"] == sym_last_day] if not tr_t.empty else pd.DataFrame()

            m_n = int(st_m.get("n", 0))
            m_r = round(float(st_m.get("total_r", 0.0)), 2)
            m_wr = round(float(st_m.get("win_rate", 0.0)) * 100, 1)
            w_n = int(len(tr_w))
            w_r = round(float(tr_w["r"].sum()), 2) if w_n else 0.0
            d_n = int(len(tr_d))
            d_r = round(float(tr_d["r"].sum()), 2) if d_n else 0.0

            # Son günde aktif botta yer almayan ama kütüphanedeki taktiğin yakaladığı işlemleri de kaydet
            if not tr_d.empty:
                for _, row in tr_d.iterrows():
                    cand = {
                        "day": str(row["day"]),
                        "time": pd.Timestamp(row["filled_at"]).strftime("%H:%M") if pd.notna(row.get("filled_at")) else "",
                        "symbol": sym,
                        "setup": tac,
                        "side": str(row["side"]),
                        "entry": round(float(row["entry"]), 2),
                        "stop": round(float(row["stop"]), 2),
                        "r": round(float(row["r"]), 2),
                        "exit": str(row.get("exit_reason", "")),
                    }
                    if not any(
                        x["symbol"] == cand["symbol"] and x["setup"] == cand["setup"] and x["time"] == cand["time"]
                        for x in last_day_trades
                    ):
                        last_day_trades.append(cand)
                    if not any(
                        x["day"] == cand["day"] and x["symbol"] == cand["symbol"] and x["setup"] == cand["setup"] and x["time"] == cand["time"]
                        for x in week_trades
                    ):
                        week_trades.append(cand)

            # Bayesçi Öğrenme Puanı: 4 yıllık taban (Prior) + Son 20 gün + Son 5 gün
            prior = PRIOR_AVG_R.get(tac, 0.30)
            avg_m = (m_r / m_n) if m_n > 0 else 0.0
            avg_w = (w_r / w_n) if w_n > 0 else avg_m
            bayes_score = round(0.50 * prior + 0.30 * avg_m + 0.20 * avg_w + 0.05 * w_r, 3)

            tactic_evals[tac] = {
                "month_n": m_n,
                "month_r": m_r,
                "month_wr": m_wr,
                "week_n": w_n,
                "week_r": w_r,
                "last_day_n": d_n,
                "last_day_r": d_r,
                "learned_score": bayes_score,
            }

        ranked = sorted(tactic_evals.items(), key=lambda kv: kv[1]["learned_score"], reverse=True)
        best_tac = ranked[0][0] if ranked else "sabirli_coklu"
        best_info = ranked[0][1] if ranked else {}

        prio_For_sym: dict[str, int] = {}
        for idx_rank, (tac_name, info) in enumerate(ranked):
            if idx_rank == 0 and info["learned_score"] > 0.35:
                prio_For_sym[tac_name] = 3
            elif idx_rank <= 2 and info["learned_score"] > 0.15:
                prio_For_sym[tac_name] = 2
            else:
                prio_For_sym[tac_name] = 1
        learned_priorities[sym] = prio_For_sym

        amr = am_ranges(df)
        am_med = round(float(am_range_median(amr, sym_last_day) or 0.0), 2)

        st_act = stats(tr_active, active_cfg)
        symbol_learnings[sym] = {
            "last_day": str(sym_last_day),
            "am_range_median": am_med,
            "active_month_n": int(st_act.get("n", 0)),
            "active_month_r": round(float(st_act.get("total_r", 0.0)), 2),
            "active_month_wr": round(float(st_act.get("win_rate", 0.0)) * 100, 1),
            "best_tactic": best_tac,
            "best_tactic_month_r": best_info.get("month_r", 0.0),
            "best_tactic_week_r": best_info.get("week_r", 0.0),
            "best_tactic_wr": best_info.get("month_wr", 0.0),
            "tactics": tactic_evals,
        }

    last_day_trades.sort(key=lambda x: (x["day"], x["time"], x["symbol"]))
    week_trades.sort(key=lambda x: (x["day"], x["time"], x["symbol"]), reverse=True)

    last_day_r = round(sum(t["r"] for t in last_day_trades), 2)
    last_day_wins = sum(1 for t in last_day_trades if t["r"] > 0)
    week_r = round(sum(t["r"] for t in week_trades), 2)
    week_wins = sum(1 for t in week_trades if t["r"] > 0)

    doc = {
        "updated_ny": now_ny.strftime("%Y-%m-%d %H:%M:%S"),
        "last_session_day": str(last_session_day or ""),
        "last_day_n": len(last_day_trades),
        "last_day_wins": last_day_wins,
        "last_day_r": last_day_r,
        "week_n": len(week_trades),
        "week_wins": week_wins,
        "week_r": week_r,
        "last_day_trades": last_day_trades,
        "week_trades": week_trades[:25],
        "symbols": symbol_learnings,
        "learned_priorities": learned_priorities,
    }

    save_weekend_learning(cfg.get("state_dir", "state"), doc)
    return doc


def save_weekend_learning(state_dir: str | Path, doc: dict) -> None:
    try:
        p = Path(state_dir) / "weekend_learning.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        log.warning("Hafta sonu öğrenme dosyası yazılamadı: %s", e)


def load_weekend_learning(state_dir: str | Path) -> dict | None:
    for rel in ("c/weekend_learning.json", "weekend_learning.json"):
        p = Path(state_dir) / rel
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def apply_learned_priorities(cfg: dict, state_dir: str | Path, symbol: str) -> dict:
    """Pazartesi canlı seans başlarken hafta sonu öğrenilen sembol bazlı taktik önceliklerini (`priority`) uygular."""
    doc = load_weekend_learning(state_dir)
    if not doc:
        return cfg
    prio_map = (doc.get("learned_priorities") or {}).get(symbol.upper())
    if not prio_map:
        return cfg
    out = copy.deepcopy(cfg)
    for tac, prio in prio_map.items():
        if tac in out["dmc"]["setups"]:
            out["dmc"]["setups"][tac]["priority"] = int(prio)
    return out
