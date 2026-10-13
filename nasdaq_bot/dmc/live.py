"""DMC canlı yürütücü (Alpaca). Backtest ile aynı DayEngine'i 1 dk IEX barlarıyla besler.

Döngü (her dakika +5 sn):
  1. Yeni kapanmış barları çek → engine.on_bar → niyetler
  2. Niyet → emir: iki bracket (a = TP1 parçası, b = TP2 parçası), client_order_id = dmc-<gün>-<sembol>-<tag>-v<n>-<a|b>
  3. Broker senkronu: dolum → engine.on_fill; a'nın TP'si dolunca b'nin stop'u girişe (BE); kapanış → engine.on_exit
  4. flat_time'da tüm DMC emirleri iptal, QQQ pozisyonu kapat

Yeniden başlatmaya dayanıklı: gün içinde açılırsa sabahtan beri barları yeniden oynatır; daha önce verilmiş emirler
client_order_id ile tanınır, state/dmc_live.json'daki dolmuş setup'lar tekrar açılmaz.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ..broker import AlpacaBroker, BrokerError
from ..common import NY
from .data import fetch_minutes, load_history, peer_symbol, rth_daily
from .engine import DayEngine
from .levels import am_range_median, am_ranges, prepare_day
from .news import blocked, fetch_events
from .setups import Intent

log = logging.getLogger("dmc.live")


def _naive(ts: datetime) -> pd.Timestamp:
    return pd.Timestamp(ts.astimezone(NY).replace(tzinfo=None))


def _intent(d: dict) -> Intent:
    """Durum dosyasındaki niyet sözlüğü → Intent."""
    return Intent(**{k: (pd.Timestamp(v) if k in ("created", "valid_until") and v else v) for k, v in d.items()})


def size_position(equity: float, bp: float, entry: float, stop: float, risk_pct: float, bp_usage: float,
                  bp_full: float | None = None, slots: int = 1) -> int:
    """Risk tabanlı adet; alım gücü tavanıyla sınırlı (dar stoplarda gerçek risk hedefin altında kalır).
    bp = şu an kalan alım gücü; bp_full = toplam (özvarlık × çarpan). Bekleyen her giriş emri Alpaca'da alım gücü
    bağladığından toplam, `slots` kadar eş zamanlı emre bölünür — ilk emir hepsini yiyip diğerlerini reddettirmesin."""
    rps = abs(entry - stop)
    if rps <= 0 or entry <= 0:
        return 0
    cap = bp * bp_usage
    if bp_full:
        cap = min(cap, bp_full * bp_usage / max(1, slots))
    return max(0, math.floor(min(equity * risk_pct / 100 / rps, cap / entry)))


class DmcLive:
    def __init__(self, cfg: dict, broker: AlpacaBroker | None, dry_run: bool = False, symbol: str | None = None,
                 shared: dict | None = None):
        self.cfg, self.d = cfg, cfg["dmc"]
        self.sym = symbol or self.d["symbol"]
        self.trade = self.sym.upper() not in {s.upper() for s in self.d.get("watch_only") or []}  # izleme: emir yok
        self.shared = shared if shared is not None else {"trades": 0, "open": set(), "day": None}
        self.broker = broker
        self.dry = dry_run or broker is None
        self.state_file = Path(cfg["state_dir"]) / f"dmc_live_{self.sym.replace('/', '')}.json"
        self.journal_file = Path(cfg["state_dir"]) / "dmc_journal.json"
        self.day: date | None = None
        self.engine: DayEngine | None = None
        self.last_ts: pd.Timestamp | None = None
        self.bars = pd.DataFrame()
        self.events = None
        self.st: dict = {}

    # ------------------------------------------------------------------ durum
    def _load_state(self, day: date) -> dict:
        if self.state_file.exists():
            st = json.loads(self.state_file.read_text(encoding="utf-8"))
            if st.get("day") == day.isoformat():
                return st
        return {"day": day.isoformat(), "tags": {}, "filled_setups": [], "flat_done": False}

    def _save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.state_file)

    def _journal(self, rec: dict):
        rows = json.loads(self.journal_file.read_text(encoding="utf-8")) if self.journal_file.exists() else []
        rows.append(rec)
        tmp = self.journal_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.journal_file)

    # ------------------------------------------------------------------ gün başlangıcı
    def start_day(self, day: date, now: pd.Timestamp):
        """Seviyeleri hazırla, motoru kur, bugünün kapanmış barlarını yeniden oynat."""
        self.day, self.st = day, self._load_state(day)
        hist = load_history(self.cfg, self.sym, day - timedelta(days=45), day - timedelta(days=1))
        # Bugün: 15 dk'dan eski kısım SIP (tam hacim; premarket H/L doğru), son 15 dk IEX (gerçek zamanlı, seyrek)
        start = datetime.combine(day, dtime(4, 0), NY)
        now_aw = now.to_pydatetime().replace(tzinfo=NY)
        sip_end = min(now_aw, datetime.now(NY) - timedelta(minutes=16))
        sip = fetch_minutes(self.sym, start, sip_end, feed="sip") if sip_end > start else pd.DataFrame()
        iex_from = (sip.index.max() + pd.Timedelta(minutes=1)).to_pydatetime().replace(tzinfo=NY) if len(sip) else start
        iex = fetch_minutes(self.sym, iex_from, now_aw, feed="iex") if iex_from < now_aw else pd.DataFrame()
        today = pd.concat([x for x in (sip, iex) if len(x)]) if (len(sip) or len(iex)) else sip
        today = today[today.index + pd.Timedelta(minutes=1) <= now] if len(today) else today
        allb = pd.concat([hist, today])
        lv = prepare_day(allb, day, daily=rth_daily(hist), fvg_cfg=self.d.get("fvg"))
        self.engine = DayEngine(self.cfg, lv, {"am_range_median": am_range_median(am_ranges(hist), day),
                                               "vol_mult": (self.d.get("vol_mult") or {}).get(self.sym.upper(), 1)})
        self.events = fetch_events(self.cfg["state_dir"])
        log.info("GÜN %s bias=%s PDH=%s PDL=%s PMH=%s PML=%s FVG=%d haber=%s", day, lv.bias, lv.pdh, lv.pdl, lv.pmh,
                 lv.pml, len(lv.fvgs), "yok" if self.events is None else
                 [f"{e.t:%H:%M} {e.title}" for e in self.events if e.t.date() == day and e.impact == "High"])
        self.bars = today[today.index.time >= dtime(9, 30)]
        self.last_ts = None
        self._replay: dict[str, Intent] = {}
        for ts, row in self.bars.iterrows():                 # yeniden oynatma: emir verilmez, son durum toplanır
            self._feed(ts, row, now, replay=True)
        for s in self.st["filled_setups"]:                   # önceki süreçte dolmuş setup'lar kapalı kalsın
            if s not in self.engine.done:
                self.engine.done.add(s)
                self.engine.trades += 1
        self._cleanup_old_orders()
        for r_ in self.st["tags"].values():                    # yeniden başlatma: bugünkü dolumlar ortak limite
            if r_.get("filled"):
                self.shared["trades"] += 1
                if not r_.get("exited"):
                    self.shared["open"].add(self.sym)
        for it in self._replay.values():                     # oynatma sonunda hâlâ geçerli olan niyetler
            if it.kind == "limit" and self._traded_through(it):
                log.info("OYNATMA: %s girişi %.2f zaten işlem gördü → geç giriş yapılmaz", it.tag, it.entry)
                continue
            if it.kind == "limit" or it.created >= now - pd.Timedelta(minutes=2):
                self._handle(it, now, replay=False)
        self._replay = {}

    def _cleanup_old_orders(self):
        if self.dry:
            return
        prefix = f"dmc-{self.day:%Y%m%d}"
        for o in self.broker.open_orders():
            cid = o.get("client_order_id") or ""
            if cid.startswith("dmc-") and not cid.startswith(prefix):
                log.warning("eski gün DMC emri iptal: %s", cid)
                self.broker.cancel_order(o["id"])

    # ------------------------------------------------------------------ bar → niyet → emir
    def _feed(self, ts, row, now: pd.Timestamp, replay: bool = False):
        self.last_ts = ts
        if replay:
            for it in list(self._replay.values()):           # oynatmada TP1'e dolmadan ulaşılan limit = kaçmış
                cit = it.cancel_if_through
                if it.kind == "limit" and cit is not None and (
                        (it.side == "long" and row.High >= cit) or (it.side == "short" and row.Low <= cit)):
                    self._replay.pop(it.tag, None)
        for it in self.engine.on_bar(ts, row.Open, row.High, row.Low, row.Close, row.Volume):
            if replay:
                if it.kind == "cancel":
                    self._replay.pop(it.tag, None)
                else:
                    self._replay[it.tag] = it
            else:
                self._handle(it, now, replay)
        if not replay:
            self._check_cancel_through(row)

    def _handle(self, it: Intent, now: pd.Timestamp, replay: bool):
        tags = self.st["tags"]
        rec = tags.get(it.tag)
        if it.kind == "cancel":
            if rec and not rec.get("filled"):
                self._cancel_tag(it.tag, "motor iptali")
            return
        if it.setup in self.st["filled_setups"] or (rec and rec.get("filled")):
            return
        if it.valid_until is not None and it.valid_until <= now:
            return
        f = self.d.get("filters", {})
        why = blocked(self.events, now, f.get("red_news_block_minutes", 5), f.get("speech_block_minutes", 30))
        if why and (why != "news_unavailable" or f.get("fail_closed_news", True)):
            log.info("HABER FİLTRESİ %s → %s atlandı", why, it.tag)
            return
        key = (round(it.entry, 2), round(it.stop, 2), it.tp1 and round(it.tp1, 2), round(it.tp2, 2), it.kind)
        if rec and rec.get("orders") and self._same_entry(rec.get("key", ()), key):
            return                                           # giriş/stop aynı: hedef oynadı diye emir yenilenmez
        if not self.trade or it.setup in (self.d.get("watch_setups") or []):   # izleme: "ne yapardı" kaydı
            if not rec or not self._same_entry(rec.get("key", ()), key):
                log.info("İZLEME %s %s %s giriş %.2f stop %.2f tp2 %.2f (işlem açılmaz)", self.sym, it.tag, it.side,
                         it.entry, it.stop, it.tp2)
                self.st["tags"][it.tag] = {**self._new_rec(it, key, 0), "watch": True}
                self._save()
            return
        if not self._limits_ok():
            log.info("ORTAK LİMİT (%s işlem, açık %s) → %s %s atlandı", self.shared["trades"],
                     sorted(self.shared["open"]), self.sym, it.tag)
            return
        if rec and rec.get("orders"):
            self._cancel_tag(it.tag, "yeniden çapalama")
        self._submit(it, key, (rec or {}).get("version", 0) + 1)

    @staticmethod
    def _same_entry(a, b) -> bool:
        """Anahtar = (giriş, stop, tp1, tp2, tür). Giriş, stop ve tür aynıysa aynı emir sayılır."""
        a, b = tuple(a), tuple(b)
        return len(a) == len(b) == 5 and a[0] == b[0] and a[1] == b[1] and a[4] == b[4]

    @staticmethod
    def _new_rec(it: Intent, key, version: int) -> dict:
        return {"intent": {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in asdict(it).items()},
                "key": list(key), "version": version, "orders": [], "filled": False, "exited": False, "be": False}

    def _submit(self, it: Intent, key, version: int):
        prev = self.st["tags"].get(it.tag) or {}
        tag_rec = self._new_rec(it, key, version)
        if self.dry:
            log.info("[DRY] %s %s %s giriş %.2f stop %.2f tp1 %s tp2 %.2f conf=%s", it.kind, it.side, it.tag, it.entry,
                     it.stop, it.tp1, it.tp2, it.confluence)
            self.st["tags"][it.tag] = tag_rec
            self._save()
            return
        if it.kind == "limit" and len(self.bars):           # fiyat girişin ötesinde: limit anında piyasadan dolar
            px = float(self.bars["Close"].iloc[-1])
            if (it.side == "long" and px <= it.entry) or (it.side == "short" and px >= it.entry):
                log.info("GEÇ %s: fiyat %.2f girişi %.2f geçmiş → gönderilmedi", it.tag, px, it.entry)
                self.st["tags"][it.tag] = tag_rec
                self._save()
                return
        opp = self._opposite(it.side, it.tag)
        if opp:                                              # Alpaca aynı sembolde alış + açığa satışı birlikte bekletmez
            if not self._nearer(it.entry, opp[1]["intent"]["entry"]):
                tag_rec["wait"] = True
                self.st["tags"][it.tag] = tag_rec
                self._save()
                if not prev.get("wait"):
                    log.info("SIRADA %s (ters yönde %s bekliyor, fiyata o daha yakın)", it.tag, opp[0])
                return
            self._cancel_tag(opp[0], f"{it.tag} fiyata daha yakın")
            opp[1]["wait"] = True
        if not self._admit(it, tag_rec, bool(prev.get("wait"))):
            return
        acct = self.broker.account()
        equity = float(acct["equity"])
        bp = float(acct.get("buying_power") or acct.get("daytrading_buying_power") or 0)
        qty = size_position(equity, bp, it.entry, it.stop, self.d["risk_per_trade_pct"], self.d.get("bp_usage", 0.9),
                            equity * float(acct.get("multiplier") or 1), self.d.get("bp_slots", 1))
        q1 = math.floor(qty * self.d["tp1_fraction"]) if it.tp1 is not None else 0
        parts = [("a", q1, it.tp1), ("b", qty - q1, it.tp2)] if q1 > 0 else [("b", qty, it.tp2)]
        side = "buy" if it.side == "long" else "sell"
        for leg, q, tp in parts:
            if q <= 0:
                continue
            body = {"symbol": self.sym, "qty": str(q), "side": side, "type": it.kind, "time_in_force": "gtc",
                    "order_class": "bracket", "client_order_id": f"dmc-{self.day:%Y%m%d}-{self.sym.replace('/', '')}-{it.tag}-v{version}-{leg}",
                    "take_profit": {"limit_price": f"{tp:.2f}"}, "stop_loss": {"stop_price": f"{it.stop:.2f}"}}
            if it.kind == "limit":
                body["limit_price"] = f"{it.entry:.2f}"
            try:
                o = self.broker.submit(body)
                tag_rec["orders"].append({"leg": leg, "id": o["id"], "qty": q})
                log.info("EMİR %s %s %s x%d giriş %s stop %.2f tp %.2f (%s)", it.tag, leg, side, q,
                         body.get("limit_price", "market"), it.stop, tp, ",".join(it.confluence))
            except BrokerError as e:
                log.error("emir reddedildi %s: %s", body["client_order_id"], e)
                if ("while a" in str(e) or "buying power" in str(e)) and not tag_rec["orders"]:
                    tag_rec["wait"] = True                   # yer açan iptal henüz işlenmedi → sonraki dk
        self.st["tags"][it.tag] = tag_rec
        self._save()

    def _traded_through(self, it: Intent) -> bool:
        """Niyet üretildikten sonra fiyat limit girişine değdi mi (o emir backtest'te çoktan dolmuş olurdu)."""
        b = self.bars[self.bars.index >= it.created] if it.created is not None else self.bars
        if not len(b):
            return False
        return bool((b["Low"] <= it.entry).any()) if it.side == "long" else bool((b["High"] >= it.entry).any())

    def _dist(self, intent: dict) -> float:
        """Girişin son fiyata uzaklığı, stop mesafesi (R) cinsinden — farklı fonlar karşılaştırılabilsin."""
        if not len(self.bars):
            return float("inf")
        px = float(self.bars["Close"].iloc[-1])
        return abs(intent["entry"] - px) / (abs(intent["entry"] - intent["stop"]) or 1e-9)

    def _resting(self, skip_tag: str) -> list[tuple[float, "DmcLive", str]]:
        """Tüm sembollerde Alpaca'da bekleyen (dolmamış) girişler: (R uzaklık, bot, tag)."""
        out = []
        for b in (self.shared.get("bots") or {self.sym: self}).values():
            for t, r in b.st.get("tags", {}).items():
                if r.get("orders") and not r.get("filled") and not (b is self and t == skip_tag):
                    out.append((b._dist(r["intent"]), b, t))
        return out

    def _admit(self, it: Intent, tag_rec: dict, was_waiting: bool) -> bool:
        """Alım gücü `bp_slots` emre bölünür → pozisyonlar büyük kalsın diye aynı anda en fazla o kadar giriş
        Alpaca'da bekler: fiyata (R olarak) en yakın olanlar. Yeni giriş en uzaktakinden belirgin yakınsa onun
        yerini alır (o sıraya girer), değilse kendisi sıraya girer; her dakika yeniden değerlendirilir."""
        rest = self._resting(it.tag)
        if len(rest) < self.d.get("bp_slots", 1):
            return True
        far = max(rest, key=lambda x: x[0])
        if self._dist(tag_rec["intent"]) < 0.8 * far[0]:
            far[1]._cancel_tag(far[2], f"{self.sym} {it.tag} fiyata daha yakın")
            far[1].st["tags"][far[2]]["wait"] = True
            far[1]._save()
            return True
        tag_rec["wait"] = True
        self.st["tags"][it.tag] = tag_rec
        self._save()
        if not was_waiting:
            log.info("SIRADA %s %s (alım gücü %d emirde, onlar fiyata daha yakın)", self.sym, it.tag, len(rest))
        return False

    def _opposite(self, side: str, tag: str) -> tuple[str, dict] | None:
        """Bu sembolde Alpaca'da bekleyen, ters yönlü, dolmamış giriş."""
        for t, r in self.st["tags"].items():
            if t != tag and not r.get("filled") and r.get("orders") and r["intent"]["side"] != side:
                return t, r
        return None

    def _nearer(self, entry: float, other: float) -> bool:
        """entry, son fiyata other'dan belirgin şekilde (%20) daha yakın mı (her dakika yer değiştirmesin)."""
        if not len(self.bars):
            return False
        px = float(self.bars["Close"].iloc[-1])
        return abs(entry - px) < 0.8 * abs(other - px)

    def _limits_ok(self) -> bool:
        lim_t = self.d.get("max_trades_total", self.d["max_trades_per_day"])
        return self.shared["trades"] < lim_t and len(self.shared["open"] - {self.sym}) < self.d.get("max_open_total", 1)

    def _retry_waiting(self, now: pd.Timestamp):
        """Sırada bekleyen girişler: ters yönlü emir kalktıysa ya da bu taraf fiyata daha yakınsa gönder."""
        for tag, rec in list(self.st["tags"].items()):
            if not rec.get("wait") or rec.get("filled") or rec.get("orders"):
                continue
            it = _intent(rec["intent"])
            if (it.valid_until is not None and now >= it.valid_until) or it.setup in self.st["filled_setups"]:
                rec["wait"] = False
                continue
            f = self.d.get("filters", {})
            why = blocked(self.events, now, f.get("red_news_block_minutes", 5), f.get("speech_block_minutes", 30))
            if not self._limits_ok() or (why and (why != "news_unavailable" or f.get("fail_closed_news", True))):
                continue
            opp = self._opposite(it.side, tag)
            if opp is None or self._nearer(it.entry, opp[1]["intent"]["entry"]):
                self._submit(it, tuple(rec["key"]), rec.get("version", 0) + 1)
        self._save()

    def _cancel_tag(self, tag: str, why: str):
        rec = self.st["tags"].get(tag)
        if not rec:
            return
        for o in rec.get("orders", []):
            if not self.dry:
                self.broker.cancel_order(o["id"])
        log.info("İPTAL %s (%s)", tag, why)
        rec["orders"] = []
        rec["wait"] = False
        self._save()

    def cancel_pending(self, why: str):
        """Dolmamış tüm giriş emirlerini iptal et (açık pozisyon limiti dolunca ikinci pozisyon açılmasın)."""
        for tag, rec in self.st.get("tags", {}).items():
            if not rec.get("filled") and (rec.get("orders") or rec.get("wait")):
                self._cancel_tag(tag, why)

    def _check_cancel_through(self, row):
        """Dolmamış limit, TP1 seviyesine dolmadan ulaşıldıysa kaçmış işlemdir → iptal (backtest ile aynı kural)."""
        for tag, rec in self.st["tags"].items():
            it = rec["intent"]
            if rec.get("filled") or not (rec.get("orders") or rec.get("wait")) or it["kind"] != "limit" \
                    or it["cancel_if_through"] is None:
                continue
            cit = it["cancel_if_through"]
            if (it["side"] == "long" and row.High >= cit) or (it["side"] == "short" and row.Low <= cit):
                self._cancel_tag(tag, "TP1 dolmadan aşıldı")

    # ------------------------------------------------------------------ broker senkronu
    def sync(self, now: pd.Timestamp):
        if self.dry:
            return
        after = datetime.combine(self.day, dtime(0, 0), NY).astimezone(timezone.utc).isoformat()
        by_id = {o["id"]: o for o in self.broker.orders_after(after)}
        for tag, rec in self.st["tags"].items():
            if rec.get("exited") or not rec.get("orders"):
                continue
            it = rec["intent"]
            parents = [by_id.get(o["id"]) for o in rec["orders"]]
            parents = [p for p in parents if p]
            filled = [p for p in parents if p["status"] in ("filled", "partially_filled")]
            if filled and not rec["filled"]:
                rec["filled"] = True
                self.shared["trades"] += 1
                self.shared["open"].add(self.sym)
                setup = it["setup"]
                if setup not in self.st["filled_setups"]:
                    self.st["filled_setups"].append(setup)
                    self.engine.on_fill(_intent(it))
                log.info("DOLUM %s @ %s", tag, [p.get("filled_avg_price") for p in filled])
            if not rec["filled"]:
                if it["valid_until"] and now >= pd.Timestamp(it["valid_until"]):
                    self._cancel_tag(tag, "süre doldu")
                continue
            legs = {o["leg"]: by_id.get(o["id"]) for o in rec["orders"]}
            # BE: a parçasının TP'si doldu → b'nin stop bacağını girişe çek
            a, b = legs.get("a"), legs.get("b")
            if a and b and not rec["be"]:
                a_tp = next((x for x in a.get("legs") or [] if x["type"] == "limit"), None)
                b_stop = next((x for x in b.get("legs") or [] if x["type"] in ("stop", "stop_limit")), None)
                if a_tp and a_tp["status"] == "filled" and b_stop and b_stop["status"] in ("new", "accepted", "held"):
                    be_px = float(b["filled_avg_price"])
                    try:
                        new = self.broker.replace_order(b_stop["id"], stop_price=be_px)
                        rec["be"] = True
                        rec["be_order_id"] = new.get("id")
                        log.info("BE %s: stop → %.2f", tag, be_px)
                    except BrokerError as e:
                        log.error("BE başarısız %s: %s", tag, e)
            done, pnl, qty = self._realized(rec, filled, by_id)
            if done and filled:
                risk = abs(float(filled[0]["filled_avg_price"]) - it["stop"]) * max(qty, 1)
                r = pnl / risk if risk else 0.0
                rec["exited"] = True
                self.shared["open"].discard(self.sym)
                self.engine.on_exit(_intent(it), r)
                self._journal({"day": str(self.day), "symbol": self.sym, "tag": tag, "setup": it["setup"], "side": it["side"],
                               "entry": it["entry"], "stop": it["stop"], "tp1": it["tp1"], "tp2": it["tp2"],
                               "pnl": round(pnl, 2), "r": round(r, 3), "confluence": it["confluence"],
                               "closed_at": str(now)})
                log.info("KAPANDI %s PnL %.2f R %.2f", tag, pnl, r)
        self._save()
        self._close_orphan()

    def _close_orphan(self):
        """Takip edilmeyen pozisyonu kapat. Örn. limit dolunca iptal edilen giriş o an kısmen dolmuşsa Alpaca
        bracket bacaklarını da iptal eder → pozisyon stopsuz kalır (2026-10-13 GLD -82). Canlı emri ya da açık
        işlemi olan sembole dokunulmaz (yeni dolum henüz işlenmemiş olabilir)."""
        if any(r.get("orders") and not r.get("exited") for r in self.st["tags"].values()):
            return
        pos = self.broker.position(self.sym)
        if pos and float(pos.get("qty") or 0):
            log.warning("SAHİPSİZ POZİSYON %s %s adet (stopsuz) → piyasadan kapatılıyor", self.sym, pos.get("qty"))
            self.broker.close_position(self.sym)

    def _order(self, oid: str | None, by_id: dict) -> dict | None:
        if not oid:
            return None
        o = by_id.get(oid)
        if o is None:
            try:
                o = self.broker.get_order(oid)
            except BrokerError:
                return None
        return o

    def _realized(self, rec: dict, filled: list[dict], by_id: dict) -> tuple[bool, float, int]:
        """Doldurulmuş parent'ların çıkışları: bracket bacakları + BE ile değiştirilen stop + seans sonu kapanış emri.
        Dönüş: (tamamen kapandı mı, gerçekleşen PnL $, toplam adet)."""
        sgn = 1 if rec["intent"]["side"] == "long" else -1
        done, pnl, qty = True, 0.0, 0
        extra = [self._order(rec.get("be_order_id"), by_id), self._order(rec.get("flat_order_id"), by_id)]
        extra = [x for x in extra if x and x.get("status") == "filled"]
        flat_left = sum(int(float(x["filled_qty"])) for x in extra if x["id"] == rec.get("flat_order_id"))
        for p in filled:
            ep, q = float(p["filled_avg_price"]), int(float(p["filled_qty"]))
            qty += q
            exits = [x for x in p.get("legs") or [] if x["status"] == "filled"]
            out_q = sum(int(float(x["filled_qty"])) for x in exits)
            pnl += sum(sgn * (float(x["filled_avg_price"]) - ep) * int(float(x["filled_qty"])) for x in exits)
            for x in extra:                                  # BE / flat emirleri bu parent'ın kalanını kapatır
                if out_q >= q:
                    break
                take = min(q - out_q, int(float(x["filled_qty"])) if x["id"] != rec.get("flat_order_id") else flat_left)
                if take <= 0:
                    continue
                pnl += sgn * (float(x["filled_avg_price"]) - ep) * take
                out_q += take
                if x["id"] == rec.get("flat_order_id"):
                    flat_left -= take
            done &= out_q >= q
        return done, pnl, qty

    def flatten(self, why: str):
        if self.st.get("flat_done"):
            return
        for tag in list(self.st["tags"]):
            if not self.st["tags"][tag].get("filled"):
                self._cancel_tag(tag, why)
        if not self.dry and self.broker.position(self.sym):
            o = self.broker.close_position(self.sym)        # önce semboldeki açık bacakları iptal eder
            for rec in self.st["tags"].values():
                if rec.get("filled") and not rec.get("exited"):
                    rec["flat_order_id"] = (o or {}).get("id")
            log.info("FLAT %s: %s pozisyonu kapatıldı", why, self.sym)
        self.st["flat_done"] = True
        self._save()

    # ------------------------------------------------------------------ tick
    def pull(self, now: pd.Timestamp) -> pd.DataFrame:
        """1. adım: yeni kapanmış barları çek ve günün barlarına ekle (motor henüz beslenmez)."""
        if self.day != now.date() or self.engine is None:
            self.start_day(now.date(), now)
        flat_t = dtime.fromisoformat(self.d["flat_time"])
        start = (self.last_ts + pd.Timedelta(minutes=1)) if self.last_ts is not None else             pd.Timestamp(datetime.combine(self.day, dtime(9, 30)))
        new = pd.DataFrame()
        if now.time() < flat_t and start < now:
            new = fetch_minutes(self.sym, start.to_pydatetime().replace(tzinfo=NY), now.to_pydatetime().replace(tzinfo=NY),
                                feed="iex")
            if not len(new):                                 # IEX bu dakikada bar vermedi (boş, RangeIndex)
                return pd.DataFrame()
            new = new[(new.index + pd.Timedelta(minutes=1) <= now) & (new.index.time >= dtime(9, 30))]
            if len(new):
                bb = pd.concat([self.bars, new]) if len(self.bars) else new
                self.bars = bb[~bb.index.duplicated(keep="last")]
        return new

    def push(self, now: pd.Timestamp, new: pd.DataFrame):
        """2. adım: yeni barlarla motoru besle, broker senkronu, seans sonu kapanışı."""
        for ts, row in new.iterrows():
            self._feed(ts, row, now)
        self.sync(now)
        if now.time() < dtime.fromisoformat(self.d["flat_time"]) and not self.dry:
            self._retry_waiting(now)
        if now.time() >= dtime.fromisoformat(self.d["flat_time"]):
            self.flatten("seans sonu")

    def tick(self, now: pd.Timestamp):
        self.push(now, self.pull(now))



def run_multi(cfg: dict, broker: AlpacaBroker, dry_run: bool = False) -> None:
    """Tüm semboller için sürekli döngü (her dakika +5 sn). Günlük işlem ve açık pozisyon limitleri ortaktır."""
    syms = cfg["dmc"].get("symbols") or [cfg["dmc"]["symbol"]]
    shared = {"trades": 0, "open": set(), "day": None}
    bots = [DmcLive(cfg, broker, dry_run, s, shared) for s in syms]
    shared["bots"] = {b.sym: b for b in bots}            # alım gücü sırası tüm sembollerde ortak
    log.info("DMC canlı başladı (%s, %s)", ", ".join(syms), "DRY-RUN" if dry_run else broker.base)
    while True:
        try:
            clk = broker.clock()
            now_dt = clk["timestamp"]
            if not clk["is_open"]:
                for b in bots:
                    if b.engine is not None and not b.st.get("flat_done"):
                        b.flatten("piyasa kapandı")
                    b.engine = None
                wait = (clk["next_open"] - now_dt).total_seconds() - 120
                log.info("piyasa kapalı; sonraki açılış %s", clk["next_open"])
                time.sleep(max(30, min(wait, 3600)))
                continue
            now = _naive(now_dt).floor("s")
            if shared["day"] != now.date():
                shared.update(trades=0, open=set(), day=now.date())
            if now.time() >= dtime(9, 30, 30):
                m = now.floor("min")
                news = {}
                for b in bots:                           # önce tüm sembollerin barları (SMT için eş verisi güncel olsun)
                    try:
                        news[b.sym] = b.pull(m)
                    except Exception:
                        log.exception("veri hatası %s", b.sym)
                by_sym = {b.sym: b for b in bots}
                for b in bots:
                    if b.sym not in news:
                        continue
                    try:
                        peer = by_sym.get(peer_symbol(b.sym) or "")
                        if peer is not None and b.engine is not None:
                            b.engine.ctx["peer"] = peer.bars          # [K] SMT: QQQ↔SPY
                        b.push(m, news[b.sym])
                    except Exception:                    # bir sembolün hatası diğerlerini durdurmasın
                        log.exception("tick hatası %s", b.sym)
                    if len(shared["open"]) >= cfg["dmc"].get("max_open_total", 1):
                        for x in bots:                   # limit doldu: bekleyen diğer girişler (her sembol, iki yön) iptal
                            try:
                                x.cancel_pending("açık pozisyon limiti")
                            except Exception:
                                log.exception("iptal hatası %s", x.sym)
        except Exception:
            log.exception("döngü hatası")
        n = datetime.now(NY)
        time.sleep(max(1.0, 65 - n.second - n.microsecond / 1e6))
