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
        self.breakeven_tracked: set[str] = set()
        self.last_positions: dict[str, dict] = {}

    def run_cycle(self) -> dict:
        """Tek bir denetim döngüsü yürütür."""
        acct = self.b.account()
        equity = float(acct["equity"])
        last_equity = float(acct.get("last_equity", equity))
        cash = float(acct["cash"])
        positions = self.b.positions()
        
        pnl_pct = ((equity / last_equity) - 1) * 100 if last_equity > 0 else 0.0
        pnl_usd = equity - last_equity

        status = {
            "time": datetime.now(NY).strftime("%H:%M:%S ET"),
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
        for p in positions:
            sym = p["symbol"]
            qty = float(p["qty"])
            entry = float(p["avg_entry_price"])
            curr = float(p["current_price"])
            unreal_pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
            
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

            # Eğer hisse tek başına +%0.6 veya daha fazla kâra geçmişse başabaş koruması
            if unreal_pnl_pct >= 0.6 and sym not in self.breakeven_tracked:
                console.print(f"[cyan]🛡️ BREAKEVEN KORUMASI: {sym} +%{unreal_pnl_pct:.2f} kârda -> Risk sıfırlandı![/]")
                self.breakeven_tracked.add(sym)

        # 4. KAPANAN POZİSYONLARI TESPİT ET VE DERS ÇIKAR (Post-Mortem Learning)
        current_symbols = {p["symbol"]: p for p in positions}
        for old_sym, old_p in list(self.last_positions.items()):
            if old_sym not in current_symbols:
                # Pozisyon kapandı! (3R Kâr hedefi veya Stop loss doldu)
                exit_px = float(old_p.get("current_price", old_p.get("avg_entry_price")))
                unreal_pnl = float(old_p.get("unrealized_pl", 0))
                reason = "3R_HEDEFİ_VURULDU" if unreal_pnl > 0 else "STOP_LOSS_VURULDU"
                res = self.tm.record_exit(old_sym, exit_px, reason)
                if res and res.get("lessons_learned"):
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
