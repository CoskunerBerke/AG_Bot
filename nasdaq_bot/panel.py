"""Yerel izleme paneli (salt okunur): http://127.0.0.1:8765

İki bot yan yana: Bot A (.env'deki ALPACA_API_KEY hesabı, tüm modeller) ve Bot B (ALPACA_B_API_KEY /
ALPACA_B_API_SECRET varsa; 10AM sadece izlenir). Alpaca hesapları her 10 sn, Oracle sunucusundaki loglar ve işlem
günlükleri her 60 sn (ssh ile) yenilenir. Sunucu için .env: ORACLE_SSH=ubuntu@<ip> (anahtar ~/.ssh/oracle_agbot.key,
değiştirmek için ORACLE_SSH_KEY). Sadece 127.0.0.1'e bağlanır; emir göndermez.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, time as dtime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from .broker import AlpacaBroker
from .common import NY

PAGE = Path(__file__).with_name("panel.html")
DURUM = {"new": "bekliyor", "accepted": "bekliyor", "held": "bekliyor", "pending_new": "bekliyor",
         "filled": "doldu", "partially_filled": "kısmen doldu", "canceled": "iptal", "expired": "süresi doldu",
         "rejected": "reddedildi", "pending_cancel": "iptal ediliyor", "replaced": "değiştirildi"}
BOTS = {"a": ("nasdaq_trading_bot", "logs/dmc_live.log", "state/dmc_journal.json"),
        "b": ("nasdaq_trading_bot_b", "logs/dmc_live_b.log", "state/b/dmc_journal.json")}
JOURNAL = "python3 -c \"import json;print(json.dumps(json.load(open('{}'))[-20:]))\" 2>/dev/null || echo []"
REMOTE = "cd ~/AG_Bot && git log -1 --format='%h %s'; echo @@; " + "; echo @@; ".join(
    f"(sudo docker ps --filter name=^{c}$ --format '{{{{.Status}}}}' | head -1); echo @@; "
    f"grep -vE 'indiriliyor|piyasa kapalı' {log} 2>/dev/null | tail -n 80; echo @@; " + JOURNAL.format(j)
    for c, log, j in BOTS.values())


def _ny(ts: str | None) -> str:
    return pd.Timestamp(ts).tz_convert(NY).strftime("%H:%M:%S") if ts else ""


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


def _realized(raw: list[dict]) -> tuple[dict[int, float], int]:
    """Gerçekleşen K/Z: her sembolde tüm dolumlar (ana emir + bracket bacakları) zaman sırasıyla FIFO eşleşir.
    Kapanış dolumunun K/Z'si o dolumun ana emir satırına yazılır; emir tipinden bağımsızdır (kısmi dolum, elle
    kapatılan pozisyon, OCO dahil). Dönüş: ({satır: K/Z}, sıfırdan açılan pozisyon sayısı)."""
    fills = []
    for i, o in enumerate(raw):
        for x in [o] + (o.get("legs") or []):
            q = float(x.get("filled_qty") or 0)
            if q > 0 and x.get("filled_avg_price"):
                fills.append((x.get("filled_at") or "", o["symbol"], q if x["side"] == "buy" else -q,
                              float(x["filled_avg_price"]), i))
    kz: dict[int, float] = {}
    pos: dict[str, list] = {}                       # sembol -> [[adet (±), fiyat], ...]
    opened = 0
    for _, sym, q, px, i in sorted(fills):
        lots = pos.setdefault(sym, [])
        if not lots:
            opened += 1
        while q and lots and (lots[0][0] > 0) != (q > 0):
            take = min(abs(q), abs(lots[0][0])) * (1 if q > 0 else -1)   # kapanan adet (q yönünde)
            kz[i] = kz.get(i, 0.0) + (lots[0][1] - px) * take            # long kapanış: take<0 → (px-giriş)*|take|
            lots[0][0] += take
            q -= take
            if lots[0][0] == 0:
                lots.pop(0)
        if q:
            lots.append([q, px])
    return kz, opened


def _log_rows(text: str) -> list[dict]:
    rows = []
    for ln in text.splitlines():
        bits = ln.split(" ", 4)                          # 2026-10-12 10:05:07,005 INFO dmc.live mesaj
        if len(bits) == 5 and bits[2] in ("INFO", "WARNING", "ERROR"):
            rows.append({"t": bits[1][:8], "seviye": bits[2], "mesaj": bits[4]})
        elif rows:                                       # traceback devamı
            rows[-1]["mesaj"] += "\n" + ln
    return rows[-60:]


class Panel:
    def __init__(self, cfg: dict):
        self.brokers = {"a": AlpacaBroker.from_env(cfg)}
        kb, sb = os.getenv("ALPACA_B_API_KEY", ""), os.getenv("ALPACA_B_API_SECRET", "")
        if kb and sb:
            self.brokers["b"] = AlpacaBroker(kb, sb, paper=True)
        self.ssh = os.getenv("ORACLE_SSH", "")
        self.key = os.getenv("ORACLE_SSH_KEY") or str(Path.home() / ".ssh" / "oracle_agbot.key")
        self.data: dict = {f"hesap_{n}": {"hata": "yükleniyor…"} for n in self.brokers}
        self.data["sunucu"] = {"hata": "yükleniyor…"}
        if "b" not in self.brokers:
            self.data["hesap_b"] = {"hata": "Bot B hesabı tanımlı değil (.env: ALPACA_B_API_KEY, ALPACA_B_API_SECRET)"}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ Alpaca
    def hesap(self, b: AlpacaBroker) -> dict:
        clk, a = b.clock(), b.account()
        day = clk["timestamp"].date()
        after = datetime.combine(day, dtime(0, 0), NY).astimezone(timezone.utc).isoformat()
        raw = b.orders_after(after)
        kz, opened = _realized(raw)
        orders = []
        for i, o in enumerate(raw):
            legs = o.get("legs") or []
            tp = next((x for x in legs if x["type"] == "limit"), None)
            sl = next((x for x in legs if x["type"] in ("stop", "stop_limit")), None)
            ex = next((x for x in legs if x["status"] == "filled"), None)
            orders.append({"saat": _ny(o.get("submitted_at")), "sembol": o["symbol"],
                           "yon": "long" if o["side"] == "buy" else "short", "adet": o["qty"],
                           "giris": _f(o.get("limit_price")), "stop": _f(sl and sl.get("stop_price")),
                           "tp": _f(tp and tp.get("limit_price")), "durum": DURUM.get(o["status"], o["status"]),
                           "dolum": _f(o.get("filled_avg_price")), "cikis": _f(ex and ex.get("filled_avg_price")),
                           "cikis_tur": ("hedef" if ex is tp else "stop") if ex else "", "kz": kz.get(i)})
        pos = [{"sembol": p["symbol"], "yon": "long" if p["side"] == "long" else "short", "adet": p["qty"],
                "giris": _f(p["avg_entry_price"]), "fiyat": _f(p["current_price"]), "kz": _f(p["unrealized_pl"])}
               for p in b.positions()]
        eq, last = float(a["equity"]), float(a["last_equity"])
        return {"piyasa_acik": clk["is_open"], "ny": clk["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "sonraki": (clk["next_close"] if clk["is_open"] else clk["next_open"]).strftime("%a %H:%M"),
                "bakiye": eq, "gun_kz": eq - last, "gun_kz_pct": (eq - last) / last * 100 if last else 0.0,
                "pozisyonlar": pos, "emirler": orders,
                "dolan": opened}

    # ------------------------------------------------------------------ sunucu
    def sunucu(self) -> dict:
        if not self.ssh:
            return {"hata": "Sunucu bağlı değil: .env dosyasına ORACLE_SSH=ubuntu@<ip> ekle."}
        ssh = shutil.which("ssh")
        if not ssh:
            return {"hata": "ssh komutu bulunamadı."}
        r = subprocess.run([ssh, "-i", self.key, "-o", "ConnectTimeout=30", "-o", "BatchMode=yes", self.ssh, REMOTE],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        parts = r.stdout.split("@@\n")
        if len(parts) < 1 + 3 * len(BOTS):
            return {"hata": "Sunucuya bağlanılamadı: " + ((r.stderr or r.stdout).strip()[-200:] or "yanıt yok")}
        out = {"surum": parts[0].strip()}
        for i, n in enumerate(BOTS):
            durum, logs, journal = parts[1 + 3 * i: 4 + 3 * i]
            try:
                islemler = json.loads(journal.strip() or "[]")
            except json.JSONDecodeError:
                islemler = []
            durum = durum.strip()
            out[n] = {"durum": durum or "çalışmıyor", "calisiyor": durum.startswith("Up"),
                      "log": _log_rows(logs), "islemler": islemler[::-1]}
        return out

    # ------------------------------------------------------------------ döngü
    def _loop(self, name: str, fn, every: int):
        while True:
            try:
                v = fn()
            except Exception as e:                       # ağ/API hatası panelde gösterilsin, döngü sürsün
                v = {"hata": f"{type(e).__name__}: {e}"[:300]}
            v["guncellendi"] = datetime.now(NY).strftime("%H:%M:%S")
            with self.lock:
                self.data[name] = v
            time.sleep(every)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.data)


def serve(cfg: dict, port: int = 8765) -> None:
    p = Panel(cfg)
    for n, b in p.brokers.items():
        threading.Thread(target=p._loop, args=(f"hesap_{n}", lambda b=b: p.hesap(b), 10), daemon=True).start()
    threading.Thread(target=p._loop, args=("sunucu", p.sunucu, 60), daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api"):
                body, ctype = json.dumps(p.snapshot(), default=str).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Panel: http://127.0.0.1:{port}  (kapatmak için Ctrl+C)", flush=True)
    srv.serve_forever()
