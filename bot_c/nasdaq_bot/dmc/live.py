"""DMC canlı yürütücü (Alpaca). Backtest ile aynı DayEngine'i 1 dk IEX barlarıyla besler.

Döngü (her dakika +5 sn):
  1. Yeni kapanmış barları çek → engine.on_bar → niyetler
  2. Niyet → emir: iki bracket (a = TP1 parçası, b = TP2 parçası), client_order_id = dmc-<gün>-<sembol>-<tag>-v<n>-<a|b>
  3. Broker senkronu: dolum → engine.on_fill; a'nın TP'si dolunca b'nin stop'u girişe (BE); kapanış → engine.on_exit
  4. flat_time'da tüm DMC emirleri iptal, açık pozisyonu kapat
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
from ..notify import send_async
from .data import fetch_minutes, load_history, peer_symbol, rth_daily
from .engine import DayEngine, SETUP_CLASSES
from .levels import am_range_median, am_ranges, prepare_day
from .news import blocked, fetch_events
from .setups import Intent, SidePair

log = logging.getLogger("dmc.live")


def _naive(ts: datetime) -> pd.Timestamp:
    return pd.Timestamp(ts.astimezone(NY).replace(tzinfo=None))


def _intent(d: dict) -> Intent:
    """Durum dosyasındaki niyet sözlüğü → Intent."""
    return Intent(**{k: (pd.Timestamp(v) if k in ("created", "valid_until") and v else v) for k, v in d.items()})


def size_position(equity: float, bp: float, entry: float, stop: float, risk_pct: float, bp_usage: float,
                  bp_full: float | None = None, slots: int = 1) -> int:
    """Risk tabanlı adet; alım gücü tavanıyla sınırlı."""
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
        self.trade = self.sym.upper() not in {s.upper() for s in self.d.get("watch_only") or []}
        self.shared = shared if shared is not None else {"trades": 0, "open": set(), "day": None}
        self.broker = broker
        self.dry = dry_run or broker is None
        Path(cfg["state_dir"]).mkdir(parents=True, exist_ok=True)
        self.state_file = Path(cfg["state_dir"]) / f"dmc_live_{self.sym.replace('/', '')}.json"
        self.journal_file = Path(cfg["state_dir"]) / "dmc_journal.json"
        self.day: date | None = None
        self.engine: DayEngine | None = None
        self.last_ts: pd.Timestamp | None = None
        self.bars = pd.DataFrame()
        self.hist_bars = pd.DataFrame()
        self.shadow_pairs: dict[str, SidePair] = {}
        self.shadow_signals: list[dict] = []
        self.shadow_evaluated = False
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
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.state_file)

    def _journal(self, rec: dict):
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)
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
        self.hist_bars = hist
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
        self.events = self.shared.get("ff_events") or fetch_events(self.cfg["state_dir"])
        self.shared["ff_events"] = self.events
        from .weekend_learner import apply_learned_priorities
        day_cfg = apply_learned_priorities(self.cfg, self.cfg["state_dir"], self.sym)
        self.engine = DayEngine(day_cfg, lv, {"am_range_median": am_range_median(am_ranges(hist), day),
                                               "vol_mult": (self.d.get("vol_mult") or {}).get(self.sym.upper(), 1),
                                               "news": self.events, "news_events": self.events},
                                symbol=self.sym)   # sembole özel watch_setups canlıda da uygulansın
        pm = today[(today.index.time >= dtime(8, 0)) & (today.index.time < dtime(9, 30))] if len(today) else pd.DataFrame()
        if len(pm):
            self.engine.set_premarket(pm)
        # 13 diğer taktik varyasyonunu gölge (shadow) modda başlat (emir göndermez, sadece sinyal ve başarılarını kaydeder)
        self.shadow_pairs = {}
        self.shadow_signals = []
        self.shadow_evaluated = False
        for s_name, s_cls in SETUP_CLASSES.items():
            if s_name in ("kaanermi", "sabirli_coklu"):
                continue
            scfg = {**(self.d.get("setups", {}).get(s_name) or {}), "_name": s_name, "enabled": True}
            try:
                sp = SidePair(s_cls, scfg, lv, self.engine.ctx)
                if len(pm):
                    sp.set_premarket(pm)
                self.shadow_pairs[s_name] = sp
            except Exception:
                pass
        hi_news = "Yok" if self.events is None else ", ".join(f"{e.t:%H:%M} {e.title}" for e in self.events if e.t.date() == day and e.impact == "High") or "Yok"
        log.info("GÜN %s bias=%s PDH=%s PDL=%s PMH=%s PML=%s FVG=%d RB=%d haber=%s", day, lv.bias, lv.pdh, lv.pdl, lv.pmh,
                 lv.pml, len(lv.fvgs), len(lv.rbs), hi_news)
        yon_tr = {"long": "Yükseliş (AL)", "short": "Düşüş (SAT)"}.get(str(lv.bias).lower(), "Nötr")
        send_async(
            f"🌅 {self.sym} Hazır ({day}) | Günlük Yön: {yon_tr}\n"
            f"Destek: {lv.pdl} – Direnç: {lv.pdh} | Haber: {hi_news}",
            dedup_key=f"start_day_{self.sym}_{day}",
            cooldown=3600,
        )
        self.bars = today[today.index.time >= dtime(9, 30)] if len(today) else pd.DataFrame()
        self.last_ts = None
        self._replay: dict[str, Intent] = {}
        for ts, row in self.bars.iterrows():
            self._feed(ts, row, now, replay=True)
        for s in self.st["filled_setups"]:
            if s not in self.engine.done:
                self.engine.done.add(s)
                self.engine.trades += 1
        self._cleanup_old_orders()
        for r_ in self.st["tags"].values():
            if r_.get("filled"):
                self.shared["trades"] += 1
                if not r_.get("exited"):
                    self.shared["open"].add(self.sym)
        for it in self._replay.values():
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
            for it in list(self._replay.values()):
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
        # Diğer 13 taktik varyasyonunu gölge (shadow) modda besle ve sinyallerini kaydet
        for s_name, sp in self.shadow_pairs.items():
            try:
                for sit in sp.on_bar(ts, row.Open, row.High, row.Low, row.Close, row.Volume):
                    if sit.kind != "cancel":
                        self.shadow_signals.append({
                            "ts": ts,
                            "setup": s_name,
                            "side": sit.side,
                            "kind": sit.kind,
                            "entry": round(float(sit.entry), 2),
                            "stop": round(float(sit.stop), 2),
                            "tp1": round(float(sit.tp1), 2) if sit.tp1 else None,
                            "tp2": round(float(sit.tp2), 2),
                            "confluence": list(sit.confluence or []),
                        })
                        if not replay:
                            yon = "AL (Long)" if sit.side == "long" else "SAT (Short)"
                            send_async(
                                f"👁️ Yan Taktik Sinyali: {self.sym} {yon} ({s_name})\n"
                                f"Fiyat: {sit.entry:.2f} | Hedef: {sit.tp2:.2f} | Stop: {sit.stop:.2f}",
                                dedup_key=f"shadow_{self.day}_{self.sym}_{s_name}_{sit.side}",
                                cooldown=3600,
                            )
            except Exception:
                pass
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
        pil = self._pillar_label(it.confluence or [])
        yon = "AL" if it.side == "long" else "SAT"
        f = self.d.get("filters", {})
        why = blocked(self.events, now, f.get("red_news_block_minutes", 5), f.get("speech_block_minutes", 30))
        if why and (why != "news_unavailable" or f.get("fail_closed_news", True)):
            log.info("HABER FİLTRESİ %s → %s atlandı", why, it.tag)
            send_async(
                f"🛑 {self.sym} {yon} ({it.entry:.2f}) — Haber var ({why}), risk alınmadı.",
                dedup_key=f"news_blk_{self.sym}_{it.tag}_{why}",
                cooldown=900,
            )
            return
        key = (round(it.entry, 2), round(it.stop, 2), it.tp1 and round(it.tp1, 2), round(it.tp2, 2), it.kind)
        if rec and rec.get("orders") and self._same_entry(rec.get("key", ()), key):
            return
        if not self.trade or it.setup in (self.d.get("watch_setups") or []):
            if not rec or not self._same_entry(rec.get("key", ()), key):
                log.info("İZLEME %s %s %s giriş %.2f stop %.2f tp2 %.2f (işlem açılmaz)", self.sym, it.tag, it.side,
                         it.entry, it.stop, it.tp2)
                self.st["tags"][it.tag] = {**self._new_rec(it, key, 0), "watch": True}
                self._save()
            return
        if not self._limits_ok():
            log.info("ORTAK LİMİT (%s işlem, açık %s) → %s %s atlandı", self.shared["trades"],
                     sorted(self.shared["open"]), self.sym, it.tag)
            send_async(
                f"⏳ {self.sym} {yon} ({it.entry:.2f}) — Açık işlem var ({', '.join(sorted(self.shared['open']))}), pas geçildi.",
                dedup_key=f"lim_blk_{self.sym}_{it.tag}",
                cooldown=900,
            )
            return
        if rec and rec.get("orders"):
            self._cancel_tag(it.tag, "yeniden çapalama")
        self._submit(it, key, (rec or {}).get("version", 0) + 1)

    @staticmethod
    def _same_entry(a, b) -> bool:
        a, b = tuple(a), tuple(b)
        return len(a) == len(b) == 5 and a[0] == b[0] and a[1] == b[1] and a[4] == b[4]

    @staticmethod
    def _new_rec(it: Intent, key, version: int) -> dict:
        return {"intent": {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in asdict(it).items()},
                "key": list(key), "version": version, "orders": [], "filled": False, "exited": False, "be": False}

    def _submit(self, it: Intent, key, version: int):
        prev = self.st["tags"].get(it.tag) or {}
        tag_rec = self._new_rec(it, key, version)
        pil = self._pillar_label(it.confluence or [])
        yon = "AL (Long)" if it.side == "long" else "SAT (Short)"
        if self.dry:
            log.info("[DRY] %s %s %s giriş %.2f stop %.2f tp1 %s tp2 %.2f conf=%s", it.kind, it.side, it.tag, it.entry,
                     it.stop, it.tp1, it.tp2, it.confluence)
            self.st["tags"][it.tag] = tag_rec
            self._save()
            return
        if it.kind == "limit" and len(self.bars):
            px = float(self.bars["Close"].iloc[-1])
            if (it.side == "long" and px <= it.entry) or (it.side == "short" and px >= it.entry):
                log.info("GEÇ %s: fiyat %.2f girişi %.2f geçmiş → gönderilmedi", it.tag, px, it.entry)
                send_async(
                    f"⏭️ {self.sym} {yon} ({it.entry:.2f}) — Fiyat ({px:.2f}) girişi geçtiği için girilmedi.",
                    dedup_key=f"passed_{self.sym}_{it.tag}_{it.entry:.2f}",
                    cooldown=900,
                )
                self.st["tags"][it.tag] = tag_rec
                self._save()
                return
        opp = self._opposite(it.side, it.tag)
        if opp:
            if not self._nearer(it.entry, opp[1]["intent"]["entry"]):
                tag_rec["wait"] = True
                self.st["tags"][it.tag] = tag_rec
                self._save()
                if not prev.get("wait"):
                    log.info("SIRADA %s (ters yönde %s bekliyor, fiyata o daha yakın)", it.tag, opp[0])
                    send_async(
                        f"⏳ {self.sym} {yon} ({it.entry:.2f}) — Sırada bekliyor (fiyata daha yakın emir var).",
                        dedup_key=f"wait_opp_{self.sym}_{it.tag}",
                        cooldown=900,
                    )
                return
            self._cancel_tag(opp[0], f"{it.tag} fiyata daha yakın")
            opp[1]["wait"] = True
        if not self._admit(it, tag_rec, bool(prev.get("wait"))):
            return
        acct = self.broker.account()
        equity = float(acct["equity"])
        bp = float(acct.get("buying_power") or acct.get("daytrading_buying_power") or 0)
        mult = float(self.d["setups"].get(it.setup, {}).get("risk_mult", 1.0))
        args = (it.entry, it.stop, self.d["risk_per_trade_pct"] * mult, self.d.get("bp_usage", 0.9),
                equity * float(acct.get("multiplier") or 1) * mult, self.d.get("bp_slots", 1))
        qty = size_position(equity, bp, *args)
        full = size_position(equity, equity * float(acct.get("multiplier") or 1), *args)
        if qty < 0.5 * full:
            tag_rec["wait"] = True
            self.st["tags"][it.tag] = tag_rec
            self._save()
            if not prev.get("wait"):
                log.info("SIRADA %s %s (alım gücü serbest kalınca: %d yerine %d adet)", self.sym, it.tag, qty, full)
                send_async(
                    f"⏳ {self.sym} {yon} ({it.entry:.2f}) — Bakiye başka emirde bağlı, sırada bekliyor.",
                    dedup_key=f"wait_bp_{self.sym}_{it.tag}",
                    cooldown=900,
                )
            return
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
                    tag_rec["wait"] = True
        if tag_rec["orders"]:
            now_ts = self.last_ts or _naive(datetime.now(NY))
            mtf = self._mtf_snapshot(now_ts, float(it.entry), it.side)
            mtf_s = self._short_mtf_label(mtf)
            send_async(
                f"📤 EMİR VERİLDİ: {self.sym} {yon} ({qty} adet)\n"
                f"Giriş: {it.entry:.2f} | Stop: {it.stop:.2f} | Hedef: {it.tp1} / {it.tp2:.2f}\n"
                f"Sebep: {pil} | Grafikler: {mtf_s}",
                dedup_key=f"order_{self.day}_{self.sym}_{it.tag}_v{version}",
                cooldown=30,
            )
        self.st["tags"][it.tag] = tag_rec
        self._save()

    def _traded_through(self, it: Intent) -> bool:
        b = self.bars[self.bars.index >= it.created] if it.created is not None else self.bars
        if not len(b):
            return False
        return bool((b["Low"] <= it.entry).any()) if it.side == "long" else bool((b["High"] >= it.entry).any())

    def _dist(self, intent: dict) -> float:
        if not len(self.bars):
            return float("inf")
        px = float(self.bars["Close"].iloc[-1])
        prio = float(self.d["setups"].get(intent.get("setup", ""), {}).get("priority", 1.0))
        return abs(intent["entry"] - px) / (abs(intent["entry"] - intent["stop"]) or 1e-9) / prio

    def _resting(self, skip_tag: str) -> list[tuple[float, "DmcLive", str]]:
        out = []
        for b in (self.shared.get("bots") or {self.sym: self}).values():
            for t, r in b.st.get("tags", {}).items():
                if r.get("orders") and not r.get("filled") and not (b is self and t == skip_tag):
                    out.append((b._dist(r["intent"]), b, t))
        return out

    def _admit(self, it: Intent, tag_rec: dict, was_waiting: bool) -> bool:
        rest = self._resting(it.tag)
        group = next((set(g) for g in self.d.get("corr_groups") or [] if self.sym in g), set())
        mates = [x for x in rest if x[1] is not self and x[1].sym in group]
        if mates:
            far = max(mates, key=lambda x: x[0])
            if not self._dist(tag_rec["intent"]) < 0.8 * far[0]:
                tag_rec["wait"] = True
                self.st["tags"][it.tag] = tag_rec
                self._save()
                if not was_waiting:
                    log.info("SIRADA %s %s (%s'de aynı yönde bekleyen emir var)", self.sym, it.tag, far[1].sym)
                return False
            far[1]._cancel_tag(far[2], f"{self.sym} {it.tag} fiyata daha yakın")
            far[1].st["tags"][far[2]]["wait"] = True
            far[1]._save()
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
        for t, r in self.st["tags"].items():
            if t != tag and not r.get("filled") and r.get("orders") and r["intent"]["side"] != side:
                return t, r
        return None

    def _nearer(self, entry: float, other: float) -> bool:
        if not len(self.bars):
            return False
        px = float(self.bars["Close"].iloc[-1])
        return abs(entry - px) < 0.8 * abs(other - px)

    def _limits_ok(self) -> bool:
        lim_t = self.d.get("max_trades_total", self.d["max_trades_per_day"])
        return self.shared["trades"] < lim_t and len(self.shared["open"] - {self.sym}) < self.d.get("max_open_total", 1)

    def _retry_waiting(self, now: pd.Timestamp):
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
        if not self.dry and rec.get("orders") and hasattr(self.broker, "get_order"):
            for o in rec["orders"]:
                try:
                    st = (self.broker.get_order(o["id"]) or {}).get("status")
                except BrokerError:
                    st = None
                if st in ("filled", "partially_filled"):
                    log.info("İPTAL ATLANDI %s (%s): emir zaten dolmuş (%s)", tag, why, st)
                    self.sync(_naive(datetime.now(NY)))
                    return
        had_orders = bool(rec.get("orders"))
        for o in rec.get("orders", []):
            if not self.dry:
                self.broker.cancel_order(o["id"])
        log.info("İPTAL %s (%s)", tag, why)
        if had_orders:
            it = rec.get("intent") or {}
            pil = self._pillar_label(it.get("confluence") or [])
            send_async(
                f"🗑️ EMİR İPTAL [{self.sym}] {pil} → {why}",
                dedup_key=f"cancel_{self.day}_{self.sym}_{tag}_{why}",
                cooldown=60,
            )
        rec["orders"] = []
        rec["wait"] = False
        self._save()

    def cancel_pending(self, why: str):
        for tag, rec in self.st.get("tags", {}).items():
            if not rec.get("filled") and (rec.get("orders") or rec.get("wait")):
                self._cancel_tag(tag, why)

    def _check_cancel_through(self, row):
        for tag, rec in self.st["tags"].items():
            it = rec["intent"]
            if rec.get("filled") or not (rec.get("orders") or rec.get("wait")) or it["kind"] != "limit" \
                    or it["cancel_if_through"] is None:
                continue
            cit = it["cancel_if_through"]
            if (it["side"] == "long" and row.High >= cit) or (it["side"] == "short" and row.Low <= cit):
                self._cancel_tag(tag, "Hedefe dokunmadan kaçtı")

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
                rec["filled_at"] = str(now)
                rec["fill_price"] = round(float(filled[0].get("filled_avg_price") or it["entry"]), 4)
                self.shared["trades"] += 1
                self.shared["open"].add(self.sym)
                setup = it["setup"]
                if setup not in self.st["filled_setups"]:
                    self.st["filled_setups"].append(setup)
                    self.engine.on_fill(_intent(it))
                log.info("DOLUM %s @ %s (Giriş kaydedildi)", tag, [p.get("filled_avg_price") for p in filled])
                self._record_entry_event(tag, rec, now)
                mtf_e = rec.get("mtf_entry") or {}
                vc = self._variation_cross_check(it.get("side", "long"), now)
                pil = self._pillar_label(it.get("confluence") or [])
                yon_tr = "ALIŞ (LONG)" if it.get("side") == "long" else "SATIŞ (SHORT)"
                send_async(
                    f"🎯 İŞLEME GİRİLDİ [{self.sym} {yon_tr}] — {pil}\n"
                    f"Fiyat: {rec['fill_price']:.2f} | Zarar Kes: {it.get('stop')} | Hedef 1: {it.get('tp1')} | Hedef 2: {it.get('tp2')}\n"
                    f"Zamanlar: {self._short_mtf_label(mtf_e)} | Diğer Taktikler → Onay:{len(vc['confirming'])} Ters:{len(vc['opposing'])}",
                    dedup_key=f"fill_{self.day}_{self.sym}_{tag}",
                    cooldown=10,
                )
            if not rec["filled"]:
                if it["valid_until"] and now >= pd.Timestamp(it["valid_until"]):
                    self._cancel_tag(tag, "süre doldu")
                continue
            legs = {o["leg"]: by_id.get(o["id"]) for o in rec["orders"]}
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
                        send_async(
                            f"🔒 HEDEF 1 ALINDI [{self.sym}] — Yarısı kârla kapandı, kalan yarının stopu girişe ({be_px:.2f}) çekildi (Zarar riski sıfır).",
                            dedup_key=f"be_{self.day}_{self.sym}_{tag}",
                            cooldown=10,
                        )
                    except BrokerError as e:
                        log.error("BE başarısız %s: %s", tag, e)
            done, pnl, qty = self._realized(rec, filled, by_id)
            if done and filled:
                fill_px = float(filled[0].get("filled_avg_price") or it["entry"])
                risk = abs(fill_px - it["stop"]) * max(qty, 1)
                r = pnl / risk if risk else 0.0
                rec["exited"] = True
                self.shared["open"].discard(self.sym)
                self.engine.on_exit(_intent(it), r)
                diag = self._diagnose_trade(tag, rec, fill_px, qty, pnl, r, now)
                self._journal(diag)
                self._append_forensics_markdown(diag)
                yon_tr = "ALIŞ" if diag["side"] == "long" else "SATIŞ"
                if r < 0:
                    log.info("KAPANDI [KAYIP] %s PnL %.2f$ (%+.2fR) | %s | %s",
                             tag, pnl, r, diag["pillar"], diag["why_lost"])
                    send_async(
                        f"❌ İŞLEM KAPANDI (ZARAR) [{self.sym} {yon_tr}] → {pnl:+,.2f} $ ({r:+.2f}R)\n"
                        f"Taktik: {diag['pillar']} | Süre: {diag['duration_min']:.0f} dk | Zamanlar: {diag.get('mtf_summary', '-')}\n"
                        f"Neden Kaybetti: {diag['why_lost']}",
                        dedup_key=f"exit_{self.day}_{self.sym}_{tag}",
                        cooldown=5,
                    )
                else:
                    log.info("KAPANDI [KAZANÇ/BE] %s PnL %.2f$ (%+.2fR) | %s | %s",
                             tag, pnl, r, diag["pillar"], diag.get("why_won", "-"))
                    send_async(
                        f"✅ İŞLEM KAPANDI (KÂR) [{self.sym} {yon_tr}] → {pnl:+,.2f} $ ({r:+.2f}R)\n"
                        f"Taktik: {diag['pillar']} | Süre: {diag['duration_min']:.0f} dk | Zamanlar: {diag.get('mtf_summary', '-')}\n"
                        f"Neden Kazandı: {diag.get('why_won', '-')}",
                        dedup_key=f"exit_{self.day}_{self.sym}_{tag}",
                        cooldown=5,
                    )
        self._save()
        self._close_orphan()

    def _pillar_label(self, conf: list[str]) -> str:
        cset = " ".join(conf or [])
        if "orderflow_vwap" in cset:
            return "Hacim + VWAP"
        if "crt_range" in cset:
            return "1S Likidite Avı"
        if "rb15m" in cset:
            return "15dk Açılış Dönüşü"
        if "rb3m" in cset:
            return "3dk Dönüş"
        if "ote" in cset or "cisd+displacement" in cset:
            return "Öğle Düzeltmesi"
        if "ff_macro" in cset:
            return "Canlı Haber"
        if "pa_srflip" in cset:
            return "Destek/Direnç + Elliott"
        return (conf[0] if conf else "Ana Taktik")

    def _short_mtf_label(self, mtf: dict) -> str:
        """6 zaman dilimini sade oklarla özetler: 1dk:↑ 5dk:↑ 15dk:↓ 1S:↑ 4S:↑ 1G:↑"""
        tfs = (mtf or {}).get("tfs") or {}
        if not tfs:
            return "-"
        tr_map = {"1m": "1dk", "5m": "5dk", "15m": "15dk", "1h": "1S", "4h": "4S", "1d": "1G"}
        parts = []
        for k, v in tfs.items():
            tr = v.get("trend", "")
            arrow = "↑" if "BULL" in tr else ("↓" if "BEAR" in tr else "→")
            parts.append(f"{tr_map.get(k, k)}:{arrow}")
        return " ".join(parts)

    def _mtf_snapshot(self, up_to_ts: pd.Timestamp, price: float, side: str) -> dict:
        """1dk, 5dk, 15dk, 1S, 4S, 1G zaman dilimlerinin ve kurumsal VWAP'ın o andaki röntgenini çıkarır."""
        snap: dict = {"tfs": {}, "vwap": None, "vwap_diff_pct": 0.0, "above_vwap": None,
                      "aligned_tfs": [], "opposing_tfs": [], "mtf_score": 0}
        try:
            parts = []
            if len(self.hist_bars):
                parts.append(self.hist_bars[self.hist_bars.index <= up_to_ts])
            if len(self.bars):
                parts.append(self.bars[self.bars.index <= up_to_ts])
            if not parts:
                return snap
            allb = pd.concat(parts)
            allb = allb[~allb.index.duplicated(keep="last")]
            if not len(allb):
                return snap
            rth = allb[(allb.index.time >= dtime(9, 30)) & (allb.index.time < dtime(16, 0))]
            if not len(rth):
                rth = allb

            # Gün içi VWAP
            if len(self.bars):
                tb = self.bars[self.bars.index <= up_to_ts]
                if len(tb) and "Volume" in tb.columns:
                    vol = tb["Volume"].replace(0, 1).astype(float)
                    typ = (tb["High"].astype(float) + tb["Low"].astype(float) + tb["Close"].astype(float)) / 3.0
                    v_sum = float(vol.sum())
                    if v_sum > 0:
                        vwap_px = float((typ * vol).sum() / v_sum)
                        snap["vwap"] = round(vwap_px, 2)
                        snap["vwap_diff_pct"] = round((price - vwap_px) / max(vwap_px, 1e-6) * 100.0, 3)
                        snap["above_vwap"] = bool(price >= vwap_px)

            tf_specs = [("1m", "1min"), ("5m", "5min"), ("15m", "15min"), ("1h", "60min"), ("4h", "240min"), ("1d", "1D")]
            for tf_name, rule in tf_specs:
                if tf_name == "1m":
                    df = rth.tail(60)
                else:
                    df = rth.resample(rule, origin="start_day", offset="9h30min").agg(
                        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
                    ).dropna(subset=["Close"]).tail(60)
                if len(df) < 5:
                    continue
                closes = df["Close"].astype(float)
                c_last = float(closes.iloc[-1])
                ema9 = float(closes.ewm(span=min(9, len(closes)), adjust=False).mean().iloc[-1])
                ema21 = float(closes.ewm(span=min(21, len(closes)), adjust=False).mean().iloc[-1])
                if c_last >= ema9 >= ema21:
                    tr = "BULL"
                elif c_last <= ema9 <= ema21:
                    tr = "BEAR"
                elif ema9 > ema21:
                    tr = "PULLBACK_IN_BULL"
                elif ema9 < ema21:
                    tr = "PULLBACK_IN_BEAR"
                else:
                    tr = "FLAT"

                # RSI(14)
                delta = closes.diff()
                up = delta.clip(lower=0.0).ewm(alpha=1 / 14, adjust=False).mean()
                down = (-delta.clip(upper=0.0)).ewm(alpha=1 / 14, adjust=False).mean()
                rs = float(up.iloc[-1]) / max(float(down.iloc[-1]), 1e-9)
                rsi = round(100.0 - (100.0 / (1.0 + rs)), 1)

                # Son 20 bar içindeki konum (%0 dip, %100 tepe)
                w = df.tail(20)
                hi20, lo20 = float(w["High"].max()), float(w["Low"].min())
                pos_pct = round((c_last - lo20) / max(hi20 - lo20, 1e-6) * 100.0, 1)

                # Son 12 barda aktif FVG desteği veya direnci
                fvg_state = "NONE"
                if len(df) >= 4:
                    w12 = df.tail(12)
                    wh = w12["High"].astype(float).values
                    wl = w12["Low"].astype(float).values
                    for i in range(2, len(w12)):
                        if wl[i] > wh[i - 2] and wh[i - 2] * 0.998 <= price <= wl[i] * 1.003:
                            fvg_state = "BULL_FVG_SUPPORT"
                        if wh[i] < wl[i - 2] and wh[i] * 0.997 <= price <= wl[i - 2] * 1.002:
                            fvg_state = "BEAR_FVG_WALL"

                snap["tfs"][tf_name] = {
                    "trend": tr,
                    "rsi": rsi,
                    "pos_pct": pos_pct,
                    "fvg": fvg_state,
                }
                is_bull = tr in ("BULL", "PULLBACK_IN_BULL")
                is_bear = tr in ("BEAR", "PULLBACK_IN_BEAR")
                if (side == "long" and is_bull) or (side == "short" and is_bear):
                    snap["aligned_tfs"].append(tf_name)
                    snap["mtf_score"] += 1
                elif (side == "long" and is_bear) or (side == "short" and is_bull):
                    snap["opposing_tfs"].append(tf_name)
                    snap["mtf_score"] -= 1
        except Exception:
            log.exception("MTF snapshot hesaplanamadı")
        return snap

    def _variation_cross_check(self, side: str, up_to_ts: pd.Timestamp) -> dict:
        """Diğer 13 taktik varyasyonunun aynı sembolde ürettiği sinyalleri kontrol eder (onaylayan vs ters düşen)."""
        confirming: list[str] = []
        opposing: list[str] = []
        seen_c: set[str] = set()
        seen_o: set[str] = set()
        for s in self.shadow_signals:
            sts = pd.Timestamp(s["ts"])
            if sts <= up_to_ts + pd.Timedelta(minutes=5):
                lbl = s["setup"]
                if s["side"] == side and s["setup"] not in seen_c:
                    seen_c.add(s["setup"])
                    confirming.append(lbl)
                elif s["side"] != side and s["setup"] not in seen_o:
                    seen_o.add(s["setup"])
                    opposing.append(lbl)
        return {"confirming": confirming, "opposing": opposing}

    def _record_entry_event(self, tag: str, rec: dict, now: pd.Timestamp) -> None:
        try:
            it = rec["intent"]
            conf = it.get("confluence") or []
            side = it.get("side", "long")
            fill_px = float(rec.get("fill_price") or it.get("entry") or 0.0)
            lv = getattr(self.engine, "lv", None)
            ff_sc = self.shared.get("ff_market_score") or {}
            mtf = self._mtf_snapshot(now, fill_px, side)
            vcross = self._variation_cross_check(side, now)
            rec["mtf_entry"] = mtf
            ev = {
                "event": "ENTRY",
                "day": str(self.day),
                "filled_at": str(now),
                "symbol": self.sym,
                "tag": tag,
                "setup": it.get("setup"),
                "pillar": self._pillar_label(conf),
                "side": side,
                "entry": fill_px,
                "stop": it.get("stop"),
                "tp1": it.get("tp1"),
                "tp2": it.get("tp2"),
                "daily_bias": getattr(lv, "bias", "?"),
                "ff_equity_score": ff_sc.get("equity_score"),
                "ff_metal_score": ff_sc.get("metal_score"),
                "mtf_entry": mtf,
                "confirming_variations": vcross["confirming"],
                "opposing_variations": vcross["opposing"],
                "confluence": conf,
            }
            ff = Path(self.cfg["state_dir"]) / "trade_forensics.jsonl"
            with ff.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.exception("Giriş kaydı yazılamadı")

    def _diagnose_trade(self, tag: str, rec: dict, fill_px: float, qty: int, pnl: float, r: float, now: pd.Timestamp) -> dict:
        """İşlem kapandığında neden kazandığını veya neden kaybettiğini sade Türkçe ile açıklar."""
        it = rec["intent"]
        side = it.get("side", "long")
        stop_px = float(it.get("stop") or fill_px)
        tp1_px = float(it.get("tp1") or it.get("tp2") or fill_px)
        tp2_px = float(it.get("tp2") or fill_px)
        rps = max(abs(fill_px - stop_px), 1e-6)
        stop_pct = rps / max(abs(fill_px), 1e-6) * 100.0
        conf = it.get("confluence") or []
        pillar = self._pillar_label(conf)
        lv = getattr(self.engine, "lv", None)
        daily_bias = getattr(lv, "bias", "neutral")

        filled_ts = pd.Timestamp(rec.get("filled_at") or it.get("created") or now)
        duration_min = max(0.0, (now - filled_ts).total_seconds() / 60.0)

        mtf_entry = rec.get("mtf_entry") or self._mtf_snapshot(filled_ts, fill_px, side)
        exit_px = float(self.bars["Close"].iloc[-1]) if len(self.bars) else fill_px
        mtf_exit = self._mtf_snapshot(now, exit_px, side)
        vcross = self._variation_cross_check(side, now)

        mfe_r, mae_r = 0.0, 0.0
        wick_stop_hunt = False
        intraday_trend_crush = False
        if len(self.bars):
            tb = self.bars[(self.bars.index >= filled_ts.floor("min")) & (self.bars.index <= now)]
            if len(tb):
                if side == "long":
                    mfe_r = max(0.0, float((tb["High"].max() - fill_px) / rps))
                    mae_r = max(0.0, float((fill_px - tb["Low"].min()) / rps))
                    last_c = float(tb["Close"].iloc[-1])
                    if float(tb["Low"].min()) <= stop_px and last_c > stop_px + 0.15 * rps:
                        wick_stop_hunt = True
                else:
                    mfe_r = max(0.0, float((fill_px - tb["Low"].min()) / rps))
                    mae_r = max(0.0, float((tb["High"].max() - fill_px) / rps))
                    last_c = float(tb["Close"].iloc[-1])
                    if float(tb["High"].max()) >= stop_px and last_c < stop_px - 0.15 * rps:
                        wick_stop_hunt = True
            day_o = float(self.bars["Open"].iloc[0])
            day_c = float(self.bars["Close"].iloc[-1])
            day_rng = float(self.bars["High"].max() - self.bars["Low"].min())
            if day_rng > 0 and abs(day_c - day_o) / day_rng >= 0.65:
                if (side == "long" and day_c < day_o) or (side == "short" and day_c > day_o):
                    intraday_trend_crush = True

        if r >= 1.4:
            exit_type = "Hedef 2 (Tam Kâr)"
        elif r > 0.05:
            exit_type = "Hedef 1 (Kısmi Kâr)"
        elif rec.get("be") or -0.15 <= r <= 0.05:
            exit_type = "Başabaş (Sıfır Zarar)"
        elif rec.get("flat_order_id"):
            exit_type = "Seans Sonu Kapanış"
        else:
            exit_type = "Stop (Zarar Kes)"

        tfs_in = mtf_entry.get("tfs") or {}
        aligned_tfs = mtf_entry.get("aligned_tfs") or []
        opposing_tfs = mtf_entry.get("opposing_tfs") or []

        # --- SADE KAZANMA NEDENİ ---
        win_reasons: list[str] = []
        if r > 0.05:
            htf_aligned = [t for t in aligned_tfs if t in ("5m", "15m", "1h", "4h", "1d")]
            if len(htf_aligned) >= 3:
                win_reasons.append(f"Büyük zaman dilimleri ({'+'.join(htf_aligned)}) işlemle aynı yöndeydi")
            if (side == "long" and mtf_entry.get("above_vwap") is True) or (side == "short" and mtf_entry.get("above_vwap") is False):
                win_reasons.append("Fiyat günlük ortalamanın (VWAP) doğru tarafındaydı")
            if vcross["confirming"]:
                win_reasons.append(f"{len(vcross['confirming'])} farklı taktik de aynı yönü onayladı")
            if mae_r <= 0.35:
                win_reasons.append("Giriş sonrası neredeyse hiç terste kalmadan hedefe yürüdü")
            if not win_reasons:
                win_reasons.append(f"{pillar} sinyali doğrudan hedefe ulaştı")

        # --- SADE KAYBETME NEDENİ ---
        reasons: list[str] = []
        if r < -0.05:
            htf_opp = [t for t in opposing_tfs if t in ("5m", "15m", "1h", "4h", "1d")]
            if len(htf_opp) >= 2:
                reasons.append(f"Büyük zaman dilimleri ({'+'.join(htf_opp)}) ters yöndeydi")
            if (side == "long" and mtf_entry.get("above_vwap") is False) or (side == "short" and mtf_entry.get("above_vwap") is True):
                reasons.append("Fiyat günlük ortalamanın (VWAP) ters tarafında kaldı")
            for tf_k in ("5m", "15m", "1h"):
                rsi_v = tfs_in.get(tf_k, {}).get("rsi")
                if rsi_v is not None:
                    if side == "long" and rsi_v >= 70.0:
                        reasons.append(f"{tf_k} grafikte fiyat çok şişmişti (RSI {rsi_v:.0f})")
                        break
                    if side == "short" and rsi_v <= 30.0:
                        reasons.append(f"{tf_k} grafikte fiyat çok düşmüştü (RSI {rsi_v:.0f})")
                        break
            if vcross["opposing"]:
                reasons.append(f"{len(vcross['opposing'])} farklı taktik ters yönde sinyal verdi")
            if mfe_r >= 0.55:
                reasons.append(f"Önce +{mfe_r:.1f}R kâra geçti ama ilk hedefe değmeden geri döndü")
            if wick_stop_hunt:
                reasons.append("Fiyat iğne atıp stopu patlattıktan sonra tekrar işlem yönüne gitti")
            if intraday_trend_crush:
                reasons.append("Gün içi sert tek yönlü trend dönüşü ezdi")
            if not reasons:
                reasons.append("Destek/direnç bölgesi tutmadı ve stop oldu")

        why_won = ", ".join(win_reasons) if win_reasons else "-"
        why_lost = ", ".join(reasons) if reasons else ("-" if r >= -0.05 else "Stop oldu")
        mtf_summary = self._short_mtf_label(mtf_entry)
        ff_sc = self.shared.get("ff_market_score") or {}
        rec_out = {
            "day": str(self.day),
            "symbol": self.sym,
            "tag": tag,
            "setup": it["setup"],
            "pillar": pillar,
            "side": side,
            "entry": round(fill_px, 2),
            "stop": it["stop"],
            "tp1": it["tp1"],
            "tp2": tp2_px,
            "stop_pct": round(stop_pct, 4),
            "qty": qty,
            "pnl": round(pnl, 2),
            "r": round(r, 3),
            "mfe_r": round(mfe_r, 2),
            "mae_r": round(mae_r, 2),
            "duration_min": round(duration_min, 1),
            "exit_type": exit_type,
            "daily_bias": daily_bias,
            "ff_equity_score": ff_sc.get("equity_score"),
            "ff_metal_score": ff_sc.get("metal_score"),
            "mtf_summary": mtf_summary,
            "mtf_entry": mtf_entry,
            "mtf_exit": mtf_exit,
            "confirming_variations": vcross["confirming"],
            "opposing_variations": vcross["opposing"],
            "why_won": why_won,
            "why_lost": why_lost,
            "win_reasons": win_reasons,
            "loss_reasons": reasons,
            "confluence": conf,
            "filled_at": str(filled_ts),
            "closed_at": str(now),
        }
        try:
            ff = Path(self.cfg["state_dir"]) / "trade_forensics.jsonl"
            with ff.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "EXIT", **rec_out}, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.exception("Çıkış kaydı yazılamadı")
        return rec_out

    def _append_forensics_markdown(self, diag: dict) -> None:
        """Her kapanan işlemi sade bir tablo olarak Markdown günlüğüne yazar."""
        try:
            bot = self.cfg.get("bot_name", "c")
            out_dir = Path(self.cfg.get("reports_dir", "reports"))
            out_dir.mkdir(parents=True, exist_ok=True)
            md_file = out_dir / f"islem_kayitlari_ve_kayip_analizi_{bot}.md"
            if not md_file.exists():
                header = (
                    f"# 📋 Bot {bot.upper()} — İşlem ve Neden Kazandı/Kaybetti Günlüğü\n\n"
                    "| Tarih | Saat | Sembol | Yön | Taktik | Giriş / Stop | Sonuç | Zaman Dilimleri | Neden Kazandı / Kaybetti |\n"
                    "| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :--- | :--- |\n"
                )
                md_file.write_text(header, encoding="utf-8")
            t_str = str(diag.get("filled_at", ""))[11:16] + "→" + str(diag.get("closed_at", ""))[11:16]
            why_str = diag["why_lost"] if diag.get("r", 0) < -0.05 else (diag.get("why_won") or diag.get("exit_type", "-"))
            row = (
                f"| `{diag['day']}` | `{t_str}` | **{diag['symbol']}** | `{diag['side'].upper()}` | "
                f"{diag['pillar']} | `{diag['entry']}` / `{diag['stop']}` | "
                f"**`{diag['pnl']:+,.2f}$` (`{diag['r']:+.2f}R`)** | "
                f"`{diag.get('mtf_summary', '-')}` | {why_str} |\n"
            )
            with md_file.open("a", encoding="utf-8") as f:
                f.write(row)
        except Exception:
            log.exception("Markdown kayıp analizi yazılamadı")

    def _evaluate_shadow_variations(self) -> None:
        """Seans sonunda diğer 13 taktik varyasyonunun gün içinde nasıl sonuçlandığını kaydeder."""
        if self.shadow_evaluated or not self.shadow_signals or not len(self.bars):
            return
        self.shadow_evaluated = True
        try:
            sh_file = Path(self.cfg["state_dir"]) / "shadow_variations.jsonl"
            seen: set[tuple[str, str]] = set()
            wins, losses, total_r = 0, 0, 0.0
            for sig in self.shadow_signals:
                key = (sig["setup"], sig["side"])
                if key in seen:
                    continue
                seen.add(key)
                sts = pd.Timestamp(sig["ts"])
                after = self.bars[self.bars.index > sts]
                if not len(after):
                    continue
                side = sig["side"]
                ep = float(sig["entry"])
                sp = float(sig["stop"])
                tp1 = float(sig["tp1"] or sig["tp2"])
                tp2 = float(sig["tp2"])
                rps = max(abs(ep - sp), 1e-6)
                if sig["kind"] == "limit":
                    touched = after[after["Low"] <= ep] if side == "long" else after[after["High"] >= ep]
                    if not len(touched):
                        continue
                    fill_ts = touched.index[0]
                    after = after[after.index >= fill_ts]
                else:
                    fill_ts = after.index[0]
                if not len(after):
                    continue
                hit_tp1 = False
                res_r = 0.0
                out_reason = "FLAT"
                mfe_r = 0.0
                for _, b in after.iterrows():
                    bh, bl = float(b["High"]), float(b["Low"])
                    if side == "long":
                        mfe_r = max(mfe_r, (bh - ep) / rps)
                        if not hit_tp1 and bl <= sp:
                            res_r, out_reason = -1.0, "STOP"
                            break
                        if hit_tp1 and bl <= ep:
                            res_r, out_reason = 0.5 * ((tp1 - ep) / rps), "BE_AFTER_TP1"
                            break
                        if bh >= tp2:
                            res_r = 0.5 * ((tp1 - ep) / rps) + 0.5 * ((tp2 - ep) / rps)
                            out_reason = "TP2_WIN"
                            break
                        if not hit_tp1 and bh >= tp1:
                            hit_tp1 = True
                    else:
                        mfe_r = max(mfe_r, (ep - bl) / rps)
                        if not hit_tp1 and bh >= sp:
                            res_r, out_reason = -1.0, "STOP"
                            break
                        if hit_tp1 and bh >= ep:
                            res_r, out_reason = 0.5 * ((ep - tp1) / rps), "BE_AFTER_TP1"
                            break
                        if bl <= tp2:
                            res_r = 0.5 * ((ep - tp1) / rps) + 0.5 * ((ep - tp2) / rps)
                            out_reason = "TP2_WIN"
                            break
                        if not hit_tp1 and bl <= tp1:
                            hit_tp1 = True
                if out_reason == "FLAT":
                    last_c = float(after["Close"].iloc[-1])
                    raw_r = ((last_c - ep) if side == "long" else (ep - last_c)) / rps
                    res_r = 0.5 * ((abs(tp1 - ep) / rps) + raw_r) if hit_tp1 else raw_r
                mtf = self._mtf_snapshot(fill_ts, ep, side)
                rec = {
                    "day": str(self.day),
                    "symbol": self.sym,
                    "setup": sig["setup"],
                    "side": side,
                    "signal_time": str(sts),
                    "fill_time": str(fill_ts),
                    "entry": ep,
                    "stop": sp,
                    "tp2": tp2,
                    "r": round(res_r, 2),
                    "mfe_r": round(mfe_r, 2),
                    "outcome": out_reason,
                    "mtf_score": mtf.get("mtf_score", 0),
                    "aligned_tfs": mtf.get("aligned_tfs", []),
                    "opposing_tfs": mtf.get("opposing_tfs", []),
                    "above_vwap": mtf.get("above_vwap"),
                }
                if res_r > 0:
                    wins += 1
                else:
                    losses += 1
                total_r += res_r
                with sh_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            if wins + losses > 0:
                send_async(
                    f"📋 [{self.sym}] Diğer Taktiklerin Gün Sonu Özeti: {wins + losses} sinyal ({wins} kazanç, {losses} kayıp, toplam {total_r:+.1f}R)",
                    dedup_key=f"shadow_eval_{self.day}_{self.sym}",
                    cooldown=3600,
                )
        except Exception:
            log.exception("Gölge varyasyon analizi kaydedilemedi")

    def _close_orphan(self):
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
            for x in extra:
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
        self._evaluate_shadow_variations()
        if self.st.get("flat_done"):
            return
        for tag in list(self.st["tags"]):
            if not self.st["tags"][tag].get("filled"):
                self._cancel_tag(tag, why)
        if not self.dry and self.broker.position(self.sym):
            o = self.broker.close_position(self.sym)
            for rec in self.st["tags"].values():
                if rec.get("filled") and not rec.get("exited"):
                    rec["flat_order_id"] = (o or {}).get("id")
            log.info("FLAT %s: %s pozisyonu kapatıldı", why, self.sym)
        self.st["flat_done"] = True
        self._save()

    # ------------------------------------------------------------------ tick
    def pull(self, now: pd.Timestamp) -> pd.DataFrame:
        if self.day != now.date() or self.engine is None:
            self.start_day(now.date(), now)
        flat_t = dtime.fromisoformat(self.d["flat_time"])
        start = (self.last_ts + pd.Timedelta(minutes=1)) if self.last_ts is not None else \
            pd.Timestamp(datetime.combine(self.day, dtime(9, 30)))
        new = pd.DataFrame()
        if now.time() < flat_t and start < now:
            new = fetch_minutes(self.sym, start.to_pydatetime().replace(tzinfo=NY), now.to_pydatetime().replace(tzinfo=NY),
                                feed="iex")
            if not len(new):
                return pd.DataFrame()
            new = new[(new.index + pd.Timedelta(minutes=1) <= now) & (new.index.time >= dtime(9, 30))]
            if len(new):
                bb = pd.concat([self.bars, new]) if len(self.bars) else new
                self.bars = bb[~bb.index.duplicated(keep="last")]
        return new

    def push(self, now: pd.Timestamp, new: pd.DataFrame):
        if len(new):
            self.sync(now)
        for ts, row in new.iterrows():
            self._feed(ts, row, now)
        self.sync(now)
        if now.time() < dtime.fromisoformat(self.d["flat_time"]) and not self.dry:
            self._retry_waiting(now)
        if now.time() >= dtime.fromisoformat(self.d["flat_time"]):
            self.flatten("seans sonu")

    def tick(self, now: pd.Timestamp):
        self.push(now, self.pull(now))


def daily_report(cfg: dict, broker: AlpacaBroker | None, day: date) -> str:
    from ..notify import send
    bot = cfg.get("bot_name", "a")
    jf = Path(cfg["state_dir"]) / "dmc_journal.json"
    rows = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else []
    today = [r for r in rows if r.get("day") == str(day)]
    lines = [f"📊 Bot {bot.upper()} — {day}"]
    if broker is not None:
        try:
            a = broker.account()
            eq, last = float(a["equity"]), float(a["last_equity"])
            lines.append(f"Bakiye: {eq:,.2f} $ | Günlük: {eq - last:+,.2f} $ ({(eq - last) / last * 100:+.2f}%)")
        except Exception as e:
            lines.append(f"(hesap okunamadı: {e})")
    lines.append(f"Bugün {len(today)} işlem" + (":" if today else " (işlem açılmadı)"))
    for r in today:
        why_s = f" | Neden: {r['why_lost']}" if r.get("r", 0) < -0.05 and r.get("why_lost") else (
            f" | Neden: {r['why_won']}" if r.get("r", 0) > 0.05 and r.get("why_won") else ""
        )
        lines.append(f"  {r['symbol']} {r['setup']} {r['side']}: {r['pnl']:+,.2f} $ ({r['r']:+.2f}R){why_s}")
    if rows:
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(r["setup"], []).append(r)
        lines.append(f"Başlangıçtan beri {len(rows)} işlem, {sum(r['pnl'] for r in rows):+,.2f} $, "
                     f"{sum(r['r'] for r in rows):+.1f}R")
        for k, v in sorted(by.items(), key=lambda kv: -sum(r["r"] for r in kv[1])):
            w = sum(1 for r in v if r["r"] > 0)
            lines.append(f"  {k}: {len(v)} işlem, {w} kazanç, {sum(r['r'] for r in v):+.1f}R, "
                         f"{sum(r['pnl'] for r in v):+,.2f} $")
    text = "\n".join(lines)
    out = Path(cfg.get("reports_dir", "reports"))
    out.mkdir(parents=True, exist_ok=True)
    (out / f"gunluk_{bot}_{day}.txt").write_text(text, encoding="utf-8")
    send(text)
    log.info("GÜNLÜK RAPOR\n%s", text)
    return text


def _keep_windows_awake() -> None:
    """Evde değilken bilgisayarın uyku moduna geçmesini ve konsol tıklama donmasını (QuickEdit) engeller."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        h = ctypes.windll.kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


def _refresh_forexfactory_shared(cfg: dict, shared: dict, bots: list[DmcLive], now: pd.Timestamp) -> None:
    """ForexFactory takvimini canlı izler ve yeni veri açıklandığında sade bildirim atar."""
    from ..notify import send
    from .news import macro_signal_for_symbol, save_market_score

    mono = time.monotonic()
    cur_evs = shared.get("ff_events") or []
    near_release = any(
        e.t.date() == now.date()
        and e.impact in ("High", "Medium")
        and not e.has_actual
        and -120 <= (now - e.t).total_seconds() <= 1500
        for e in cur_evs
    )
    shared["ff_near_release"] = near_release
    interval = 60.0 if near_release else 300.0
    if shared.get("ff_last_fetch") and (mono - shared["ff_last_fetch"]) < interval:
        return

    shared["ff_last_fetch"] = mono
    evs = fetch_events(cfg["state_dir"], today_only=bool(cur_evs))
    if not evs and cur_evs:
        evs = cur_evs
    if not evs:
        return
    shared["ff_events"] = evs
    shared["ff_near_release"] = any(
        e.t.date() == now.date()
        and e.impact in ("High", "Medium")
        and not e.has_actual
        and -120 <= (now - e.t).total_seconds() <= 1500
        for e in evs
    )

    try:
        score_doc = save_market_score(cfg["state_dir"], evs, now)
        shared["ff_market_score"] = score_doc
        sig_tuple = (score_doc["equity_score"], score_doc["metal_score"], score_doc["usd_score"], len(score_doc["breakdown"]))
        if shared.get("ff_last_score_sig") != sig_tuple:
            shared["ff_last_score_sig"] = sig_tuple
            upc = score_doc.get("upcoming") or []
            upc_lines = [
                f"• {u['time'][5:]} {u['title']} → Beklenti: {u['forecast'] or '-'} | Önceki: {u['previous'] or '-'}"
                for u in upc[:5]
            ]
            ff_line = (
                f"📊 FOREXFACTORY TAKİBİ — Borsa Puanı: {score_doc['equity_score']}/100 | Altın Puanı: {score_doc['metal_score']}/100"
                + ("\n📅 Beklenen Veriler:\n" + "\n".join(upc_lines) if upc_lines else "")
            )
            log.info(ff_line.replace("\n", " | "))
            send_async(ff_line, dedup_key=f"ff_score_{sig_tuple}", cooldown=600)
    except Exception:
        log.exception("ForexFactory piyasa puanı hesaplama hatası")

    seen: set = shared.setdefault("ff_seen_actuals", set())
    pre_warned: set = shared.setdefault("ff_pre_warned", set())

    for e in evs:
        if e.t.date() == now.date() and e.impact in ("High", "Medium"):
            # Veri açıklanmadan 5 dk önce hatırlatma (Forecast & Previous)
            secs_to = (e.t - now).total_seconds()
            pre_key = (str(e.t), e.title)
            if not e.has_actual and 0 <= secs_to <= 360 and pre_key not in pre_warned:
                pre_warned.add(pre_key)
                send_async(
                    f"⏰ 5 DK SONRA VERİ AÇIKLANACAK: {e.title}\n"
                    f"Beklenti (Forecast): {e.forecast or '-'} | Önceki (Previous): {e.effective_previous or '-'}",
                    dedup_key=f"ff_pre_{pre_key}",
                    cooldown=600,
                )
            # Veri açıklandığı an (Actual vs Forecast vs Previous)
            if e.has_actual:
                key = (str(e.t), e.title, e.actual)
                if key not in seen:
                    seen.add(key)
                    eq_sig = macro_signal_for_symbol(e, "QQQ").upper()
                    mt_sig = macro_signal_for_symbol(e, "GLD").upper()
                    msg = (
                        f"🌐 YENİ VERİ AÇIKLANDI: {e.title}\n"
                        f"Açıklanan (Actual): {e.actual} | Beklenti (Forecast): {e.forecast or '-'} | Önceki (Previous): {e.effective_previous or '-'}\n"
                        f"Karşılaştırma: {e.comparison_note()} → Borsa: {eq_sig} | Altın: {mt_sig}"
                    )
                    log.info(msg.replace("\n", " | "))
                    if abs((now - e.t).total_seconds()) <= 3600:
                        try:
                            send(msg)
                        except Exception:
                            pass

    for b in bots:
        b.events = evs
        if b.engine is not None:
            b.engine.news = evs
            b.engine.ctx["news"] = evs
            b.engine.ctx["news_events"] = evs


def _run_weekend_learning_if_due(cfg: dict, shared: dict, now: pd.Timestamp) -> None:
    """Piyasa kapalıyken son kapanan gün ve haftayı analiz edip sade bir özet gönderir."""
    from .weekend_learner import run_weekend_learning

    mono = time.monotonic()
    last_ts = shared.get("last_learn_mono")
    if last_ts is not None and (mono - last_ts) < 14400:
        return
    shared["last_learn_mono"] = mono
    try:
        doc = run_weekend_learning(cfg, now)
        shared["weekend_learning"] = doc
        ld = doc.get("last_session_day", "")
        log.info(
            "🧠 Hafta Sonu Analizi (%s) → Son Gün: %s işlem (%s kazanç, %+.2fR) | Son Hafta: %s işlem (%+.2fR)",
            ld, doc.get("last_day_n", 0), doc.get("last_day_wins", 0), doc.get("last_day_r", 0.0),
            doc.get("week_n", 0), doc.get("week_r", 0.0),
        )
        send_async(
            f"🧠 HAFTA SONU ANALİZİ ({ld})\n"
            f"Son Gün: {doc.get('last_day_n', 0)} işlem ({doc.get('last_day_wins', 0)} kazanç, {doc.get('last_day_r', 0.0):+.1f}R) | "
            f"Son Hafta: {doc.get('week_n', 0)} işlem ({doc.get('week_r', 0.0):+.1f}R)",
            dedup_key=f"wk_learn_{ld}",
            cooldown=14000,
        )
    except Exception:
        log.exception("Hafta sonu öğrenme döngüsü hatası")


def run_multi(cfg: dict, broker: AlpacaBroker, dry_run: bool = False) -> None:
    _keep_windows_awake()
    syms = cfg["dmc"].get("symbols") or [cfg["dmc"]["symbol"]]
    shared = {"trades": 0, "open": set(), "day": None}
    bots = [DmcLive(cfg, broker, dry_run, s, shared) for s in syms]
    shared["bots"] = {b.sym: b for b in bots}
    log.info("DMC canlı başladı (%s, %s) — ForexFactory canlı takip ve uyku koruması aktif",
             ", ".join(syms), "DRY-RUN" if dry_run else broker.base)
    send_async(
        f"🟢 BOT C AKTİF — {', '.join(syms)} izleniyor.\n"
        f"Her karar, giriş, çıkış ve kazanma/kaybetme nedeni sade olarak bu gruba yazılacak.",
        dedup_key="bot_c_boot",
        cooldown=60,
    )
    while True:
        try:
            _keep_windows_awake()
            clk = broker.clock()
            now_dt = clk["timestamp"]
            now = _naive(now_dt).floor("s")
            if shared["day"] != now.date():
                shared.update(trades=0, open=set(), day=now.date(), ff_last_fetch=0.0)
            _refresh_forexfactory_shared(cfg, shared, bots, now)
            if not clk["is_open"]:
                session_day = next((b.day for b in bots if b.engine is not None), None)
                for b in bots:
                    if b.engine is not None and not b.st.get("flat_done"):
                        b.flatten("piyasa kapandı")
                    b.engine = None
                if session_day is not None and shared.get("reported") != session_day:
                    shared["reported"] = session_day
                    shared["last_learn_mono"] = None
                    try:
                        daily_report(cfg, None if dry_run else broker, session_day)
                    except Exception:
                        log.exception("günlük rapor hatası")
                _run_weekend_learning_if_due(cfg, shared, now)
                wait = (clk["next_open"] - now_dt).total_seconds() - 120
                max_sleep = 60 if shared.get("ff_near_release") else 300
                log.info("piyasa kapalı; sonraki açılış %s (otomatik bekleniyor)", clk["next_open"])
                time.sleep(max(5, min(wait, max_sleep)))
                continue
            if now.time() >= dtime(9, 30, 30):
                m = now.floor("min")
                news = {}
                for b in bots:
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
                            b.engine.ctx["peer"] = peer.bars
                        b.push(m, news[b.sym])
                    except Exception:
                        log.exception("tick hatası %s", b.sym)
                    if len(shared["open"]) >= cfg["dmc"].get("max_open_total", 1):
                        for x in bots:
                            try:
                                x.cancel_pending("açık pozisyon limiti")
                            except Exception:
                                log.exception("iptal hatası %s", x.sym)
        except Exception:
            log.exception("döngü hatası")
        n = datetime.now(NY)
        time.sleep(max(1.0, 65 - n.second - n.microsecond / 1e6))
