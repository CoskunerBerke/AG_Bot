"""Risk yönetimi: pozisyon boyutlama, günlük %1 hedef kilidi ve günlük zarar limiti."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from .data import NY


class RiskManager:
    def __init__(self, cfg: dict):
        self.r = cfg["risk"]
        self.dir = Path(cfg["state_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.day_file = self.dir / "daily_state.json"
        self.pos_file = self.dir / "positions.json"

    # ------------------------------------------------------------------ boyutlama
    def position_size(self, equity: float, entry: float, stop: float, cash: float | None = None) -> int:
        """İşlem başına risk (%) ve maksimum pozisyon (%) sınırlarına göre adet."""
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0 or entry <= 0:
            return 0
        by_risk = equity * self.r["risk_per_trade_pct"] / 100 / risk_per_share
        by_cap = equity * self.r["max_position_pct"] / 100 / entry
        qty = min(by_risk, by_cap)
        if cash is not None:
            qty = min(qty, cash / entry)
        return max(0, math.floor(qty))

    # ------------------------------------------------------------------ günlük durum
    def _today(self) -> str:
        return datetime.now(NY).date().isoformat()

    def load_day(self) -> dict:
        if self.day_file.exists():
            st = json.loads(self.day_file.read_text(encoding="utf-8"))
            if st.get("date") == self._today():
                return st
        return {"date": self._today(), "target_hit": False, "loss_halt": False, "events": []}

    def save_day(self, st: dict) -> None:
        self.day_file.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")

    def evaluate(self, equity: float, last_equity: float) -> dict:
        """Günlük kâr/zarar durumunu hesaplar ve kilitleri günceller."""
        st = self.load_day()
        pnl = (equity / last_equity - 1) * 100 if last_equity else 0.0
        st["pnl_pct"] = pnl
        action = None
        if pnl >= self.r["daily_profit_target_pct"] and self.r.get("stop_after_target", True) and not st["target_hit"]:
            st["target_hit"] = True
            action = "TARGET"
            st["events"].append(f"{datetime.now(NY):%H:%M} hedef %{pnl:.2f} -> pozisyonlar kapatıldı")
        elif pnl <= -self.r["daily_max_loss_pct"] and not st["loss_halt"]:
            st["loss_halt"] = True
            action = "HALT"
            st["events"].append(f"{datetime.now(NY):%H:%M} zarar limiti %{pnl:.2f} -> durduruldu")
        self.save_day(st)
        st["action"] = action
        st["can_open"] = not (st["target_hit"] or st["loss_halt"])
        return st

    # ------------------------------------------------------------------ pozisyon kayıtları
    def load_positions(self) -> dict:
        if self.pos_file.exists():
            return json.loads(self.pos_file.read_text(encoding="utf-8"))
        return {}

    def record_entry(self, symbol: str, plan: dict) -> None:
        pos = self.load_positions()
        pos[symbol] = {"entry_date": self._today(), "stop": plan["stop"], "target": plan["target"]}
        self.pos_file.write_text(json.dumps(pos, indent=2), encoding="utf-8")

    def forget(self, symbols: list[str]) -> None:
        pos = self.load_positions()
        for s in symbols:
            pos.pop(s, None)
        self.pos_file.write_text(json.dumps(pos, indent=2), encoding="utf-8")
