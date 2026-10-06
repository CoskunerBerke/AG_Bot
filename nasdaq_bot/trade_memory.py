"""Kendi Kendine Öğrenen İşlem Hafızası ve Neden-Sonuç Analiz Motoru.

İşleyiş:
1. Her işleme girilirken piyasa bağlamı (hacim oranı, delta, saat, RB tipi, stop/hedef) kaydedilir.
2. İşlemden çıkıldığında sonuç (kâr/zarar, çıkış nedeni, süre) eşleştirilir.
3. Öğrenme Motoru (Learning Engine) her çıkış sonrası çalışarak:
   - 'Bu işlem neden kazandı?' veya 'Bu işlem neden kaybetti?' analizini yapar.
   - Gelecek işlemler için dinamik filtre kurallarını (örneğin: hacim eşiği, zaman filtresi,
     veya hisse bazlı güven skoru) otomatik olarak günceller.
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
    def __init__(self, state_dir: str = "state"):
        self.dir = Path(state_dir) / "learned"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal_file = self.dir / "trade_journal.json"
        self.rules_file = self.dir / "learned_rules.json"
        self.history: list[dict[str, Any]] = self._load_journal()
        self.rules: dict[str, Any] = self._load_rules()

    def _load_journal(self) -> list[dict[str, Any]]:
        if self.journal_file.exists():
            try:
                return json.loads(self.journal_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Trade journal yüklenemedi: %s", e)
        return []

    def _save_journal(self) -> None:
        self.journal_file.write_text(json.dumps(self.history, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_rules(self) -> dict[str, Any]:
        if self.rules_file.exists():
            try:
                return json.loads(self.rules_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Learned rules yüklenemedi: %s", e)
        return {
            "min_rel_vol": 1.15,
            "preferred_hours": [9, 10, 14, 15],
            "avoid_hours": [12, 13],
            "ticker_multipliers": {},
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "key_lessons": []
        }

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
        reasons: list[str] | None = None
    ) -> str:
        """Yeni açılan bir işlemin tüm nedenlerini ve piyasa bağlamını kaydeder."""
        trade_id = f"{ticker}_{datetime.now(NY):%Y%m%d_%H%M%S}"
        now_ny = datetime.now(NY)
        entry_record = {
            "trade_id": trade_id,
            "ticker": ticker.upper(),
            "side": side.upper(),
            "status": "OPEN",
            "entry_time": now_ny.isoformat(),
            "entry_hour": now_ny.hour,
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "target_price": float(target_price),
            "qty": int(qty),
            "pos_val": float(qty * entry_price),
            "rb_type": rb_type,
            "rel_vol": float(rel_vol),
            "delta_ratio": float(delta_ratio),
            "reasons": reasons or ["Rejection Block tespit edildi"],
            "exit_time": None,
            "exit_price": None,
            "exit_reason": None,
            "pnl_pct": None,
            "pnl_usd": None,
            "lessons_learned": None
        }
        self.history.append(entry_record)
        self._save_journal()
        log.info("İşlem hafızaya kaydedildi: %s %s @ $%.2f", ticker, side, entry_price)
        return trade_id

    # ------------------------------------------------------------------ Çıkış & Öğrenme
    def record_exit(
        self,
        ticker: str,
        exit_price: float,
        exit_reason: str,
        trade_id: str | None = None
    ) -> dict[str, Any] | None:
        """Pozisyon kapandığında sonucu hesaplar, ders çıkarır ve kural tabanını günceller."""
        now_ny = datetime.now(NY)
        target_trade = None
        
        # trade_id ile veya ticker'a ait en son açık işlemle eşleştir
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
        target_trade["exit_price"] = float(exit_price)
        target_trade["exit_reason"] = exit_reason
        target_trade["pnl_pct"] = round(pnl_pct, 2)
        target_trade["pnl_usd"] = round(pnl_usd, 2)
        target_trade["win"] = win

        # NEDEN-SONUÇ VE DERS ÇIKARMA ANALİZİ (Post-Mortem Attribution)
        lessons = []
        if win:
            lessons.append(f"BAŞARI: {exit_reason} ile %+0.2f%% kâr realize edildi." % pnl_pct)
            if target_trade["rel_vol"] >= 1.2:
                lessons.append(f"Teyit: Yüksek hacim ({target_trade['rel_vol']:.1f}x) kurumsal desteği doğruladı.")
            if target_trade["entry_hour"] in (9, 10, 14, 15):
                lessons.append(f"Zamanlama: Optimal seans penceresinde (Saat {target_trade['entry_hour']}:00 ET) girildi.")
        else:
            lessons.append(f"KAYIP: {exit_reason} nedeniyle %0.2f%% zarar oluştu." % pnl_pct)
            if target_trade["rel_vol"] < 1.1:
                lessons.append("Zayıf Nokta: Hacim ortalamanın altındaydı, bir dahakine daha yüksek hacim filtresi ara.")
            if target_trade["entry_hour"] in (11, 12, 13):
                lessons.append("Zamanlama Hatası: Öğle saatlerindeki düşük hacimli testere piyasasına denk geldi.")

        target_trade["lessons_learned"] = lessons
        self._save_journal()

        # Otomatik Kural Güncelleme (Feedback Loop)
        self._update_learned_rules()
        return target_trade

    def _update_learned_rules(self) -> None:
        """Geçmiş tüm kapalı işlemleri istatistiksel olarak inceleyip kuralları günceller."""
        closed = [t for t in self.history if t.get("status") == "CLOSED"]
        if not closed:
            return

        df = pd.DataFrame(closed)
        total = len(df)
        wins = df[df["win"] == True]
        win_rate = (len(wins) / total) * 100 if total > 0 else 0.0

        self.rules["total_trades"] = total
        self.rules["win_rate_pct"] = round(win_rate, 1)

        # Hisse bazında başarı çarpanları
        ticker_scores = {}
        for tk, grp in df.groupby("ticker"):
            tk_wins = grp[grp["win"] == True]
            tk_wr = len(tk_wins) / len(grp)
            # 1.0 nötr, yüksek kazanma oranına ödül, düşüğe ceza
            ticker_scores[tk] = round(0.5 + tk_wr, 2)
        self.rules["ticker_multipliers"] = ticker_scores

        # En önemli dersler özeti
        recent_lessons = []
        for t in closed[-5:]:
            for l in t.get("lessons_learned") or []:
                if l not in recent_lessons:
                    recent_lessons.append(f"[{t['ticker']}] {l}")
        self.rules["key_lessons"] = recent_lessons[-10:]

        self._save_rules()
        log.info("Öğrenilen kurallar güncellendi: Toplam işlem=%d, WinRate=%%%.1f", total, win_rate)

    def get_ticker_multiplier(self, ticker: str) -> float:
        """Bir hissenin geçmişte bu botta bıraktığı kâr tecrübesine göre skor çarpanı."""
        return self.rules.get("ticker_multipliers", {}).get(ticker.upper(), 1.0)
