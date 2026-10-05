"""Portföy düzeyinde gerçekçi backtest.

Kurallar (canlı botla birebir aynı):
  * Sinyal t günü kapanışında hesaplanır, emir t+1 günü LİMİT (kapanış x (1+gap)) ile girer.
  * Stop / hedef fiyatları sinyal anında sabitlenir (bracket emir gibi).
  * Açılışta gap stop'un altındaysa açılıştan çıkılır (gerçekçi kayıp).
  * Aynı gün hem stop hem hedef görülürse önce STOP varsayılır (muhafazakâr).
  * Komisyon + kayma her iki yönde düşülür.
Sınırlar: geçmiş haber/analist verisi olmadığı için yalnızca teknik + rejim bileşeni test edilir;
güncel NASDAQ-100 listesi kullanıldığından hayatta kalma yanlılığı (survivorship bias) vardır.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .strategy import analyze_frame, regime_series
from .weights import load_weights


def run_backtest(data: dict[str, pd.DataFrame], cfg: dict, years: float) -> dict:
    s, r, b = cfg["strategy"], cfg["risk"], cfg["backtest"]
    cost = (b["commission_bps"] + b["slippage_bps"]) / 1e4
    qqq, vix = data.pop("QQQ", None), data.pop("^VIX", None)
    regime = regime_series(qqq, vix)

    frames = {}
    w1d = load_weights(cfg, "1d")
    for t, df in data.items():
        if len(df) < 220:
            continue
        d, _ = analyze_frame(df, s, w1d)
        frames[t] = d[["Open", "High", "Low", "Close", "atr", "atr_pct", "dollar_vol20", "tech_score"]]
    if not frames:
        raise RuntimeError("Backtest için yeterli veri yok.")

    dates = sorted(set().union(*[f.index for f in frames.values()]))
    dates = pd.DatetimeIndex(dates)
    start = dates[-1] - pd.Timedelta(days=int(365 * years))
    dates = dates[dates >= start]
    tick = list(frames)
    A = {k: np.column_stack([frames[t][k].reindex(dates).to_numpy(float) for t in tick])
         for k in ["Open", "High", "Low", "Close", "atr", "atr_pct", "dollar_vol20", "tech_score"]}
    mult = regime.reindex(dates).ffill().fillna(1.0).to_numpy() if len(regime) else np.ones(len(dates))

    cash = float(b["initial_capital"])
    positions: dict[int, dict] = {}
    pending: list[dict] = []
    trades, equity_curve, n_pos = [], [], []
    last_px = np.full(len(tick), np.nan)

    def close_pos(j: int, px: float, i: int, reason: str):
        nonlocal cash
        p = positions.pop(j)
        exit_px = px * (1 - cost)
        cash += p["shares"] * exit_px
        trades.append({
            "ticker": tick[j], "entry_date": dates[p["i"]].date(), "exit_date": dates[i].date(),
            "entry": p["entry"], "exit": exit_px, "ret_pct": (exit_px / p["entry"] - 1) * 100,
            "pnl": p["shares"] * (exit_px - p["entry"]), "days": i - p["i"] + 1, "reason": reason,
        })

    for i in range(len(dates)):
        O, H, L, C = A["Open"][i], A["High"][i], A["Low"][i], A["Close"][i]
        valid_c = ~np.isnan(C)
        last_px[valid_c] = C[valid_c]

        # 1) Eski pozisyonlarda açılış gap kontrolü
        for j in list(positions):
            p = positions[j]
            if np.isnan(O[j]):
                continue
            if O[j] <= p["stop"]:
                close_pos(j, O[j], i, "gap-stop")
            elif O[j] >= p["target"]:
                close_pos(j, O[j], i, "gap-hedef")

        # 2) Bekleyen limit emirleri
        equity_open = cash + sum(p["shares"] * last_px[j] for j, p in positions.items())
        for o in pending:
            j = o["j"]
            if j in positions or len(positions) >= r["max_positions"] or np.isnan(O[j]):
                continue
            if O[j] <= o["limit"]:
                fill = O[j]
            elif L[j] <= o["limit"]:
                fill = o["limit"]
            else:
                continue  # fiyat kaçtı, kovalamıyoruz
            if fill <= o["stop"]:
                continue
            entry = fill * (1 + cost)
            risk_sh = equity_open * r["risk_per_trade_pct"] / 100 / max(entry - o["stop"], 1e-9)
            cap_sh = equity_open * r["max_position_pct"] / 100 / entry
            shares = min(risk_sh, cap_sh, cash / entry)
            if shares * entry < 100:
                continue
            cash -= shares * entry
            positions[j] = {"shares": shares, "entry": entry, "stop": o["stop"], "target": o["target"], "i": i}
        pending = []

        # 3) Gün içi stop / hedef / zaman stopu
        for j in list(positions):
            p = positions[j]
            if np.isnan(L[j]):
                continue
            if L[j] <= p["stop"]:
                close_pos(j, p["stop"], i, "stop")
            elif H[j] >= p["target"]:
                close_pos(j, p["target"], i, "hedef")
            elif i - p["i"] + 1 >= s["max_hold_days"]:
                close_pos(j, C[j], i, "zaman")

        eq = cash + sum(p["shares"] * last_px[j] for j, p in positions.items())
        equity_curve.append(eq)
        n_pos.append(len(positions))

        # 4) Yarın için sinyaller
        free = r["max_positions"] - len(positions)
        if free > 0 and i < len(dates) - 1:
            score = A["tech_score"][i] * mult[i]
            ok = ((score >= s["buy_threshold"]) & (A["dollar_vol20"][i] >= s["min_avg_dollar_volume"])
                  & (A["atr_pct"][i] >= s["min_atr_pct"]) & (A["atr_pct"][i] <= s["max_atr_pct"]))
            ok &= ~np.isnan(score)
            for j in positions:
                ok[j] = False
            idx = np.where(ok)[0]
            idx = idx[np.argsort(-score[idx])][:free]
            for j in idx:
                c, a = C[j], A["atr"][i][j]
                tgt = max(s["target_atr_mult"] * a, c * s["min_target_pct"] / 100)
                pending.append({"j": j, "limit": c * (1 + s["max_entry_gap_pct"] / 100),
                                "stop": c - s["stop_atr_mult"] * a, "target": c + tgt})

    equity = pd.Series(equity_curve, index=dates, name="equity")
    bench = None
    if qqq is not None:
        q = qqq["Close"].reindex(dates).ffill().bfill()
        bench = q / q.iloc[0] * b["initial_capital"]
    return {"equity": equity, "trades": pd.DataFrame(trades), "benchmark": bench,
            "stats": compute_stats(equity, pd.DataFrame(trades), r["daily_profit_target_pct"],
                                   pd.Series(n_pos, index=dates), bench)}


def compute_stats(equity: pd.Series, trades: pd.DataFrame, target_pct: float,
                  n_pos: pd.Series, bench: pd.Series | None) -> dict:
    ret = equity.pct_change().dropna()
    n = len(ret)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    st = {
        "Başlangıç": equity.index[0].date(), "Bitiş": equity.index[-1].date(), "İşlem günü": n,
        "Toplam getiri %": total * 100,
        "Yıllık getiri (CAGR) %": ((1 + total) ** (252 / max(n, 1)) - 1) * 100,
        "Sharpe": (ret.mean() / ret.std() * math.sqrt(252)) if ret.std() > 0 else 0.0,
        "Maks. düşüş %": (equity / equity.cummax() - 1).min() * 100,
        "Ort. günlük getiri %": ret.mean() * 100,
        f"Günlerin %{target_pct:g}+ kârla kapandığı oran %": (ret >= target_pct / 100).mean() * 100,
        "Zararlı gün oranı %": (ret < 0).mean() * 100,
        "En iyi gün %": ret.max() * 100, "En kötü gün %": ret.min() * 100,
        "Pozisyonda geçen gün %": (n_pos > 0).mean() * 100,
    }
    if bench is not None:
        st["QQQ al-tut getiri %"] = (bench.iloc[-1] / bench.iloc[0] - 1) * 100
    if len(trades):
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        st.update({
            "İşlem sayısı": len(trades),
            "Kazanma oranı %": len(wins) / len(trades) * 100,
            "Ort. işlem getirisi %": trades["ret_pct"].mean(),
            "Kâr faktörü": wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) and losses["pnl"].sum() else float("inf"),
            "Ort. tutma (gün)": trades["days"].mean(),
            "Çıkış nedenleri": trades["reason"].value_counts().to_dict(),
        })
    return st
