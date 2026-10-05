"""Rejection Block Mükemmelleştirilmiş Portföy Simülatörü & Raporu.

2019-2026 Walk-Forward OOS döneminde belirlenen 39 Alfa hisse üzerinde,
gerçek sermaye yönetimi kurallarıyla (en fazla 5 eş zamanlı pozisyon, işlem başına %1 risk)
portföyün sermaye büyümesini, kazanma oranını ve Sharpe/Calmar rasyolarını hesaplar.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rich.table import Table

from .report import console


def run_portfolio_backtest(
    trades_path: str = "reports/learn_rb_1d_oos_kural.csv",
    alpha_path: str = "state/learned/rb_alpha_tickers.json",
    reports_dir: str = "reports",
    initial_capital: float = 100_000.0,
    max_positions: int = 5,
    risk_per_trade_pct: float = 0.01,
) -> dict[str, Any]:
    """Rejection Block stratejisinin portföy simülasyonunu çalıştırır."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tp = Path(trades_path)
    ap = Path(alpha_path)
    if not tp.exists():
        console.print(f"[red]İşlem dosyası bulunamadı: {tp}[/]")
        return {}

    trades_df = pd.read_csv(tp)
    if ap.exists():
        with open(ap, "r", encoding="utf-8") as f:
            alpha_data = json.load(f)
        alpha_tickers = set(alpha_data.get("tickers", {}).keys())
        trades = trades_df[trades_df["ticker"].isin(alpha_tickers)].copy()
    else:
        trades = trades_df.copy()

    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    trades = trades.sort_values("entry_time").reset_index(drop=True)

    daily_dates = pd.date_range(trades["entry_time"].min(), trades["exit_time"].max(), freq="B")
    curr_equity = initial_capital
    daily_equity = {}
    open_trades = []
    trade_pnl_log = []

    for cur_date in daily_dates:
        # 1. Kapanan pozisyonları kontrol et
        still_open = []
        for exit_time, risk_usd, row in open_trades:
            if cur_date >= exit_time:
                pnl_usd = risk_usd * row["R"]
                curr_equity += pnl_usd
                trade_pnl_log.append({
                    "ticker": row["ticker"],
                    "entry_time": row["entry_time"],
                    "exit_time": exit_time,
                    "R": row["R"],
                    "net_pct": row["net"],
                    "risk_usd": risk_usd,
                    "pnl_usd": pnl_usd,
                    "equity_after": curr_equity,
                    "reason": row["reason"],
                })
            else:
                still_open.append((exit_time, risk_usd, row))
        open_trades = still_open

        # 2. Bugün açılmak isteyen pozisyonlar
        todays_candidates = trades[trades["entry_time"] == cur_date]
        for _, row in todays_candidates.iterrows():
            currently_held = {t[2]["ticker"] for t in open_trades}
            if len(open_trades) < max_positions and row["ticker"] not in currently_held:
                risk_usd = curr_equity * risk_per_trade_pct
                open_trades.append((row["exit_time"], risk_usd, row))

        daily_equity[cur_date] = curr_equity

    eq_series = pd.Series(daily_equity)
    exec_df = pd.DataFrame(trade_pnl_log)

    total_return_pct = (eq_series.iloc[-1] / initial_capital - 1) * 100
    n_exec = len(exec_df)
    wins = (exec_df["pnl_usd"] > 0).sum()
    win_rate = (wins / n_exec) * 100 if n_exec > 0 else 0
    total_pnl = exec_df["pnl_usd"].sum()
    gross_profit = exec_df.loc[exec_df["pnl_usd"] > 0, "pnl_usd"].sum()
    gross_loss = abs(exec_df.loc[exec_df["pnl_usd"] < 0, "pnl_usd"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    cummax = eq_series.cummax()
    dd = (eq_series - cummax) / cummax
    max_dd = dd.min() * 100

    days = (daily_dates[-1] - daily_dates[0]).days
    years = days / 365.25
    cagr = ((eq_series.iloc[-1] / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    daily_ret = eq_series.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0
    calmar = abs(cagr / (max_dd / 100)) if max_dd != 0 else 0

    # Rich Tablosu
    t = Table(title="Rejection Block Kusursuzlaştırılmış Portföy Sonuçları (2019 - 2026 OOS)", header_style="bold cyan")
    t.add_column("Metrik", style="bold")
    t.add_column("Değer", justify="right")

    t.add_row("Başlangıç Sermayesi", f"${initial_capital:,.2f}")
    t.add_row("Bitiş Sermayesi", f"${eq_series.iloc[-1]:,.2f}")
    t.add_row("Toplam Net Kâr", f"[bold green]${total_pnl:,.2f} (+%{total_return_pct:.1f})[/]")
    t.add_row("Yıllık Bileşik Getiri (CAGR)", f"[bold green]+%{cagr:.1f}[/]")
    t.add_row("Toplam Gerçekleşen İşlem", f"{n_exec}")
    t.add_row("Kazanma Oranı (Win Rate)", f"%{win_rate:.1f} (Hedef 3R)")
    t.add_row("Kâr Faktörü (Profit Factor)", f"[bold cyan]{pf:.2f}[/]")
    t.add_row("Maksimum Düşüş (Max DD)", f"[red]%{max_dd:.1f}[/]")
    t.add_row("Sharpe Oranı", f"{sharpe:.2f}")
    t.add_row("Calmar Oranı", f"{calmar:.2f}")
    t.add_row("3R Hedefe Ulaşan İşlemler", f"{(exec_df['reason'] == 'hedef').sum()} (%{((exec_df['reason'] == 'hedef').mean()*100):.1f})")
    t.add_row("Stop Olan (Fitil ucu)", f"{(exec_df['reason'] == 'stop').sum()} (%{((exec_df['reason'] == 'stop').mean()*100):.1f})")
    t.add_row("Zaman Stopu (20 bar)", f"{(exec_df['reason'] == 'zaman').sum()} (%{((exec_df['reason'] == 'zaman').mean()*100):.1f})")

    console.print(t)

    # Grafik Çizimi
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    ax1.plot(eq_series.index, eq_series.values, color="#00C853", linewidth=2, label=f"RB Taktik Portföyü (CAGR: +%{cagr:.1f})")
    ax1.set_title("Rejection Block Kusursuzlaştırılmış Portföy Büyümesi (2019 - 2026)\n"
                  f"Kural: Yapısal Stop ± 0.25 ATR, 3R Hedef, %1 Risk/İşlem, 39 Alfa Hisse", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Portföy Değeri ($)", fontsize=10)
    ax1.legend(loc="upper left")
    ax1.yaxis.set_major_formatter("${x:,.0f}")

    ax2.fill_between(dd.index, dd.values * 100, 0, color="#D50000", alpha=0.35, label=f"Drawdown (Maks: %{max_dd:.1f})")
    ax2.set_ylabel("Düşüş %", fontsize=10)
    ax2.set_xlabel("Tarih", fontsize=10)
    ax2.legend(loc="lower left")

    plt.tight_layout()
    chart_path = Path(reports_dir) / "rb_strategy_equity.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(chart_path)
    plt.close()
    console.print(f"Sermaye eğrisi grafiği kaydedildi: [bold]{chart_path}[/]")

    return {
        "final_equity": float(eq_series.iloc[-1]),
        "total_pnl": float(total_pnl),
        "return_pct": float(total_return_pct),
        "cagr": float(cagr),
        "trades": n_exec,
        "win_rate": float(win_rate),
        "pf": float(pf),
        "max_dd": float(max_dd),
        "sharpe": float(sharpe),
    }


if __name__ == "__main__":
    run_portfolio_backtest()
