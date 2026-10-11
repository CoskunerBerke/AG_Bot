"""Broker katmanı.

* AlpacaBroker : gerçek / paper Alpaca hesabı (REST). Long + short bracket emirleri.
* market_clock : ABD borsası açık mı, sonraki açılış/kapanış (tatiller ve yarım günler dahil).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .common import NY


class BrokerError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- borsa takvimi
NYSE_HOLIDAYS = {date.fromisoformat(d) for d in [
    "2024-11-28", "2024-12-25", "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]}
EARLY_CLOSE = {date.fromisoformat(d) for d in ["2026-11-27", "2026-12-24", "2027-11-26"]}


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def _session(d: date) -> tuple[datetime, datetime]:
    close_t = dtime(13, 0) if d in EARLY_CLOSE else dtime(16, 0)
    return datetime.combine(d, dtime(9, 30), NY), datetime.combine(d, close_t, NY)


def local_clock(now: datetime | None = None) -> dict:
    now = now or datetime.now(NY)
    d = now.date()
    if _is_trading_day(d):
        o, c = _session(d)
        if o <= now < c:
            return {"is_open": True, "next_open": o, "next_close": c, "timestamp": now}
        if now < o:
            return {"is_open": False, "next_open": o, "next_close": c, "timestamp": now}
    nd = d + timedelta(days=1)
    while not _is_trading_day(nd):
        nd += timedelta(days=1)
    o, c = _session(nd)
    return {"is_open": False, "next_open": o, "next_close": c, "timestamp": now}


# ----------------------------------------------------------------------------- Alpaca
class AlpacaBroker:
    name = "ALPACA"

    def __init__(self, key: str, secret: str, paper: bool = True, base_url: str | None = None):
        if not key or not secret:
            raise BrokerError("ALPACA_API_KEY / ALPACA_API_SECRET .env dosyasında tanımlı değil.")
        base = base_url or os.getenv("ALPACA_BASE_URL") or ("https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets")
        if base.endswith("/v2"):
            base = base[:-3]
        self.base = base
        self.paper = paper
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._assets: dict[str, dict] = {}

    @classmethod
    def from_env(cls, cfg: dict) -> "AlpacaBroker":
        key = os.getenv("ALPACA_API_KEY", "") or os.getenv("ALPACA_KEY_ID", "")
        secret = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
        return cls(key, secret, paper=cfg.get("broker", {}).get("paper", True))

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=20, **kw)
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    # bilgi
    def account(self) -> dict:
        return self._req("GET", "/v2/account")

    def clock(self) -> dict:
        c = self._req("GET", "/v2/clock")
        return {"is_open": c["is_open"], "timestamp": datetime.fromisoformat(c["timestamp"]).astimezone(NY),
                "next_open": datetime.fromisoformat(c["next_open"]).astimezone(NY),
                "next_close": datetime.fromisoformat(c["next_close"]).astimezone(NY)}

    def positions(self) -> list[dict]:
        out = []
        for p in self._req("GET", "/v2/positions") or []:
            p["qty"] = abs(float(p["qty"]))
            out.append(p)
        return out

    def open_orders(self) -> list[dict]:
        return self._req("GET", "/v2/orders", params={"status": "open", "nested": "true", "limit": 500}) or []

    def is_shortable(self, symbol: str) -> bool:
        if symbol not in self._assets:
            try:
                self._assets[symbol] = self._req("GET", f"/v2/assets/{symbol}")
            except BrokerError:
                self._assets[symbol] = {}
        a = self._assets[symbol]
        return bool(a.get("shortable") and a.get("easy_to_borrow") and a.get("tradable"))

    # emirler
    def place_bracket(self, symbol: str, qty: int, side: str, take_profit: float, stop_loss: float,
                      limit: float | None = None, tif: str = "gtc") -> dict:
        body = {
            "symbol": symbol, "qty": str(int(qty)), "side": side,
            "type": "limit" if limit else "market", "time_in_force": tif, "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        }
        if limit:
            body["limit_price"] = f"{limit:.2f}"
        return self._req("POST", "/v2/orders", json=body)

    # Alias for compatibility
    order_bracket = place_bracket

    def close_position(self, symbol: str):
        # Önce bu sembolün açık bacak emirlerini iptal et, sonra pozisyonu kapat
        for o in self.open_orders():
            if o.get("symbol") == symbol:
                try:
                    self._req("DELETE", f"/v2/orders/{o['id']}")
                except BrokerError:
                    pass
        return self._req("DELETE", f"/v2/positions/{symbol}")

    def close_all(self):
        return self._req("DELETE", "/v2/positions", params={"cancel_orders": "true"})

    # ---- DMC yürütücüsü için düşük seviye emir API'si
    def submit(self, body: dict) -> dict:
        return self._req("POST", "/v2/orders", json=body)

    def orders_after(self, after_iso_utc: str, status: str = "all") -> list[dict]:
        """`after` (UTC ISO) sonrasında oluşturulmuş emirler, bracket bacaklarıyla (nested)."""
        return self._req("GET", "/v2/orders", params={"status": status, "nested": "true", "limit": 500,
                                                      "after": after_iso_utc, "direction": "asc"}) or []

    def replace_order(self, order_id: str, **fields) -> dict:
        body = {k: (f"{v:.2f}" if isinstance(v, float) else str(v)) for k, v in fields.items()}
        return self._req("PATCH", f"/v2/orders/{order_id}", json=body)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._req("DELETE", f"/v2/orders/{order_id}")
            return True
        except BrokerError as e:              # zaten dolmuş/iptal edilmiş
            if " 422" in str(e) or " 404" in str(e):
                return False
            raise

    def get_order(self, order_id: str) -> dict:
        return self._req("GET", f"/v2/orders/{order_id}", params={"nested": "true"})

    def position(self, symbol: str) -> dict | None:
        try:
            return self._req("GET", f"/v2/positions/{symbol}")
        except BrokerError as e:
            if " 404" in str(e):
                return None
            raise

    def cancel_stale_entries(self) -> list[str]:
        """Bugünden önce verilmiş ama dolmamış giriş (parent) emirlerini iptal eder."""
        today = datetime.now(NY).date()
        cancelled = []
        for o in self.open_orders():
            if o.get("side") != "buy" or o.get("status") not in ("new", "accepted", "pending_new"):
                continue
            created = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")).astimezone(NY).date()
            if created < today:
                self._req("DELETE", f"/v2/orders/{o['id']}")
                cancelled.append(o["symbol"])
        return cancelled
