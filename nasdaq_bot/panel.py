"""Yerel izleme paneli (salt okunur): http://127.0.0.1:8765

Alpaca paper hesabı (bakiye, pozisyonlar, bugünün emirleri) her 10 sn, Oracle sunucusundaki bot logu ve işlem
günlüğü her 60 sn (ssh ile) yenilenir. Sunucu için .env: ORACLE_SSH=ubuntu@<ip> (anahtar: ~/.ssh/oracle_agbot.key,
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
REMOTE = ("cd ~/AG_Bot && (sudo docker compose ps --format '{{.Status}}' 2>/dev/null | head -1); echo @@; "
          "git log -1 --format='%h %s'; echo @@; "
          "grep -vE 'indiriliyor|piyasa kapalı' logs/dmc_live.log 2>/dev/null | tail -n 80; echo @@; "
          "python3 -c \"import json;print(json.dumps(json.load(open('state/dmc_journal.json'))[-20:]))\" "
          "2>/dev/null || echo []")


def _ny(ts: str | None) -> str:
    return pd.Timestamp(ts).tz_convert(NY).strftime("%H:%M:%S") if ts else ""


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


class Panel:
    def __init__(self, cfg: dict):
        self.broker = AlpacaBroker.from_env(cfg)
        self.ssh = os.getenv("ORACLE_SSH", "")
        self.key = os.getenv("ORACLE_SSH_KEY") or str(Path.home() / ".ssh" / "oracle_agbot.key")
        self.data: dict = {"hesap": {"hata": "yükleniyor…"}, "sunucu": {"hata": "yükleniyor…"}}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ Alpaca
    def hesap(self) -> dict:
        b = self.broker
        clk, a = b.clock(), b.account()
        day = clk["timestamp"].date()
        after = datetime.combine(day, dtime(0, 0), NY).astimezone(timezone.utc).isoformat()
        orders = []
        for o in b.orders_after(after):
            legs = o.get("legs") or []
            tp = next((x for x in legs if x["type"] == "limit"), None)
            sl = next((x for x in legs if x["type"] in ("stop", "stop_limit")), None)
            ex = next((x for x in legs if x["status"] == "filled"), None)
            fill = _f(o.get("filled_avg_price"))
            pnl = None
            if fill and ex:
                sgn = 1 if o["side"] == "buy" else -1
                pnl = sgn * (float(ex["filled_avg_price"]) - fill) * float(ex["filled_qty"])
            orders.append({"saat": _ny(o.get("submitted_at")), "sembol": o["symbol"],
                           "yon": "long" if o["side"] == "buy" else "short", "adet": o["qty"],
                           "giris": _f(o.get("limit_price")), "stop": _f(sl and sl.get("stop_price")),
                           "tp": _f(tp and tp.get("limit_price")), "durum": DURUM.get(o["status"], o["status"]),
                           "dolum": fill, "cikis": _f(ex and ex.get("filled_avg_price")),
                           "cikis_tur": ("hedef" if ex is tp else "stop") if ex else "", "kz": pnl,
                           "giris_emri": bool(legs)})
        pos = [{"sembol": p["symbol"], "yon": "long" if p["side"] == "long" else "short", "adet": p["qty"],
                "giris": _f(p["avg_entry_price"]), "fiyat": _f(p["current_price"]), "kz": _f(p["unrealized_pl"])}
               for p in b.positions()]
        eq, last = float(a["equity"]), float(a["last_equity"])
        return {"piyasa_acik": clk["is_open"], "ny": clk["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "sonraki": (clk["next_close"] if clk["is_open"] else clk["next_open"]).strftime("%a %H:%M"),
                "bakiye": eq, "gun_kz": eq - last, "gun_kz_pct": (eq - last) / last * 100 if last else 0.0,
                "pozisyonlar": pos, "emirler": orders,
                "dolan": sum(1 for o in orders if o["giris_emri"] and o["dolum"])}

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
        if len(parts) < 4:
            return {"hata": "Sunucuya bağlanılamadı: " + ((r.stderr or r.stdout).strip()[-200:] or "yanıt yok")}
        durum, commit, logs, journal = parts[0].strip(), parts[1].strip(), parts[2], parts[3]
        satirlar = []
        for ln in logs.splitlines():
            bits = ln.split(" ", 4)                      # 2026-10-12 10:05:07,005 INFO dmc.live mesaj
            if len(bits) == 5 and bits[2] in ("INFO", "WARNING", "ERROR"):
                satirlar.append({"t": bits[1][:8], "seviye": bits[2], "mesaj": bits[4]})
            elif satirlar:                               # traceback devamı
                satirlar[-1]["mesaj"] += "\n" + ln
        try:
            islemler = json.loads(journal.strip() or "[]")
        except json.JSONDecodeError:
            islemler = []
        return {"durum": durum or "konteyner çalışmıyor", "calisiyor": durum.startswith("Up"), "surum": commit,
                "log": satirlar[-60:], "islemler": islemler[::-1]}

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
    threading.Thread(target=p._loop, args=("hesap", p.hesap, 10), daemon=True).start()
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
