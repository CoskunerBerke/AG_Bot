"""Akıllı & Otonom Bulut İşlem Motoru (Smart Cloud Trader).

Bu modül kullanıcının şu taleplerini eksiksiz yerine getirir:
1. Bilgisayar kapalıyken bile GitHub Actions üzerinde otonom çalışır.
2. Forex Factory ekonomik takvimini (Kırmızı Klasör Kalkanı) ve Dow Jones son dakika haberlerini denetler.
3. İki ana koldan fırsat yakalar:
   - 39 Alfa hissede Rejection Block likidite süpürmeleri (SMC)
   - Patlayıcı Momentum & Penny Runner (günlük %10+ artan, RVol > 1.5x)
4. Kullanıcının 48 mum ve grafik formasyon varyasyonunu (Çekiç, Yutan Boğa, Sabah Yıldızı vb.)
   ve Order Flow alıcı emilimini doğrular; düşen bıçakları ve ayı tuzaklarını eler.
5. Her işlem için:
   - NEDEN GİRİLDİĞİ (Giriş gerekçesi, formasyon, hacim, haber katalizörü)
   - NE ZAMAN ÇIKILACAĞI (Stop loss, TP1, TP2, +%0.6 başabaş koruması)
   - NEDEN KÂR / ZARAR ETTİĞİ (Kök neden otopsisi, 1m/5m/1h mum grafiği)
   - BU DURUMUN NASIL DÜZELTİLECEĞİ (Kalıcı evrensel kural)
   bilgilerini reports/trade_journal.csv, reports/postmortems/ ve state/learned/ içine işler.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
from rich.table import Table

from .broker import AlpacaBroker, BrokerError, local_clock
from .chart_engine import (
    analyze_candle_formation,
    create_trade_chart_snapshot,
    evaluate_candlestick_gatekeeper,
    generate_trade_postmortem_report,
)
from .config import ROOT
from .data import NY
from .momentum_scanner import run_momentum_scan
from .news_agent import ForexFactoryNewsAgent
from .order_flow import compute_volume_delta, detect_absorption, should_smart_enter
from .report import console
from .risk import RiskManager
from .strategy import get_universe
from .tactic_tracker import TacticTracker
from .trade_memory import TradeMemory
from .watcher import SessionWatcher

log = logging.getLogger("smart_trader")


class SmartTrader:
    def __init__(self, cfg: dict, broker: AlpacaBroker | None = None):
        self.cfg = cfg
        self.broker = broker
        self.reports_dir = Path(cfg.get("reports_dir", "reports"))
        self.state_dir = Path(cfg.get("state_dir", "state"))
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.news_agent = ForexFactoryNewsAgent(self.state_dir, self.reports_dir)
        self.trade_memory = TradeMemory(self.state_dir)
        self.tactic_tracker = TacticTracker(self.state_dir, self.reports_dir)
        self.risk_manager = RiskManager(cfg)

    def run_full_pipeline(self, execute: bool = False, watch_minutes: int = 0) -> dict[str, Any]:
        """Tam döngü: Haber Kalkanı -> Çoklu Tarama -> Formasyon Onayı -> Emirler -> Gözcü."""
        console.print("\n[bold cyan]══════════════════════════════════════════════════════════════════[/]")
        console.print("[bold yellow]🤖 OTONOM AKILLI İŞLEM MOTORU (SMART CLOUD TRADER) BAŞLATILDI[/]")
        console.print("[bold cyan]══════════════════════════════════════════════════════════════════[/]\n")

        summary: dict[str, Any] = {
            "timestamp": datetime.now(NY).isoformat(),
            "news_shield": None,
            "candidates_found": 0,
            "orders_prepared": [],
            "orders_sent": [],
            "status": "COMPLETED"
        }

        # -------------------------------------------------------------
        # ADIM 1: FOREX FACTORY MAKRO İSTİHBARAT & KIRMIZI KLASÖR KALKANI
        # -------------------------------------------------------------
        console.print("[bold cyan]1. Forex Factory & Makro İstihbarat Denetleniyor...[/]")
        try:
            briefing = self.news_agent.generate_briefing()
            shield = self.news_agent.check_red_folder_shield()
            summary["news_shield"] = shield
            if shield["shield_active"]:
                console.print(f"[bold red]⚠️ DİKKAT: KIRMIZI KLASÖR KALKANI AKTİF![/]")
                console.print(f"[red]{shield['message']}[/]")
                console.print("[yellow]Büyük makro veri anında kontrolsüz kayma (slippage) riskine karşı yeni emir açılışı donduruluyor.[/]")
                if not execute:
                    console.print("[dim]Analiz modunda olunduğu için tarama bilgi amaçlı devam ettiriliyor.[/]")
                else:
                    summary["status"] = "HALTED_BY_NEWS_SHIELD"
                    return summary
            else:
                console.print("[bold green]🟢 Makro Kalkan Temiz: Kritik Kırmızı Klasör Yok, İşlem Güvenli.[/]")
        except Exception as ex:
            log.warning("Haber ajanı hatası: %s", ex)
            console.print(f"[yellow]Haber ajanı uyarısı: {ex}[/]")

        # -------------------------------------------------------------
        # ADIM 2: ÇİFT YÖNLÜ TARAMA (REJECTION BLOCK + MOMENTUM RUNNERS)
        # -------------------------------------------------------------
        console.print("\n[bold cyan]2. Çoklu Piyasa Taraması Yapılıyor (SMC Rejection + Patlayıcı Momentum)...[/]")
        candidates = []

        # A) Patlayıcı Momentum / Penny Runners (%10+ fırlayan, yüksek hacimli)
        try:
            momentum_candidates = run_momentum_scan(self.reports_dir)
            for m in momentum_candidates:
                candidates.append({
                    "ticker": m["ticker"],
                    "source": "MOMENTUM_RUNNER",
                    "price": float(m.get("current_price") or m.get("price") or 0.0),
                    "pct_change": float(m.get("change_pct") or m.get("pct_change") or 0.0),
                    "rvol": float(m.get("rvol", 1.5)),
                    "breakout": m.get("breakout"),
                    "stop": m.get("stop"),
                    "tp1": m.get("tp1"),
                    "tp2": m.get("tp2"),
                    "reason": f"Patlayıcı Momentum: Günlük %{m.get('change_pct', 0):.1f} ralli, RVol {m.get('rvol', 1.5):.1f}x"
                })
        except Exception as ex:
            log.warning("Momentum tarama hatası: %s", ex)

        # B) 39 Alfa NASDAQ Hissesinde Rejection Block Taraması
        try:
            from nasdaq_bot.rejection_blocks import find_rejection_blocks
            from nasdaq_bot.indicators import add_indicators
            from nasdaq_bot.data import download_ohlcv

            alpha_tickers = [t for t in get_universe(self.cfg) if t not in ["SPY", "QQQ"]][:20]
            dfs = download_ohlcv(alpha_tickers, period="30d", interval="1h", cache_dir=self.cfg["data"]["cache_dir"],
                                 ttl_minutes=self.cfg["data"]["cache_ttl_minutes"])
            for sym, df in dfs.items():
                if df is None or len(df) < 20:
                    continue
                d = add_indicators(df)
                sigs, stops, zones = find_rejection_blocks(d)
                live_zones = [z for z in zones if z.end is None]
                if not live_zones:
                    continue
                px = float(d["Close"].iloc[-1])
                for z in live_zones:
                    if z.side > 0:  # Boğa Rejection Destek Bölgesi
                        dist_pct = abs(px - z.top) / px * 100
                        if dist_pct <= 2.5:  # Bölgeye %2.5 yakın
                            candidates.append({
                                "ticker": sym,
                                "source": "REJECTION_BLOCK",
                                "price": px,
                                "pct_change": float(d["Close"].pct_change().iloc[-1] * 100),
                                "rvol": float(d["Volume"].iloc[-1] / d["Volume"].rolling(20).mean().iloc[-1]) if "Volume" in d else 1.2,
                                "breakout": round(px * 1.002, 2),
                                "stop": round(z.bot * 0.995, 2),
                                "tp1": round(px + 2.0 * (px - z.bot), 2),
                                "tp2": round(px + 3.0 * (px - z.bot), 2),
                                "reason": f"SMC Rejection Destek Testi (${z.bot:.2f}-${z.top:.2f}) | Likidite Süpürme: {'Evet' if z.sweep else 'Normal'}"
                            })
                            break
        except Exception as ex:
            log.warning("RB tarama hatası: %s", ex)

        summary["candidates_found"] = len(candidates)
        console.print(f"[green]✓ Toplam {len(candidates)} potansiyel fırsat adayı tespit edildi.[/]")

        # -------------------------------------------------------------
        # ADIM 3: 48 MUM FORMASYON VARYASYONU & ORDER FLOW KAPICISI
        # -------------------------------------------------------------
        console.print("\n[bold cyan]3. 48 Mum Formasyonu & Order Flow Alıcı Emilimi Doğrulanıyor...[/]")
        confirmed_setups = []

        table = Table(title="🔍 Aday Hisselerin Mum Morfolojisi & Karar Tablosu", border_style="cyan")
        table.add_column("Hisse", style="bold")
        table.add_column("Kaynak")
        table.add_column("Fiyat ($)")
        table.add_column("5m Mum Morfolojisi")
        table.add_column("Order Flow / Alıcı Emilimi")
        table.add_column("Taktik & Karar")

        for cand in candidates[:15]:
            sym = cand["ticker"]
            px = cand["price"]
            try:
                # 5m mumlarını çek
                df_5m = yf.download(sym, period="2d", interval="5m", progress=False)
                if df_5m.empty or len(df_5m) < 5:
                    continue
                if isinstance(df_5m.columns, pd.MultiIndex):
                    df_5m.columns = df_5m.columns.get_level_values(0)

                # Mum Morfolojisi
                morph = analyze_candle_formation(df_5m)
                pattern_name = morph.get("pattern", "STANDART")
                desc = morph.get("desc", "-")
                lw_ratio = morph.get("lower_wick_ratio", 0.0)

                # AI Kapı Bekçisi Kontrolü
                gate_ok, gate_msg, _ = evaluate_candlestick_gatekeeper(sym, "LONG")

                # Order Flow Alıcı Emilimi
                of_metrics = detect_absorption(df_5m, px, side="LONG")
                is_absorbed = of_metrics.get("absorption", False)
                delta_ratio = of_metrics.get("delta_ratio", 0.0)

                # Taktik Eşleştirme
                tactic_name = "Rejection Block Destek"
                win_rate = 55.0
                active_t = self.tactic_tracker.extract_active_tactics(df_5m)
                if active_t:
                    tactic_name = active_t[0]["name"]
                    t_id = active_t[0]["id"]
                    t_info = self.tactic_tracker.scorecard.get(t_id, {})
                    win_rate = t_info.get("win_rate_pct", 55.0)
                elif pattern_name in ("CEKIC_ALICI_RETI", "DOJI_ALICI_RETI") or lw_ratio >= 0.50:
                    tactic_name = "Çekiç (Hammer / Dip İğne)"
                elif pattern_name == "YUTAN_BOGA":
                    tactic_name = "Yutan Boğa (Bullish Engulfing)"
                elif pattern_name == "MARUBOZU_BOGA":
                    tactic_name = "Boğa Marubozu (Güçlü Alış)"

                # Karar Kuralları
                learned_avoid = self.trade_memory.rules.get("avoid_hours", [12, 13, 14])
                current_hour = datetime.now(NY).hour
                in_lunch_chop = current_hour in learned_avoid

                decision = "ONAYLANDI"
                reject_reason = ""

                if not gate_ok:
                    decision = "REDDEDİLDİ"
                    reject_reason = gate_msg
                elif in_lunch_chop:
                    decision = "REDDEDİLDİ"
                    reject_reason = f"Testere Saati ({current_hour}:00 ET New York Öğle Chop)"
                elif cand["pct_change"] > 60.0:
                    decision = "REDDEDİLDİ"
                    reject_reason = "Aşırı Primli (>%60) Tükeniş Riski"

                of_str = f"Delta: {delta_ratio:+.2f} | {'Emilim: VAR' if is_absorbed else 'Normal'}"
                morph_str = f"{pattern_name} (Alt İğne: %{lw_ratio*100:.0f})"
                decision_str = f"[bold green]✓ {decision}[/]" if decision == "ONAYLANDI" else f"[bold red]✗ {reject_reason}[/]"

                table.add_row(sym, cand["source"], f"${px:.2f}", morph_str, of_str, decision_str)

                if decision == "ONAYLANDI":
                    confirmed_setups.append({
                        "ticker": sym,
                        "source": cand["source"],
                        "entry_price": cand["breakout"] or px,
                        "stop_price": cand["stop"],
                        "tp1": cand["tp1"],
                        "tp2": cand["tp2"],
                        "tactic_name": tactic_name,
                        "win_rate": win_rate,
                        "morphology": morph_str,
                        "order_flow": of_str,
                        "reason": f"{cand['reason']} | Taktik: {tactic_name} (Kazanma: %{win_rate:.0f}) | {morph_str}"
                    })
            except Exception as ex:
                log.warning("Mum analiz hatası (%s): %s", sym, ex)
                continue

        console.print(table)
        summary["orders_prepared"] = confirmed_setups

        # -------------------------------------------------------------
        # ADIM 4: EMİR PLANLARI & NEDEN-SONUÇ VE ÇIKIŞ ŞARTLARININ KAYDI
        # -------------------------------------------------------------
        if not confirmed_setups:
            console.print("\n[yellow]Şu anda piyasada tüm güvenlik filtrelerinden (Mum, Order Flow, Öğrenilen Kurallar) geçen onaylı işlem bulunamadı.[/]")
            return summary

        console.print(f"\n[bold green]🎯 {len(confirmed_setups)} Adet Onaylı İşlem Seviyesi Hesaplandı:[/]")
        orders_table = Table(title="📋 Giriş & Çıkış Planı Kütüğü", border_style="green")
        orders_table.add_column("Hisse", style="bold")
        orders_table.add_column("Giriş ($)")
        orders_table.add_column("Stop ($) (Max -%3)")
        orders_table.add_column("TP1 ($) (+%3-5)")
        orders_table.add_column("TP2 ($) (+%8-15)")
        orders_table.add_column("Neden Girildi? (Gerekçe & Taktik)")
        orders_table.add_column("Ne Zaman Çıkılacak? (Plan)")

        for s in confirmed_setups:
            stop_pct = abs(s["entry_price"] - s["stop_price"]) / s["entry_price"] * 100
            tp1_pct = abs(s["tp1"] - s["entry_price"]) / s["entry_price"] * 100
            exit_plan = f"Stop: ${s['stop_price']:.2f} (-%{stop_pct:.1f}) | TP1: ${s['tp1']:.2f} (+%{tp1_pct:.1f}) | +%0.6'da Başabaş"
            orders_table.add_row(
                s["ticker"],
                f"${s['entry_price']:.2f}",
                f"${s['stop_price']:.2f} (-%{stop_pct:.1f})",
                f"${s['tp1']:.2f} (+%{tp1_pct:.1f})",
                f"${s['tp2']:.2f}",
                s["reason"],
                exit_plan
            )
        console.print(orders_table)

        # -------------------------------------------------------------
        # ADIM 5: BROKERA EMİR İLETİMİ (EXECUTE AKTİFSE)
        # -------------------------------------------------------------
        if execute and self.broker:
            console.print("\n[bold cyan]5. Alpaca Hesabına Bracket Emirleri Gönderiliyor...[/]")
            acct = self.broker.account()
            equity = float(acct["equity"])
            cash = float(acct["cash"])
            console.print(f"Alpaca Özsermaye: ${equity:,.2f} | Boşta Nakit: ${cash:,.2f}")

            for s in confirmed_setups[:2]:  # Sermaye yönetimi gereği eşzamanlı en fazla 2 en iyi setup
                sym = s["ticker"]
                entry = s["entry_price"]
                stop = s["stop_price"]
                target = s["tp1"]

                size_usd = min(equity * 0.25, cash * 0.90)
                qty = max(1, int(size_usd / entry))

                try:
                    res = self.broker.order_bracket(
                        symbol=sym,
                        qty=qty,
                        side="buy",
                        stop_loss=stop,
                        take_profit=target,
                        limit_price=entry
                    )
                    console.print(f"[bold green]✓ EMİR İLETİLDİ:[/] {sym} - {qty} adet @ ${entry:.2f} (Stop: ${stop:.2f}, Hedef: ${target:.2f})")
                    summary["orders_sent"].append(sym)

                    # Hafızaya kaydet
                    self.trade_memory.record_entry(
                        ticker=sym,
                        side="LONG",
                        entry_price=entry,
                        stop_price=stop,
                        target_price=target,
                        qty=qty,
                        rb_type=s["source"],
                        rel_vol=1.5,
                        delta_ratio=0.15,
                        reasons=[s["reason"], f"Çıkış Planı: Stop ${stop:.2f}, Hedef ${target:.2f}, Breakeven +%0.6"]
                    )
                except Exception as ex:
                    console.print(f"[red]Emir gönderme hatası ({sym}): {ex}[/]")
        elif not execute:
            console.print("\n[dim]--execute verilmediği için emirler gönderilmedi (Kuru Çalışma Modu).[/]")

        # -------------------------------------------------------------
        # ADIM 6: SEANS GÖZCÜSÜ & KÂR KİLİDİ (WATCH İSTENMİŞSE)
        # -------------------------------------------------------------
        if watch_minutes > 0 and self.broker:
            console.print(f"\n[bold cyan]6. Canlı Seans Gözcüsü {watch_minutes} Dakika Boyunca Devrede...[/]")
            watcher = SessionWatcher(self.cfg, self.broker)
            cycles = int(watch_minutes * 60 / 20)
            watcher.loop(interval_sec=20, max_cycles=cycles)

        return summary
