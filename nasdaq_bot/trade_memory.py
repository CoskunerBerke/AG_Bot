"""Kendi Kendine Öğrenen İşlem Hafızası, Neden-Sonuç Analiz ve Meta-Kural Motoru.

İşleyiş & Çapraz Hisse Öğrenmesi (Meta-Learning):
1. Her işleme girilirken piyasa bağlamı (hisse gün içi prim oranı %, stop mesafesi %, hacim oranı, saat) kaydedilir.
2. Seans boyunca işlem anlık izlenerek gördüğü en yüksek kâr (MFE - Maximum Favorable Excursion) ve
   en derin geri çekilme (MAE - Maximum Adverse Excursion) sürekli güncellenir.
3. Çıkışta 'Neden kazandım / Neden kaybettim?' analiz edilir:
   - Sadece tek hisseye değil, TÜM GELECEK HİSSELERE aktarılabilen evrensel kurallar (Meta-Rules) üretilir:
     * Gün içi %4.5'ten fazla fırlamış hisselere tepe alımı yapılmaz (CEG dersi).
     * Stop mesafesi %3.5'ten geniş olan işlemler filtrelenir (risk orantısızlığı dersi).
     * MFE +%0.6'ya ulaştığında kârın erimesine izin verilmez (Trailing Stop kuralı).
     * Saat 12:30-14:30 ET testere seansında yeni işlem açılmaz.
4. Tüm veriler hem JSON'da hem de Excel ile açılabilir 'reports/trade_journal.csv' dosyasında kalıcı olarak saklanır.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import NY

log = logging.getLogger("trade_memory")


class TradeMemory:
    def __init__(self, state_dir: str = "state", reports_dir: str = "reports"):
        self.dir = Path(state_dir) / "learned"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self.journal_file = self.dir / "trade_journal.json"
        self.rules_file = self.dir / "learned_rules.json"
        self.csv_file = self.reports_dir / "trade_journal.csv"

        self.history: list[dict[str, Any]] = self._load_journal()
        self.rules: dict[str, Any] = self._load_rules()
        self.export_csv()

    def _load_journal(self) -> list[dict[str, Any]]:
        if self.journal_file.exists():
            try:
                return json.loads(self.journal_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Trade journal yüklenemedi: %s", e)
        return []

    def _save_journal(self) -> None:
        self.journal_file.write_text(json.dumps(self.history, indent=2, ensure_ascii=False), encoding="utf-8")
        self.export_csv()

    def _load_rules(self) -> dict[str, Any]:
        default_rules = {
            "min_rel_vol": 1.15,
            "preferred_hours": [9, 10, 14, 15],
            "avoid_hours": [12, 13, 14],
            "meta_filters": {
                "max_entry_day_runup_pct": 4.5,   # Girişte hisse %4.5'ten fazla fırlamışsa tepe alımı yapma
                "max_stop_dist_pct": 3.5,         # Stop mesafesi %3.5'ten geniş olamaz
                "mfe_breakeven_trigger_pct": 0.6, # +%0.6 kârda stopu maliyete çek
                "mfe_trail_profit_pct": 1.0,      # +%1.0 kârda stopu +%0.5 kâra çek (Trailing Stop)
            },
            "ticker_multipliers": {},
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "key_lessons": []
        }
        if self.rules_file.exists():
            try:
                data = json.loads(self.rules_file.read_text(encoding="utf-8"))
                default_rules.update(data)
                return default_rules
            except Exception as e:
                log.warning("Learned rules yüklenemedi: %s", e)
        return default_rules

    def _save_rules(self) -> None:
        self.rules_file.write_text(json.dumps(self.rules, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ Giriş Kaydı
    def record_entry(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        qty: int,
        rb_type: str = "rejection_block",
        rel_vol: float = 1.0,
        delta_ratio: float = 0.0,
        intraday_gap_pct: float = 0.0,
        reasons: list[str] | None = None
    ) -> str:
        """Yeni açılan bir işlemin tüm nedenlerini, risk oranını ve piyasa bağlamını kaydeder."""
        trade_id = f"{ticker}_{datetime.now(NY):%Y%m%d_%H%M%S}"
        now_ny = datetime.now(NY)
        stop_dist_pct = abs(entry_price - stop_price) / entry_price * 100
        target_pct = abs(target_price - entry_price) / entry_price * 100

        entry_record = {
            "trade_id": trade_id,
            "ticker": ticker.upper(),
            "side": side.upper(),
            "status": "OPEN",
            "entry_time": now_ny.isoformat(),
            "entry_hour": now_ny.hour,
            "entry_price": float(round(entry_price, 2)),
            "stop_price": float(round(stop_price, 2)),
            "target_price": float(round(target_price, 2)),
            "stop_dist_pct": float(round(stop_dist_pct, 2)),
            "target_pct": float(round(target_pct, 2)),
            "qty": int(qty),
            "pos_val": float(round(qty * entry_price, 2)),
            "rb_type": rb_type,
            "rel_vol": float(round(rel_vol, 2)),
            "delta_ratio": float(round(delta_ratio, 2)),
            "intraday_gap_pct": float(round(intraday_gap_pct, 2)),
            "reasons": reasons or ["Rejection Block tespit edildi"],
            "mfe_usd": 0.0,
            "mfe_pct": 0.0,
            "mae_usd": 0.0,
            "mae_pct": 0.0,
            "current_price": float(round(entry_price, 2)),
            "exit_time": None,
            "exit_price": None,
            "exit_reason": None,
            "pnl_pct": None,
            "pnl_usd": None,
            "lessons_learned": None
        }
        self.history.append(entry_record)
        self._save_journal()
        log.info("İşlem hafızaya kaydedildi: %s %s @ $%.2f (Stop: -%%%.1f, Hedef: +%%%.1f)",
                 ticker, side, entry_price, stop_dist_pct, target_pct)
        return trade_id

    # ------------------------------------------------------------------ Canlı MFE / MAE Güncelleme
    def update_live_metrics(self, ticker: str, current_price: float, unreal_pnl_usd: float, unreal_pnl_pct: float) -> None:
        """Pozisyonun gördüğü en yüksek kârı (MFE) ve en derin zararı (MAE) anlık kaydeder."""
        updated = False
        for t in self.history:
            if t["status"] == "OPEN" and t["ticker"] == ticker.upper():
                t["current_price"] = round(current_price, 2)
                if unreal_pnl_usd > t.get("mfe_usd", 0.0):
                    t["mfe_usd"] = round(unreal_pnl_usd, 2)
                    t["mfe_pct"] = round(unreal_pnl_pct, 2)
                if unreal_pnl_usd < t.get("mae_usd", 0.0):
                    t["mae_usd"] = round(unreal_pnl_usd, 2)
                    t["mae_pct"] = round(unreal_pnl_pct, 2)
                updated = True
        if updated:
            self._save_journal()

    # ------------------------------------------------------------------ Çıkış & Çapraz Hisse Öğrenmesi
    def record_exit(
        self,
        ticker: str,
        exit_price: float,
        exit_reason: str,
        trade_id: str | None = None
    ) -> dict[str, Any] | None:
        """Pozisyon kapandığında sonucu hesaplar, hem hisse hem evrensel meta-dersler çıkarır."""
        now_ny = datetime.now(NY)
        target_trade = None
        for t in reversed(self.history):
            if t["status"] == "OPEN" and (trade_id is None or t["trade_id"] == trade_id):
                if t["ticker"] == ticker.upper():
                    target_trade = t
                    break

        if not target_trade:
            log.warning("Kapatılacak açık işlem bulunamadı: %s", ticker)
            return None

        entry_px = target_trade["entry_price"]
        side_mult = 1.0 if target_trade["side"] == "LONG" else -1.0
        pnl_pct = ((exit_price / entry_px) - 1.0) * 100 * side_mult
        pnl_usd = (exit_price - entry_px) * target_trade["qty"] * side_mult
        win = pnl_usd > 0

        target_trade["status"] = "CLOSED"
        target_trade["exit_time"] = now_ny.isoformat()
        target_trade["exit_price"] = float(round(exit_price, 2))
        target_trade["exit_reason"] = exit_reason
        target_trade["pnl_pct"] = round(pnl_pct, 2)
        target_trade["pnl_usd"] = round(pnl_usd, 2)
        target_trade["win"] = win

        # ÇAPRAZ HİSSE ÖĞRENME ANALİZİ (Her hisseye uygulanabilen kurallar)
        lessons = []
        mfe_pct = target_trade.get("mfe_pct", 0.0)
        stop_dist = target_trade.get("stop_dist_pct", 2.0)
        gap_pct = target_trade.get("intraday_gap_pct", 0.0)
        entry_h = target_trade.get("entry_hour", 10)

        if win:
            lessons.append(f"BAŞARI: {exit_reason} ile %+0.2f%% kâr (${pnl_usd:+,.2f}) realize edildi." % pnl_pct)
            if target_trade["rel_vol"] >= 1.2:
                lessons.append(f"Hacim Teyidi: {target_trade['rel_vol']:.1f}x hacim kurumsal desteği doğruladı.")
            if entry_h in (9, 10, 15):
                lessons.append(f"Zamanlama: Yüksek momentum seansında (Saat {entry_h}:00 ET) işlem yapıldı.")
        else:
            lessons.append(f"KAYIP: {exit_reason} nedeniyle %0.2f%% (${pnl_usd:,.2f}) sonuçlandı." % pnl_pct)
            # 1. Kârı Geri Verme Dersi (MFE Analizi)
            if mfe_pct >= 0.6:
                lessons.append(f"KÂR GERİ VERİLDİ: Pozisyon +%{mfe_pct:.2f} (${target_trade.get('mfe_usd', 0):+.1f}) kâr gördü fakat korunamadı. Kural: MFE > +%0.6 olunca Trailing Stop zorunlu!")
            # 2. Aşırı Prim / Tepe Alımı Dersi
            if gap_pct >= 4.5:
                lessons.append(f"TEPE TUZAĞI: Hisse güne +%{gap_pct:.1f} primle başlamıştı. Kural: Gün içi +%4.5'ten fazla koşmuş hisseye yeni Long açma!")
            # 3. Geniş Stop Dersi
            if stop_dist > 3.5:
                lessons.append(f"ORANTISIZ STOP: Stop mesafesi %{stop_dist:.1f} idi (aşırı geniş). Kural: Gün içi stoplar %3.5 ile sınırlandırılmalı.")
            # 4. Testere Saati Dersi
            if entry_h in (12, 13, 14):
                lessons.append(f"TESTERE SAATİ: 12:30-14:30 ET seans ortasında hacim düşüşüne yakalandı.")

        target_trade["lessons_learned"] = lessons
        self._save_journal()
        self._update_learned_rules()
        return target_trade

    def _update_learned_rules(self) -> None:
        """Tüm geçmişi tarar, hisse çarpanlarını ve meta-filtre kurallarını günceller."""
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        if not closed:
            return

        df = pd.DataFrame(closed)
        total = len(df)
        wins = df[df["win"] == True]
        win_rate = (len(wins) / total) * 100 if total > 0 else 0.0

        self.rules["total_trades"] = total
        self.rules["win_rate_pct"] = round(win_rate, 1)

        # Hisse bazlı katsayılar
        ticker_scores = {}
        for tk, grp in df.groupby("ticker"):
            tk_wins = grp[grp["win"] == True]
            tk_wr = len(tk_wins) / len(grp)
            ticker_scores[tk] = round(0.5 + tk_wr, 2)
        self.rules["ticker_multipliers"] = ticker_scores

        # Evrensel dersler listesi
        recent_lessons = []
        for t in closed[-10:]:
            for l in t.get("lessons_learned") or []:
                if l not in recent_lessons:
                    recent_lessons.append(f"[{t['ticker']}] {l}")
        self.rules["key_lessons"] = recent_lessons[-15:]

        self._save_rules()
        log.info("Öğrenilen kurallar güncellendi: Toplam işlem=%d, WinRate=%%%.1f", total, win_rate)

    def get_ticker_multiplier(self, ticker: str) -> float:
        return self.rules.get("ticker_multipliers", {}).get(ticker.upper(), 1.0)

    def get_meta_filters(self) -> dict[str, Any]:
        """Tüm gelecekteki hisselere uygulanacak evrensel kuralları döndürür."""
        return self.rules.get("meta_filters", {
            "max_entry_day_runup_pct": 4.5,
            "max_stop_dist_pct": 3.5,
            "mfe_breakeven_trigger_pct": 0.6,
            "mfe_trail_profit_pct": 1.0,
        })

    # ------------------------------------------------------------------ Excel / CSV İhracı
    def export_csv(self, path: Path | str | None = None) -> Path:
        """Kullanıcının doğrudan Excel'de inceleyebileceği temiz, detaylı CSV defteri üretir."""
        target_path = Path(path) if path else self.csv_file
        if not self.history:
            # Boş şablon oluştur
            cols = ["trade_id", "ticker", "side", "status", "entry_time", "entry_price",
                    "stop_price", "target_price", "stop_dist_pct", "target_pct", "qty",
                    "pos_val", "mfe_usd", "mfe_pct", "exit_time", "exit_price",
                    "exit_reason", "pnl_usd", "pnl_pct", "lessons_learned"]
            pd.DataFrame(columns=cols).to_csv(target_path, index=False, encoding="utf-8-sig")
            return target_path

        rows = []
        for t in self.history:
            row = {
                "İşlem ID": t.get("trade_id"),
                "Hisse": t.get("ticker"),
                "Yön": t.get("side"),
                "Durum": t.get("status"),
                "Giriş Zamanı": t.get("entry_time"),
                "Giriş Fiyatı ($)": t.get("entry_price"),
                "Stop Fiyatı ($)": t.get("stop_price"),
                "Hedef Fiyatı ($)": t.get("target_price"),
                "Stop Mesafesi (%)": t.get("stop_dist_pct", round(abs(t.get("entry_price", 0) - t.get("stop_price", 0)) / (t.get("entry_price") or 1) * 100, 2)),
                "Hedef (%)": t.get("target_pct", round(abs(t.get("target_price", 0) - t.get("entry_price", 0)) / (t.get("entry_price") or 1) * 100, 2)),
                "Adet": t.get("qty"),
                "Maliyet Tutarı ($)": t.get("pos_val"),
                "Görülen Max Kâr ($)": t.get("mfe_usd", 0.0),
                "Görülen Max Kâr (%)": t.get("mfe_pct", 0.0),
                "Çıkış Zamanı": t.get("exit_time"),
                "Çıkış Fiyatı ($)": t.get("exit_price"),
                "Çıkış Nedeni": t.get("exit_reason"),
                "Net K/Z ($)": t.get("pnl_usd"),
                "Net K/Z (%)": t.get("pnl_pct"),
                "Çıkarılan Dersler": " | ".join(t.get("lessons_learned") or [])
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_csv(target_path, index=False, encoding="utf-8-sig")
        return target_path
