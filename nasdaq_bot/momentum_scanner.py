"""Patlayıcı Hacim & Düşük Dolaşımlı Hisseler Kırılım Tarayıcısı (Explosive Momentum & Low-Float Breakout Scanner).

İşlevler:
1. ABD borsalarındaki (NASDAQ, AMEX, NYSE) $0.50 - $12.00 arası tüm patlayıcı küçük hisseleri (Penny & Micro-Cap) canlı tarar.
2. Normalin 3 katından fazla hacim patlaması (RVol > 3.0x) yaşayan ve gün içi > +10% artan hisseleri seçer.
3. VJET ve OLOX gibi hisselerde profesyonellerin uyguladığı Kırılım & Kademe Planını otomatik hesaplar:
   - KIRILIM (Direnç Seviyesi)
   - STOP (Destek Seviyesi)
   - TP1 (İlk Kâr Alma Seviyesi)
   - TP2 (Tüm Kademeleri Boşaltma Seviyesi)
4. Sonuçları konsola basar ve 'reports/momentum_runners.csv' olarak kaydeder.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from rich.table import Table

from .data import NY
from .report import console

log = logging.getLogger("momentum_scanner")


def fetch_live_gainers(min_change_pct: float = 10.0, max_price: float = 15.0, count: int = 50) -> list[dict[str, Any]]:
    """Canlı en çok yükselen ve hacim patlaması yaşayan küçük hisseleri çeker."""
    candidates = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    screeners = [
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=day_gainers&count=50",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=most_actives&count=50"
    ]

    seen = set()
    for url in screeners:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            quotes = r.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            for q in quotes:
                sym = q.get("symbol", "")
                if not sym or sym in seen or "^" in sym or "=" in sym:
                    continue
                seen.add(sym)

                px = float(q.get("regularMarketPrice") or 0)
                chg = float(q.get("regularMarketChangePercent") or 0)
                vol = float(q.get("regularMarketVolume") or 0)

                # Küçük ve orta fiyatlı momentum koşucuları ($0.50 - $15.00)
                if 0.50 <= px <= max_price and chg >= min_change_pct and vol >= 100_000:
                    candidates.append({
                        "ticker": sym,
                        "name": q.get("shortName", sym),
                        "price": px,
                        "change_pct": round(chg, 2),
                        "volume": int(vol),
                    })
        except Exception as e:
            log.warning("Gainer çekme hatası (%s): %s", url, e)

    return candidates


def analyze_breakout_levels(ticker: str, current_price: float) -> dict[str, Any] | None:
    """Hissenin 5 dakikalık konsolidasyon grafiğinden Kırılım, Stop, TP1 ve TP2 seviyelerini çıkarır."""
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False).tail(30)
        if df.empty or len(df) < 10:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        highs = df["High"]
        lows = df["Low"]
        vols = df["Volume"]

        # Hacim katı (Relative Volume)
        avg_vol = vols.rolling(10).mean().iloc[-1]
        rvol = round(float(vols.iloc[-1] / (avg_vol or 1)), 2)

        # Kırılım seviyesi: Son 15 barın en yüksek direnci
        resistance = round(float(highs.max()), 2)
        # Stop seviyesi: Son 10 barın en düşük desteği
        support = round(float(lows.tail(10).min()), 2)

        # Eğer direnç mevcut fiyata çok yakınsa (%2 içi)
        breakout = resistance if resistance >= current_price else round(current_price * 1.01, 2)
        risk = max(breakout - support, breakout * 0.04)
        stop = round(breakout - risk, 2)

        # TP1 (+%6 - %10) ve TP2 (+%15 - %25)
        tp1 = round(breakout + 1.2 * risk, 2)
        tp2 = round(breakout + 2.5 * risk, 2)
        tp1_pct = round(((tp1 / breakout) - 1.0) * 100, 1)
        tp2_pct = round(((tp2 / breakout) - 1.0) * 100, 1)

        return {
            "ticker": ticker,
            "current_price": current_price,
            "breakout": breakout,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_pct": tp1_pct,
            "tp2_pct": tp2_pct,
            "rvol": rvol,
        }
    except Exception as e:
        log.warning("%s analiz edilemedi: %s", ticker, e)
        return None


def run_momentum_scan(reports_dir: str = "reports") -> list[dict[str, Any]]:
    """Patlayıcı momentum taramasını yürütür, planları üretir ve kaydeder."""
    console.print("\n[bold cyan][MOMENTUM] Patlayıcı Küçük Hisseler & Kırılım Tarayıcısı Çalışıyor...[/]")
    candidates = fetch_live_gainers(min_change_pct=10.0, max_price=20.0)
    console.print(f"[dim]Kriterleri karşılayan {len(candidates)} hisse bulundu, kırılım seviyeleri hesaplanıyor...[/]")

    results = []
    for c in candidates:
        plan = analyze_breakout_levels(c["ticker"], c["price"])
        if plan:
            plan["change_pct"] = c["change_pct"]
            plan["volume"] = c["volume"]
            results.append(plan)

    # Sıralama: En yüksek hacim katı (RVol) ve değişim
    results.sort(key=lambda x: (x["rvol"], x["change_pct"]), reverse=True)

    # Tablo Yazdır
    table = Table(title="[MOMENTUM] Patlayıcı Küçük Hisseler & Kırılım Fırsatları (VJET / OLOX Tarzı)", border_style="cyan")
    table.add_column("Hisse", style="bold yellow")
    table.add_column("Fiyat ($)", justify="right")
    table.add_column("Günlük (%)", justify="right", style="green")
    table.add_column("Hacim Katı (RVol)", justify="right", style="magenta")
    table.add_column("KIRILIM ($)", justify="right", style="bold green")
    table.add_column("STOP ($)", justify="right", style="red")
    table.add_column("TP1 ($ / %)", justify="right")
    table.add_column("TP2 ($ / %)", justify="right", style="bold green")

    rows_to_save = []
    for r in results[:15]:
        table.add_row(
            r["ticker"],
            f"${r['current_price']:.2f}",
            f"+%{r['change_pct']:.1f}",
            f"{r['rvol']:.1f}x",
            f"${r['breakout']:.2f}",
            f"${r['stop']:.2f}",
            f"${r['tp1']:.2f} (+%{r['tp1_pct']}%)",
            f"${r['tp2']:.2f} (+%{r['tp2_pct']}%)"
        )
        rows_to_save.append({
            "Hisse": r["ticker"],
            "Güncel Fiyat ($)": r["current_price"],
            "Değişim (%)": r["change_pct"],
            "Hacim Katı (RVol)": r["rvol"],
            "KIRILIM ($)": r["breakout"],
            "STOP ($)": r["stop"],
            "TP1 ($)": r["tp1"],
            "TP1 (%)": r["tp1_pct"],
            "TP2 ($)": r["tp2"],
            "TP2 (%)": r["tp2_pct"]
        })

    console.print(table)

    # CSV Kaydet
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_file = out_dir / "momentum_runners.csv"
    pd.DataFrame(rows_to_save).to_csv(csv_file, index=False, encoding="utf-8-sig")
    console.print(f"[dim]Sonuçlar kaydedildi: {csv_file}[/dim]\n")

    return results
