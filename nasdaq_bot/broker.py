"""Broker katmanı.

* AlpacaBroker : gerçek / paper Alpaca hesabı (REST). Long + short bracket emirleri.
* SimBroker    : anahtar gerektirmeyen yerel simülasyon. Canlı 5 dk veriyle stop/hedefleri
                 işletir, durumu state/sim_account.json'da saklar (yeniden başlatmaya dayanıklı).
* market_clock : ABD borsası açık mı, sonraki açılış/kapanış (tatiller ve yarım günler dahil).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .data import NY


class BrokerError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- borsa takvimi
NYSE_HOLIDAYS = {date.fromisoformat(d) for d in [
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

    def __init__(self, key: str, secret: str, paper: bool = True):
        if not key or not secret:
            raise BrokerError("ALPACA_API_KEY / ALPACA_API_SECRET .env dosyasında tanımlı değil.")
        self.base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.paper = paper
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._assets: dict[str, dict] = {}

    @classmethod
    def from_env(cls, cfg: dict) -> "AlpacaBroker":
        return cls(os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_API_SECRET", ""),
                   paper=cfg.get("broker", {}).get("paper", True))

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
                      limit: float | None = None, tif: str = "day") -> dict:
        body = {
            "symbol": symbol, "qty": str(int(qty)), "side": side,
            "type": "limit" if limit else "market", "time_in_force": tif, "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        }
        if limit:
            body["limit_price"] = f"{limit:.2f}"
        return self._req("POST", "/v2/orders", json=body)

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


# ----------------------------------------------------------------------------- simülasyon
class SimBroker:
    """Yerel sanal hesap. Giriş: son kapanan 5 dk mumunun kapanışı (+kayma).
    Stop/hedef sonraki mumların yüksek/düşük değerleriyle kontrol edilir (önce stop varsayılır)."""
    name = "SIM"
    paper = True

    def __init__(self, cfg: dict):
        self.file = Path(cfg["state_dir"]) / "sim_account.json"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        b = cfg["backtest"]
        self.cost = (b["commission_bps"] + b["slippage_bps"]) / 1e4
        start = float(cfg["live"].get("sim_start_equity", 10000))
        if self.file.exists():
            self.st = json.loads(self.file.read_text(encoding="utf-8"))
        else:
            self.st = {"realized": start, "day": None, "last_equity": start, "positions": {}, "closed": []}
        self.prices: dict[str, float] = {}
        self.last_bar: dict[str, str] = {}

    def _save(self):
        self.file.write_text(json.dumps(self.st, indent=2, default=str), encoding="utf-8")

    def _unreal(self, sym: str, p: dict) -> float:
        px = self.prices.get(sym, p["entry"])
        sign = 1 if p["side"] == "long" else -1
        return sign * p["qty"] * (px - p["entry"])

    def equity(self) -> float:
        return self.st["realized"] + sum(self._unreal(s, p) for s, p in self.st["positions"].items())

    def _roll_day(self):
        today = datetime.now(NY).date().isoformat()
        if self.st["day"] != today:
            self.st["day"] = today
            self.st["last_equity"] = self.equity()
            self._save()

    def _exit(self, sym: str, px: float, reason: str):
        p = self.st["positions"].pop(sym)
        fill = px * (1 - self.cost) if p["side"] == "long" else px * (1 + self.cost)
        sign = 1 if p["side"] == "long" else -1
        pnl = sign * p["qty"] * (fill - p["entry"])
        self.st["realized"] += pnl
        self.st["closed"].append({"symbol": sym, "side": p["side"], "qty": p["qty"], "entry": p["entry"],
                                  "exit": fill, "pnl": pnl, "reason": reason, "opened": p["opened"],
                                  "closed": datetime.now(NY).isoformat(timespec="minutes")})
        self.st["closed"] = self.st["closed"][-500:]
        self._save()
        return pnl

    def on_bars(self, bars: dict[str, pd.DataFrame]):
        """Yeni 5 dk mumlarıyla fiyatları günceller ve stop/hedefleri işletir."""
        for sym, df in bars.items():
            if df is not None and len(df):
                self.prices[sym] = float(df["Close"].iloc[-1])
                self.last_bar[sym] = str(df.index[-1])
        for sym in list(self.st["positions"]):
            p = self.st["positions"][sym]
            df = bars.get(sym)
            if df is None or df.empty:
                continue
            new = df[df.index > pd.Timestamp(p["last_ts"])]
            for ts, row in new.iterrows():
                o, h, l = row["Open"], row["High"], row["Low"]
                if p["side"] == "long":
                    if l <= p["stop"]:
                        self._exit(sym, min(o, p["stop"]), "stop"); break
                    if h >= p["target"]:
                        self._exit(sym, max(o, p["target"]), "hedef"); break
                else:
                    if h >= p["stop"]:
                        self._exit(sym, max(o, p["stop"]), "stop"); break
                    if l <= p["target"]:
                        self._exit(sym, min(o, p["target"]), "hedef"); break
                p["last_ts"] = str(ts)
        self._save()

    # Alpaca ile aynı arayüz
    def account(self) -> dict:
        self._roll_day()
        eq = self.equity()
        return {"equity": eq, "last_equity": self.st["last_equity"], "cash": eq, "daytrade_count": 0}

    def clock(self) -> dict:
        return local_clock()

    def positions(self) -> list[dict]:
        out = []
        for s, p in self.st["positions"].items():
            u = self._unreal(s, p)
            out.append({"symbol": s, "side": p["side"], "qty": p["qty"], "avg_entry_price": p["entry"],
                        "current_price": self.prices.get(s, p["entry"]), "unrealized_pl": u,
                        "unrealized_plpc": u / (p["qty"] * p["entry"])})
        return out

    def open_orders(self) -> list[dict]:
        return []

    def is_shortable(self, symbol: str) -> bool:
        return True

    def place_bracket(self, symbol: str, qty: int, side: str, take_profit: float, stop_loss: float,
                      limit: float | None = None, tif: str = "day") -> dict:
        px = self.prices.get(symbol)
        if px is None:
            raise BrokerError(f"{symbol} için fiyat yok")
        fill = px * (1 + self.cost) if side == "buy" else px * (1 - self.cost)
        self.st["positions"][symbol] = {
            "side": "long" if side == "buy" else "short", "qty": int(qty), "entry": fill,
            "stop": stop_loss, "target": take_profit, "last_ts": self.last_bar.get(symbol, "1970-01-01"),
            "opened": datetime.now(NY).isoformat(timespec="minutes"),
        }
        self._save()
        return {"id": f"sim-{symbol}", "status": "filled"}

    def close_position(self, symbol: str):
        if symbol in self.st["positions"]:
            p = self.st["positions"][symbol]
            self._exit(symbol, self.prices.get(symbol, p["entry"]), "manuel/sinyal")

    def close_all(self):
        for s in list(self.st["positions"]):
            self.close_position(s)

    def cancel_stale_entries(self) -> list[str]:
        return []


def get_clock(broker) -> dict:
    try:
        return broker.clock() if broker else local_clock()
    except Exception:  # noqa: BLE001
        return local_clock()
