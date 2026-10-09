"""Canlı Seans Kâr ve Risk Gözcüsü (Profit & Breakeven Watcher).

Görevi:
1. Her 15-30 saniyede bir Alpaca hesabını ve açık pozisyonları sorgular.
2. Portföy kârı +%1.0'e ulaştığı an:
   -> Tüm pozisyonları piyasa emriyle nakde çevirir (Kârı kasaya kilitler).
   -> Bekleyen giriş emirlerini iptal eder.
   -> Günlük hedef kilidini aktif eder.
3. Portföy -%2.0 zarara değerse:
   -> Devre kesiciyi çalıştırır, tüm pozisyonları kapatıp günü sonlandırır.
4. Breakeven (Başabaş) Koruması:
   -> Bir pozisyon +1.5R kâra ulaştığı an stop seviyesini başabaşa çeker (Sıfır risk).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from .broker import AlpacaBroker, BrokerError
from .config import load_config
from .data import NY
from .report import console
from .risk import RiskManager
from .trade_memory import TradeMemory

log = logging.getLogger("watcher")


class SessionWatcher:
    def __init__(self, cfg: dict, broker: AlpacaBroker):
        self.cfg = cfg
        self.b = broker
        self.rm = RiskManager(cfg)
        self.tm = TradeMemory(cfg.get("state_dir", "state"))
        self.target_pct = float(cfg.get("risk", {}).get("daily_profit_target_pct", 1.0))
        self.halt_pct = float(cfg.get("risk", {}).get("daily_max_loss_pct", 2.0))
        self.pos_tp_pct = float(cfg.get("risk", {}).get("position_take_profit_pct", 0.85))
        self.be_trigger_pct = float(cfg.get("risk", {}).get("breakeven_trigger_pct", 0.50))
        self.eod_cash_out = bool(cfg.get("risk", {}).get("eod_cash_out_enabled", True))
        self.eod_hour = int(cfg.get("risk", {}).get("eod_cash_out_hour_et", 15))
        self.eod_min = int(cfg.get("risk", {}).get("eod_cash_out_min_et", 45))
        self.breakeven_tracked: set[str] = set()
        self.last_positions: dict[str, dict] = {}

    def run_cycle(self) -> dict:
        """Tek bir denetim döngüsü yürütür."""
        acct = self.b.account()
        equity = float(acct["equity"])
        last_equity = float(acct.get("last_equity", equity))
        cash = float(acct["cash"])
        positions = self.b.positions()
        now_ny = datetime.now(NY)
        
        pnl_pct = ((equity / last_equity) - 1) * 100 if last_equity > 0 else 0.0
        pnl_usd = equity - last_equity

        status = {
            "time": now_ny.strftime("%H:%M:%S ET"),
            "equity": equity,
            "cash": cash,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "positions_count": len(positions),
            "action": None
        }

        # 1. GÜNLÜK %1 KÂR KİLİDİ
        if pnl_pct >= self.target_pct:
            console.print(f"\n[bold green]🎯 GÜNLÜK HEDEF KİLİTLENDİ: Portföy Kârı %{pnl_pct:+.2f} (+${pnl_usd:,.2f})[/]")
            console.print("[green]Tüm pozisyonlar kârla nakde çevriliyor ve gün kapatılıyor...[/]")
            try:
                self.b.close_all()
                self.b.cancel_stale_entries()
                st = self.rm.load_day()
                st["target_hit"] = True
                st["events"].append(f"{datetime.now(NY):%H:%M} HEDEF +%{pnl_pct:.2f} -> Kapatıldı")
                self.rm.save_day(st)
                status["action"] = "TARGET_LOCKED"
            except Exception as e:
                console.print(f"[red]Kâr kapatma hatası: {e}[/]")
            return status

        # 1.5 SEANS SONU KÂR KİLİDİ (EOD CASH-OUT: 15:45 ET SONRASI KÂRDAKİ HİSSELERİ NAKDE ÇEVİR)
        if self.eod_cash_out and ((now_ny.hour == self.eod_hour and now_ny.minute >= self.eod_min) or (now_ny.hour > self.eod_hour and now_ny.hour < 20)):
            for p in positions:
                sym = p["symbol"]
                unreal_pnl = float(p.get("unrealized_pl", 0))
                unreal_pct = float(p.get("unrealized_plpc", 0)) * 100
                if unreal_pnl > 0 or unreal_pct > 0.05:
                    console.print(f"\n[bold green]🌙 SEANS SONU KÂR KİLİDİ (EOD): {sym} kârda (+${unreal_pnl:,.2f} / +%{unreal_pct:.2f}) -> Gece riskine bırakılmadan nakde çevriliyor![/]")
                    try:
                        self.b.close_position(sym)
                    except Exception as ex:
                        log.warning("EOD kapatma hatası (%s): %s", sym, ex)

        # 2. GÜNLÜK %2 ZARAR DEVRE KESİCİSİ
        if pnl_pct <= -self.halt_pct:
            console.print(f"\n[bold red]🛑 ACİL FREN (DEVRE KESİCİ): Portföy Kaybı %{pnl_pct:+.2f} (-${abs(pnl_usd):,.2f})[/]")
            console.print("[red]Ana sermayeyi korumak için tüm pozisyonlar kapatılıyor...[/]")
            try:
                self.b.close_all()
                st = self.rm.load_day()
                st["loss_halt"] = True
                st["events"].append(f"{datetime.now(NY):%H:%M} FREN %{pnl_pct:.2f} -> Kapatıldı")
                self.rm.save_day(st)
                status["action"] = "HALT_EXECUTED"
            except Exception as e:
                console.print(f"[red]Acil fren kapatma hatası: {e}[/]")
            return status

        # 3. POZİSYON BAZINDA BREAKEVEN (BAŞABAŞ) DENETİMİ & HAFIZA SENKRONİZASYONU
        open_journal_symbols = {t["ticker"]: t for t in self.tm.history if t.get("status") == "OPEN"}
        closing_symbols = {o["symbol"] for o in self.b.open_orders() if o.get("side") == "sell" and o.get("type") == "market"}
        for p in positions:
            sym = p["symbol"]
            if sym in closing_symbols:
                continue
            qty = float(p["qty"])
            entry = float(p["avg_entry_price"])
            curr = float(p["current_price"])
            unreal_pnl = float(p.get("unrealized_pl", 0))
            unreal_pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
            
            # Anlık MFE / MAE güncellemesi (en yüksek görülen kâr hafızaya kalıcı işlenir)
            self.tm.update_live_metrics(sym, curr, unreal_pnl, unreal_pnl_pct)
            
            # Hafızada henüz yoksa otomatik senkronize et (örneğin sonradan dolan limit emirleri)
            if sym not in open_journal_symbols:
                self.tm.record_entry(
                    ticker=sym,
                    side="LONG" if float(p.get("qty", 0)) > 0 else "SHORT",
                    entry_price=entry,
                    stop_price=entry * 0.985,
                    target_price=entry * 1.03,
                    qty=int(abs(qty)),
                    rb_type="rejection_block",
                    rel_vol=1.2,
                    delta_ratio=0.15,
                    reasons=["Alpaca aktif açık pozisyonu hafızaya senkronize edildi"]
                )
                open_journal_symbols[sym] = True
                console.print(f"[magenta]🧠 Hafıza Senkronu:[/] {sym} ({int(qty)} adet @ ${entry:.2f}) takibe alındı.")

            # 3.0 TEK HİSSE MİKRO-KÂR KİLİDİ (POSITION-LEVEL TAKE-PROFIT)
            # Mega-cap hissede +%0.85 kârı görünce cebe koy, geri dönmesine izin verme!
            if unreal_pnl_pct >= self.pos_tp_pct:
                console.print(f"\n[bold green]💰 HİSSE KÂR KİLİDİ ({sym}):[/] Hedef +%{unreal_pnl_pct:.2f} (+${unreal_pnl:,.2f}) yakalandı! Kâr cebe konuyor, pozisyon kapatılıyor.")
                try:
                    self.b.close_position(sym)
                    continue
                except Exception as ex:
                    log.warning("Kâr kilidi kapatma hatası (%s): %s", sym, ex)

            # 3.1 BREAKEVEN (BAŞABAŞ) AKTİF KORUMA
            if unreal_pnl_pct >= self.be_trigger_pct and sym not in self.breakeven_tracked:
                console.print(f"[cyan]🛡️ BREAKEVEN KORUMASI AKTİF: {sym} +%{unreal_pnl_pct:.2f} kâr gördü -> Giriş seviyesi korumaya alındı![/]")
                self.breakeven_tracked.add(sym)

            # Eğer daha önce kâr görüp başabaş korumasına alınmışsa ve fiyat maliyete geri dönerse: KÂRI KORU, ZARARA İZİN VERME!
            if sym in self.breakeven_tracked and unreal_pnl_pct <= 0.10:
                console.print(f"[bold yellow]⚠️ BREAKEVEN TETİKLENDİ ({sym}):[/] Fiyat maliyete ($ {entry:.2f}) geri döndü (+%{unreal_pnl_pct:.2f}). Kârın zarara dönüşmemesi için pozisyon sıfır riskle kapatılıyor!")
                try:
                    self.b.close_position(sym)
                    continue
                except Exception as ex:
                    log.warning("Breakeven kapatma hatası (%s): %s", sym, ex)

            # 3.2 GİVEBACK / KÂR ERİME KORUMASI (TRADING2 İLKESİ)
            open_rec = self.tm.get_open_trade(sym)
            peak_mfe = float(open_rec.get("mfe_pct", 0.0)) if open_rec else unreal_pnl_pct
            # Eğer hisse en az +%0.50 kâr gördüyse ve bu kârın %35'inden fazlası eridiyse: kârı kurtar!
            if peak_mfe >= 0.50 and unreal_pnl_pct <= (peak_mfe * 0.60):
                console.print(f"\n[bold yellow]⚠️ KÂR ERİME KORUMASI (GIVEBACK) ({sym}):[/] Zirve kârı +%{peak_mfe:.2f} idi, şu an +%{unreal_pnl_pct:.2f}'ye indi. Kârın buharlaşmasını önlemek için pozisyon nakde çevriliyor!")
                try:
                    self.b.close_position(sym)
                    continue
                except Exception as ex:
                    log.warning("Giveback kapatma hatası (%s): %s", sym, ex)

            # 3.3 MOMENTUM & DÖNÜŞ TÜKENİŞ ÇIKIŞI (HİSSENİN DAHA GİDİŞATI YOKSA KÂRI ÇEK)
            # Eğer hisse kârdayken (>= +%0.35) 5m barlarda üst iğneli satış / ayı yutan mumu gelirse:
            if unreal_pnl_pct >= 0.35:
                try:
                    import yfinance as yf
                    df_5m = yf.download(sym, period="1d", interval="5m", progress=False).tail(3)
                    if not df_5m.empty:
                        if isinstance(df_5m.columns, pd.MultiIndex):
                            df_5m.columns = df_5m.columns.get_level_values(0)
                        last_bar = df_5m.iloc[-1]
                        o, h, l, c = float(last_bar["Open"]), float(last_bar["High"]), float(last_bar["Low"]), float(last_bar["Close"])
                        rng = h - l if h > l else 0.001
                        upper_wick = (h - max(o, c)) / rng
                        body_is_red = c < o
                        red_streak = sum(1 for _, r in df_5m.tail(2).iterrows() if float(r["Close"]) < float(r["Open"]))
                        
                        if (upper_wick >= 0.40 and body_is_red) or red_streak >= 2:
                            console.print(f"\n[bold magenta]📉 MOMENTUM TÜKENİŞİ TESPİT EDİLDİ ({sym}):[/] Üst fitil %{upper_wick*100:.0f} veya 2 kırmızı bar basıldı. Hissede yukarı gidişat zayıfladığı için +%{unreal_pnl_pct:.2f} (+${unreal_pnl:,.2f}) kâr cebe konuyor!")
                            try:
                                self.b.close_position(sym)
                                continue
                            except Exception as ex:
                                log.warning("Tükeniş kapatma hatası (%s): %s", sym, ex)
                except Exception as ex:
                    log.warning("Tükeniş analizi hatası (%s): %s", sym, ex)

            # 3.4 AKTİF MUM VE MOMENTUM BOZULMA DENETİMİ (Setup Invalidation Exit)
            # Eğer hisse -%1.5'ten fazla zarardaysa ve son barlarda art arda kırmızı satış mumları basıyorsa erken hasar kontrolü
            if unreal_pnl_pct <= -2.0:
                try:
                    import yfinance as yf
                    df_5m = yf.download(sym, period="1d", interval="5m", progress=False).tail(4)
                    if isinstance(df_5m.columns, pd.MultiIndex):
                        df_5m.columns = df_5m.columns.get_level_values(0)
                    reds = sum(1 for _, r in df_5m.iterrows() if r["Close"] < r["Open"])
                    if reds >= 3:
                        console.print(f"[bold red]🛑 FORMASYON İPTALİ / MUM ÇÖKÜŞÜ ({sym}):[/] Art arda 3 kırmızı bar basıldı ve zarar -%{abs(unreal_pnl_pct):.2f}. Hasar büyümeden erken kapatılıyor!")
                        self.b.close_position(sym)
                        continue
                except Exception as ex:
                    log.warning("Mum çöküşü analizi hatası (%s): %s", sym, ex)

        # 4. KAPANAN POZİSYONLARI TESPİT ET VE DERS ÇIKAR (Post-Mortem Learning)
        current_symbols = {p["symbol"]: p for p in positions}
        for old_sym, old_p in list(self.last_positions.items()):
            if old_sym not in current_symbols:
                # Pozisyon kapandı! (3R Kâr hedefi veya Stop loss doldu)
                exit_px = float(old_p.get("current_price", old_p.get("avg_entry_price")))
                unreal_pnl = float(old_p.get("unrealized_pl", 0))
                reason = "3R_HEDEFİ_VURULDU" if unreal_pnl > 0 else "STOP_LOSS_VURULDU"
                res = self.tm.record_exit(old_sym, exit_px, reason)
                if res:
                    # 1m, 5m, 1h Çıkış Mum Grafiği Snapshot'ı ve Adli Otopsi Raporu Oluştur
                    try:
                        from .chart_engine import create_trade_chart_snapshot, generate_trade_postmortem_report
                        chart_path, patterns = create_trade_chart_snapshot(
                            old_sym, res["trade_id"], res["entry_price"], res["stop_price"],
                            res["target_price"], exit_px, tag="exit"
                        )
                        rep_file = generate_trade_postmortem_report(res, chart_path, patterns)
                        console.print(f"\n[bold magenta]🔬 ADLİ OTOPSİ VE MUM RAPORU OLUŞTURULDU ({old_sym}):[/] {rep_file}")
                    except Exception as err:
                        console.print(f"[yellow]Otopsi raporu üretilemedi: {err}[/]")

                    if res.get("lessons_learned"):
                        console.print(f"\n[bold magenta]🧠 BOT ÖĞRENDİ ({old_sym}):[/]")
                        for l in res["lessons_learned"]:
                            console.print(f"   [magenta]-> {l}[/]")
        self.last_positions = current_symbols

        return status

    def loop(self, interval_sec: int = 20, max_cycles: int | None = None):
        """Seans boyunca çalışan gözcü döngüsü."""
        console.print(f"[bold cyan]🔍 Seans Gözcüsü Başlatıldı (Hedef: +%{self.target_pct:.1f} | Zarar Freni: -%{self.halt_pct:.1f})[/]")
        cycles = 0
        while True:
            try:
                st = self.run_cycle()
                act = f" | [bold]{st['action']}[/]" if st["action"] else ""
                console.print(f"[dim]{st['time']}[/] Özsermaye: ${st['equity']:,.2f} | Günlük K/Z: [{ 'green' if st['pnl_pct']>=0 else 'red'}]{st['pnl_pct']:+.2f}% (${st['pnl_usd']:+,.2f})[/] | Açık: {st['positions_count']}{act}")
                
                if st["action"] in ("TARGET_LOCKED", "HALT_EXECUTED"):
                    console.print("[bold yellow]Gözcü görevi başarıyla tamamlandı. İşlem sonlandırıldı.[/]")
                    break

                # Borsa kapandıysa seans gözcüsü sonlanır
                try:
                    clk = self.b.clock()
                    if not clk["is_open"] and datetime.now(NY).hour >= 16:
                        console.print("[dim]Borsa seansı kapandı (16:00 ET / 23:00 TSİ). Gözcü döngüsü tamamlandı.[/]")
                        break
                except Exception:
                    pass

                cycles += 1
                if max_cycles and cycles >= max_cycles:
                    break
                time.sleep(interval_sec)
            except KeyboardInterrupt:
                console.print("\n[yellow]Gözcü kullanıcı tarafından durduruldu.[/]")
                break
            except Exception as e:
                console.print(f"[red]Gözcü döngü hatası: {e}[/]")
                time.sleep(10)
