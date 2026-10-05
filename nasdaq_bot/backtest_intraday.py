"""Gün içi long/short backtest (5 dk mumlar, Yahoo'nun izin verdiği son ~60 gün).

Canlı motorla aynı kurallar: sinyal mum kapanışında, giriş sonraki mumun açılışında (piyasa emri),
stop/hedef sinyal fiyatına göre sabit, aynı mumda ikisi birden görülürse önce STOP,
ters sinyalde çıkış, +%1 / -%2 günlük kilit, kapanışa 10 dk kala tüm pozisyonlar kapatılır.
Haber/analist verisinin geçmişi olmadığı için yalnızca teknik + rejim kısmı test edilir.
"""
from __future__ import annotations

import math
from datetime import time as dtime

import numpy as np
import pandas as pd

from .data import download_ohlcv
from .intraday import (build_intraday, daily_scores, pick_side, prev_day_values, qqq_vwap_flag,
                       session_only)
from .strategy import regime_series, regime_short_series
from .weights import load_weights


def _minutes(t: dtime) -> int:
    return t.hour * 60 + t.minute


def run_intraday_backtest(cfg: dict, tickers: list[str], progress=None) -> dict:
    li, r, b, s = cfg["live"], cfg["risk"], cfg["backtest"], cfg["strategy"]
    dc = cfg["data"]
    cost = (b["commission_bps"] + b["slippage_bps"]) / 1e4

    intr = download_ohlcv(tickers + ["QQQ"], period="60d", interval="5m",
                          cache_dir=dc["cache_dir"], ttl_minutes=dc["cache_ttl_minutes"])
    daily = download_ohlcv(tickers + ["QQQ", "^VIX"], period="2y", interval="1d",
                           cache_dir=dc["cache_dir"], ttl_minutes=dc["cache_ttl_minutes"])
    if "QQQ" not in intr:
        raise RuntimeError("QQQ 5 dk verisi alınamadı.")
    qqq5 = session_only(intr.pop("QQQ"))
    qflag = qqq_vwap_flag(qqq5)
    timeline = qqq5.index

    regime = pd.DataFrame({"rl": regime_series(daily.get("QQQ"), daily.get("^VIX")),
                           "rs": regime_short_series(daily.get("QQQ"))})
    reg_bar = prev_day_values(regime, timeline).fillna(1.0)
    ml, ms = reg_bar["rl"].to_numpy(), reg_bar["rs"].to_numpy()

    frames = {}
    w1d, w5m = load_weights(cfg, "1d"), load_weights(cfg, "5m")
    for t in tickers:
        if t not in intr or t not in daily or len(daily[t]) < 220:
            continue
        df5 = session_only(intr[t])
        if len(df5) < 300:
            continue
        f, _ = build_intraday(df5, daily_scores(daily[t], s, w1d), qflag, li, w5m)
        frames[t] = f.reindex(timeline)
        if progress:
            progress(t)
    tick = list(frames)
    if not tick:
        raise RuntimeError("Yeterli gün içi veri yok.")
    A = {k: np.column_stack([frames[t][k].to_numpy(float) for t in tick])
         for k in ["Open", "High", "Low", "Close", "L", "S", "d_atr", "d_atr_pct", "d_dvol"]}

    E = float(b["initial_capital"])
    pos: dict[int, dict] = {}
    trades, daily_rows = [], []
    first_ok = 9 * 60 + 30 + li["no_entry_first_minutes"]

    def unreal(i: int) -> float:
        tot = 0.0
        for j, p in pos.items():
            px = A["Close"][i][j]
            if np.isnan(px):
                px = p["last"]
            p["last"] = px
            tot += p["sign"] * p["qty"] * (px - p["entry"])
        return tot

    def close(j: int, px: float, i: int, reason: str):
        nonlocal E
        p = pos.pop(j)
        fill = px * (1 - cost * p["sign"])
        pnl = p["sign"] * p["qty"] * (fill - p["entry"])
        E += pnl
        trades.append({"ticker": tick[j], "side": "long" if p["sign"] > 0 else "short",
                       "entry_time": timeline[p["i"]], "exit_time": timeline[i], "entry": p["entry"],
                       "exit": fill, "ret_pct": p["sign"] * (fill / p["entry"] - 1) * 100, "pnl": pnl,
                       "reason": reason})

    days = timeline.normalize()
    for day in days.unique():
        idx = np.where(days == day)[0]
        start_eq = E
        locked, trades_today, pending = False, 0, []
        cooldown: dict[int, int] = {}
        last_bar_min = _minutes(timeline[idx[-1]].time())
        close_min = last_bar_min + 5  # yarım günleri de kapsar
        flat_min = close_min - li["flatten_minutes_before_close"] - 5
        last_entry_min = close_min - li["no_entry_last_minutes"] - 5

        for k, i in enumerate(idx):
            O, H, Lo, C = A["Open"][i], A["High"][i], A["Low"][i], A["Close"][i]
            m = _minutes(timeline[i].time())

            # 1) bekleyen emirler bu mumun açılışında dolar
            for o in pending:
                j = o["j"]
                if j in pos or np.isnan(O[j]) or len(pos) >= li["max_positions"]:
                    continue
                sign = 1 if o["side"] == "long" else -1
                if (sign > 0 and O[j] <= o["stop"]) or (sign < 0 and O[j] >= o["stop"]):
                    continue  # gap stopun ötesinde açıldı -> emir anlamsız
                fill = O[j] * (1 + cost * sign)
                eq_now = E + unreal(i)
                per = abs(fill - o["stop"])
                gross = sum(p["qty"] * p["last"] for p in pos.values())
                qty = min(eq_now * li["risk_per_trade_pct"] / 100 / per,
                          eq_now * li["max_position_pct"] / 100 / fill,
                          max(0.0, eq_now * li["max_gross_exposure"] - gross) / fill)
                if qty * fill < 100:
                    continue
                pos[j] = {"sign": sign, "qty": qty, "entry": fill, "stop": o["stop"], "target": o["target"],
                          "i": i, "last": fill}
                trades_today += 1
            pending = []

            # 2) stop / hedef
            for j in list(pos):
                p = pos[j]
                if np.isnan(Lo[j]):
                    continue
                if p["sign"] > 0:
                    if Lo[j] <= p["stop"]:
                        close(j, min(O[j], p["stop"]), i, "stop")
                    elif H[j] >= p["target"]:
                        close(j, max(O[j], p["target"]), i, "hedef")
                else:
                    if H[j] >= p["stop"]:
                        close(j, max(O[j], p["stop"]), i, "stop")
                    elif Lo[j] <= p["target"]:
                        close(j, min(O[j], p["target"]), i, "hedef")

            # 3) günlük kilit / kapanış
            eq = E + unreal(i)
            pnl = eq / start_eq - 1
            if not locked and (pnl >= r["daily_profit_target_pct"] / 100 or pnl <= -r["daily_max_loss_pct"] / 100):
                for j in list(pos):
                    close(j, pos[j]["last"], i, "günlük-kilit")
                locked = True
            if m >= flat_min or k == len(idx) - 1:
                for j in list(pos):
                    close(j, pos[j]["last"], i, "kapanış")
                locked = True
            if locked:
                continue

            # 4) ters sinyalde çıkış
            if li.get("reversal_exit", True):
                for j in list(pos):
                    p = pos[j]
                    own, opp = (A["L"][i][j], A["S"][i][j]) if p["sign"] > 0 else (A["S"][i][j], A["L"][i][j])
                    if not np.isnan(opp) and opp >= li["threshold"] and opp - own >= li["min_margin"]:
                        close(j, C[j], i, "ters-sinyal")
                        cooldown[j] = i

            # 5) yeni sinyaller (sonraki mumun açılışında girilecek)
            if m + 5 < first_ok or m > last_entry_min or trades_today >= li["max_trades_per_day"]:
                continue
            free = li["max_positions"] - len(pos)
            if free <= 0:
                continue
            ok = ((A["d_dvol"][i] >= s["min_avg_dollar_volume"]) & (A["d_atr_pct"][i] >= li["min_daily_atr_pct"])
                  & (A["d_atr_pct"][i] <= li["max_daily_atr_pct"]) & ~np.isnan(A["L"][i]) & ~np.isnan(C))
            cands = []
            for j in np.where(ok)[0]:
                if j in pos or (j in cooldown and (i - cooldown[j]) * 5 < li["cooldown_minutes"]):
                    continue
                side, sc = pick_side(A["L"][i][j], A["S"][i][j], li, ml[i], ms[i])
                if side:
                    cands.append((sc, j, side))
            cands.sort(reverse=True)
            for sc, j, side in cands[:free]:
                c, a = C[j], A["d_atr"][i][j]
                tgt = max(li["target_daily_atr"] * a, c * li["min_target_pct"] / 100)
                stp = li["stop_daily_atr"] * a
                if side == "long":
                    pending.append({"j": j, "side": side, "stop": c - stp, "target": c + tgt})
                else:
                    pending.append({"j": j, "side": side, "stop": c + stp, "target": c - tgt})
                cooldown[j] = i

        daily_rows.append({"day": day.date(), "start": start_eq, "end": E, "ret_pct": (E / start_eq - 1) * 100})

    dd = pd.DataFrame(daily_rows).set_index("day")
    tr = pd.DataFrame(trades)
    return {"daily": dd, "trades": tr, "stats": _stats(dd, tr, r["daily_profit_target_pct"], b["initial_capital"],
                                                      qqq5)}


def _stats(dd: pd.DataFrame, tr: pd.DataFrame, target: float, initial: float, qqq5: pd.DataFrame) -> dict:
    ret = dd["ret_pct"] / 100
    eq = dd["end"]
    st = {
        "Başlangıç": dd.index[0], "Bitiş": dd.index[-1], "İşlem günü": len(dd),
        "Toplam getiri %": (eq.iloc[-1] / initial - 1) * 100,
        "Ort. günlük getiri %": ret.mean() * 100,
        f"%{target:g}+ kârlı gün oranı %": (ret >= target / 100 * 0.999).mean() * 100,
        "Kârlı gün oranı %": (ret > 0).mean() * 100,
        "Zararlı gün oranı %": (ret < 0).mean() * 100,
        "İşlemsiz gün": int((ret == 0).sum()),
        "En iyi gün %": ret.max() * 100, "En kötü gün %": ret.min() * 100,
        "Maks. düşüş %": ((eq / eq.cummax()) - 1).min() * 100,
        "Sharpe (yıllık)": ret.mean() / ret.std() * math.sqrt(252) if ret.std() > 0 else 0.0,
        "QQQ aynı dönem %": (qqq5["Close"].iloc[-1] / qqq5["Open"].iloc[0] - 1) * 100,
    }
    if len(tr):
        for side in ("long", "short"):
            t = tr[tr["side"] == side]
            if len(t):
                st[f"{side.upper()} işlem / kazanma % / ort. %"] = (
                    f"{len(t)} / {(t['pnl'] > 0).mean() * 100:.1f} / {t['ret_pct'].mean():+.2f}")
        w, l_ = tr[tr["pnl"] > 0], tr[tr["pnl"] <= 0]
        st["Toplam işlem"] = len(tr)
        st["Kazanma oranı %"] = len(w) / len(tr) * 100
        st["Kâr faktörü"] = w["pnl"].sum() / abs(l_["pnl"].sum()) if l_["pnl"].sum() else float("inf")
        st["Çıkış nedenleri"] = tr["reason"].value_counts().to_dict()
    return st
