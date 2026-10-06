"""Kullanıcı Taktikleri, Mum Formasyonları ve Grafik Modelleri Başarı İzleyicisi (Tactic Tracker).

Bu modül:
1. Kullanıcının sağladığı tüm teyit mumlarını (Confirmation Candles), OBO, TOBO, İkili Tepe ve Dip formasyonlarını izler.
2. Açılan her işlemde hangi taktik/formasyonun aktif olduğunu tespit edip işleme etiketler.
3. İşlem kapandığında o taktiğin kâr mı zarar mı ettirdiğini 'tactic_scorecard.json' ve 'reports/tactic_scorecard.csv' dosyalarına işler.
4. Başarılı taktiklerin katsayısını (Weight Multiplier) otomatik artırır, başarısız taktikleri cezalandırır.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .candles import PATTERNS as CANDLE_PATTERNS, detect_patterns
from .chart_patterns import CHART_PATTERNS, detect_chart_patterns

log = logging.getLogger("tactic_tracker")

# Birleştirilmiş Tüm Taktik Sözlüğü
ALL_TACTICS: dict[str, dict[str, Any]] = {}

for k, (name, direction, strength) in CANDLE_PATTERNS.items():
    ALL_TACTICS[k] = {
        "name": name,
        "type": "CANDLE",
        "direction": "BULL" if direction > 0 else "BEAR" if direction < 0 else "NEUTRAL",
        "strength": strength
    }

for k, (name, direction, strength) in CHART_PATTERNS.items():
    ALL_TACTICS[k] = {
        "name": name,
        "type": "CHART",
        "direction": "BULL" if direction > 0 else "BEAR" if direction < 0 else "NEUTRAL",
        "strength": strength
    }


class TacticTracker:
    def __init__(self, state_dir: str = "state", reports_dir: str = "reports"):
        self.state_dir = Path(state_dir) / "learned"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self.scorecard_file = self.state_dir / "tactic_scorecard.json"
        self.csv_file = self.reports_dir / "tactic_scorecard.csv"

        self.scorecard: dict[str, dict[str, Any]] = self._load_scorecard()
        self.export_csv()

    def _load_scorecard(self) -> dict[str, dict[str, Any]]:
        data = {}
        if self.scorecard_file.exists():
            try:
                data = json.loads(self.scorecard_file.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Tactic scorecard yüklenemedi: %s", e)

        # Eksik taktikleri varsayılan değerlerle ekle
        for key, meta in ALL_TACTICS.items():
            if key not in data:
                data[key] = {
                    "tactic_id": key,
                    "name": meta["name"],
                    "type": meta["type"],
                    "direction": meta["direction"],
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate_pct": 0.0,
                    "total_pnl_usd": 0.0,
                    "avg_pnl_pct": 0.0,
                    "multiplier": 1.0
                }
        return data

    def _save_scorecard(self) -> None:
        self.scorecard_file.write_text(json.dumps(self.scorecard, indent=2, ensure_ascii=False), encoding="utf-8")
        self.export_csv()

    def extract_active_tactics(self, df: pd.DataFrame, recent_bars: int = 3) -> list[dict[str, Any]]:
        """Son barlarda tetiklenen veya aktif olan tüm mum ve grafik taktiklerini tespit eder."""
        if df is None or len(df) < 25:
            return []

        active = []
        try:
            # 1. Mum Formasyonları
            c_pats, _ = detect_patterns(df)
            recent_candles = c_pats.iloc[-recent_bars:]
            for col in recent_candles.columns:
                if recent_candles[col].any():
                    meta = ALL_TACTICS.get(col, {})
                    active.append({
                        "id": col,
                        "name": meta.get("name", col),
                        "type": "CANDLE",
                        "direction": meta.get("direction", "NEUTRAL"),
                        "multiplier": self.get_multiplier(col)
                    })

            # 2. Grafik Formasyonları (OBO, TOBO, İkili Tepe/Dip vb.)
            ch_pats = detect_chart_patterns(df)
            recent_chart = ch_pats.iloc[-recent_bars:]
            for col in recent_chart.columns:
                if recent_chart[col].any():
                    meta = ALL_TACTICS.get(col, {})
                    active.append({
                        "id": col,
                        "name": meta.get("name", col),
                        "type": "CHART",
                        "direction": meta.get("direction", "NEUTRAL"),
                        "multiplier": self.get_multiplier(col)
                    })
        except Exception as e:
            log.warning("Taktik tarama hatası: %s", e)

        return active

    def record_outcome(self, tactic_ids: list[str], win: bool, pnl_usd: float, pnl_pct: float) -> None:
        """Kapanan bir işlemdeki tüm taktiklerin başarı hanesini günceller."""
        if not tactic_ids:
            return

        for tid in tactic_ids:
            if tid not in self.scorecard:
                continue
            entry = self.scorecard[tid]
            entry["total_trades"] += 1
            if win:
                entry["wins"] += 1
            else:
                entry["losses"] += 1

            entry["total_pnl_usd"] = round(entry["total_pnl_usd"] + pnl_usd, 2)
            tot = entry["total_trades"]
            wr = (entry["wins"] / tot) * 100 if tot > 0 else 0.0
            entry["win_rate_pct"] = round(wr, 1)

            # Dinamik Eğilim Ağırlığı (Katsayı):
            # 0.5 (düşük güven/ceza) ile 1.5 (yüksek güven/ödül) arasında
            entry["multiplier"] = round(0.5 + (wr / 100.0), 2)

        self._save_scorecard()
        log.info("Taktik karnesi güncellendi: %s | Sonuç: %s ($%.2f)", tactic_ids, "KÂR" if win else "ZARAR", pnl_usd)

    def get_multiplier(self, tactic_id: str) -> float:
        """Bir taktiğin geçmiş başarı eğilimine göre çarpanını döndürür."""
        return self.scorecard.get(tactic_id, {}).get("multiplier", 1.0)

    def export_csv(self) -> Path:
        """Excel ile doğrudan incelenebilir temiz bir Taktik Karnesi tablosu üretir."""
        rows = []
        for tid, data in self.scorecard.items():
            rows.append({
                "Taktik ID": tid,
                "Taktik / Formasyon Adı": data["name"],
                "Kategori": data["type"],
                "Sinyal Yönü": data["direction"],
                "Toplam Denenen İşlem": data["total_trades"],
                "Kazanan İşlem": data["wins"],
                "Kaybeden İşlem": data["losses"],
                "Başarı Oranı (%)": data["win_rate_pct"],
                "Toplam K/Z ($)": data["total_pnl_usd"],
                "Eğilim Ağırlık Çarpanı": data["multiplier"]
            })

        df = pd.DataFrame(rows)
        # Çok işlem yapılan ve başarılı olanları en üste sırala
        df.sort_values(by=["Toplam Denenen İşlem", "Başarı Oranı (%)"], ascending=[False, False], inplace=True)
        df.to_csv(self.csv_file, index=False, encoding="utf-8-sig")
        return self.csv_file
