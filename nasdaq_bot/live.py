"""Sürekli çalışan gün içi long/short işlem motoru.

Akış (her gün otomatik):
  seans öncesi  -> evren + günlük skorlar + piyasa rejimi hazırlanır
  09:30-16:00 ET -> her 5 dk: veri çek, skorla, pozisyon yönet, yeni long/short aç
  kapanış -10dk -> tüm pozisyonlar kapatılır (gece taşınmaz)
  günlük +%1    -> kâr kilitlenir, gün biter     |   günlük -%2 -> zarar kesilir, gün biter
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from rich.table import Table

from .broker import get_clock
from .candles import patterns_on
from .data import NY, download_ohlcv
from .fundamentals import analyst_view
from .intraday import (build_intraday, daily_scores, drop_incomplete, intraday_plan, pick_side,
                       qqq_vwap_flag, session_only)
from .report import console
from .risk import RiskManager
from .indicators import add_indicators
from .rb_scanner import load_alpha_profile
from .rejection_blocks import find_rejection_blocks
from .sentiment import news_sentiment
from .strategy import get_universe, load_data, regime_series, regime_short_series
from .weights import load_weights

log = logging.getLogger("live")
_TR = ZoneInfo("Europe/Istanbul")


class LiveTrader:
    def __init__(self, cfg: dict, broker, analysis_only: bool = False):
        self.cfg, self.li, self.broker = cfg, cfg["live"], broker
        self.analysis_only = analysis_only
        self.rm = RiskManager(cfg)
        self.day: str | None = None
        self.day_done = False
        self.ds: dict[str, pd.DataFrame] = {}
        self.universe: list[str] = []
        self.mult_long = self.mult_short = 1.0
        self.enrich_cache: dict[str, tuple[datetime, dict, dict]] = {}
        self.cooldown: dict[str, datetime] = {}
        self.trades_today = 0
        self.known: dict[str, str] = {}
        self.journal = Path(cfg["reports_dir"]) / "journal.csv"
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        logdir = Path(cfg["reports_dir"]).parent / "logs"
        logdir.mkdir(exist_ok=True)
        fh = logging.FileHandler(logdir / f"live_{datetime.now():%Y%m%d}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        log.setLevel(logging.INFO)
        log.propagate = False

    # ------------------------------------------------------------------ yardımcılar
    def say(self, msg: str, style: str = ""):
        console.print(f"[dim]{datetime.now(NY):%H:%M:%S} ET[/] " + (f"[{style}]{msg}[/]" if style else msg))
        log.info(msg)

    def _journal(self, row: dict):
        new = not self.journal.exists()
        with open(self.journal, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    # ------------------------------------------------------------------ gün hazırlığı
    def prepare_day(self, today: str):
        self.say(f"=== {today} için hazırlık: evren ve günlük skorlar ===", "bold cyan")
        s, li = self.cfg["strategy"], self.li
        tickers = get_universe(self.cfg)
        daily = load_data(self.cfg, tickers + ["QQQ", "^VIX"], period="2y")
        rl = regime_series(daily.get("QQQ"), daily.get("^VIX"))
        rs = regime_short_series(daily.get("QQQ"))
        self.mult_long = float(rl.iloc[-1]) if len(rl) else 1.0
        self.mult_short = float(rs.iloc[-1]) if len(rs) else 1.0
        self.ds = {}
        self.w1d, self.w5m = load_weights(self.cfg, "1d"), load_weights(self.cfg, "5m")
        for t in tickers:
            df = daily.get(t)
            if df is None or len(df) < 220:
                continue
            ds = daily_scores(df, s, self.w1d)
            last = ds.iloc[-1]
            if (last["d_dvol"] >= s["min_avg_dollar_volume"]
                    and li["min_daily_atr_pct"] <= last["d_atr_pct"] <= li["max_daily_atr_pct"]):
                self.ds[t] = ds
        self.universe = list(self.ds)
        self.day, self.day_done = today, False
        self.trades_today, self.cooldown, self.enrich_cache = 0, {}, {}
        self.say(f"Evren: {len(self.universe)} hisse (likidite + oynaklık filtresi) | "
                 f"rejim çarpanı long {self.mult_long:.2f} / short {self.mult_short:.2f}")

        # Rejection Block bölgelerini ve alfa profilini hazırla
        self.alpha_profile = load_alpha_profile(self.cfg.get("state_dir", "state"))
        self.alpha_map = self.alpha_profile.get("tickers", {})
        self.rb_zones = {}
        for t in self.universe:
            df = daily.get(t)
            if df is not None and len(df) >= 60:
                try:
                    d = add_indicators(df)
                    _, _, zones = find_rejection_blocks(d)
                    self.rb_zones[t] = [z for z in zones if z.end is None]
                except Exception:
                    pass
        n_rb = sum(len(z) for z in self.rb_zones.values())
        self.say(f"Rejection Block (SMC): {len(self.alpha_map)} kanıtlanmış alfa hisse, {n_rb} aktif bölge izleniyor.")

        top_l = sorted(self.universe, key=lambda t: -self.ds[t]["d_long"].iloc[-1])[:5]
        top_s = sorted(self.universe, key=lambda t: -self.ds[t]["d_short"].iloc[-1])[:5]
        self.say(f"Günlük grafikte en güçlü long adayları: {', '.join(top_l)}")
        self.say(f"Günlük grafikte en güçlü short adayları: {', '.join(top_s)}")

    # ------------------------------------------------------------------ haber/analist
    def _enrich(self, t: str):
        c = self.enrich_cache.get(t)
        if c and datetime.now(NY) - c[0] < timedelta(minutes=self.li["news_refresh_minutes"]):
            return c[1], c[2]
        s = self.cfg["strategy"]
        news = news_sentiment(t, s["news_max_age_hours"])
        an = analyst_view(t, s["avoid_earnings_days"])
        self.enrich_cache[t] = (datetime.now(NY), news, an)
        return news, an

    # ------------------------------------------------------------------ tek döngü
    def cycle(self, clock: dict):
        li, now = self.li, datetime.now(NY)
        acct = self.broker.account()
        eq, last_eq = float(acct["equity"]), float(acct["last_equity"])

        if not self.analysis_only:
            day = self.rm.evaluate(eq, last_eq)
            if day["action"]:
                self.broker.close_all()
                self.say(("GÜNLÜK HEDEF +%" if day["action"] == "TARGET" else "GÜNLÜK ZARAR LİMİTİ %")
                         + f"{day['pnl_pct']:.2f} -> tüm pozisyonlar kapatıldı, bugün işlem yok.",
                         "bold green" if day["action"] == "TARGET" else "bold red")
                self.day_done = True
                return
            mins_to_close = (clock["next_close"] - now).total_seconds() / 60
            if mins_to_close <= li["flatten_minutes_before_close"]:
                if self.broker.positions():
                    self.broker.close_all()
                    self.say("Kapanışa az kaldı -> tüm pozisyonlar kapatıldı (gece taşınmaz).", "yellow")
                self.day_done = True
                return
        else:
            day = {"pnl_pct": (eq / last_eq - 1) * 100 if last_eq else 0, "can_open": True}
            mins_to_close = 999

        # ---- veri
        intr = download_ohlcv(self.universe + ["QQQ"], period=li["intraday_period"], interval="5m",
                              cache_dir=self.cfg["data"]["cache_dir"], ttl_minutes=0)
        intr = {t: session_only(drop_incomplete(df, 5)) for t, df in intr.items()}
        if hasattr(self.broker, "on_bars"):
            self.broker.on_bars(intr)
        qflag = qqq_vwap_flag(intr.get("QQQ"))

        rows = []
        for t in self.universe:
            df5 = intr.get(t)
            if df5 is None or len(df5) < 250:
                continue
            if not self.analysis_only and now.replace(tzinfo=None) - df5.index[-1].to_pydatetime() > timedelta(minutes=20):
                continue  # bayat veri
            try:
                f, pats = build_intraday(df5, self.ds[t], qflag, li, self.w5m)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: %s", t, e)
                continue
            last = f.iloc[-1]
            if np.isnan(last["L"]):
                continue

            pats_list = patterns_on(pats)
            rb_zone = None
            c_px, a_px = float(last["Close"]), float(last["d_atr"])
            for z in self.rb_zones.get(t, []):
                if z.side > 0 and (z.bot <= c_px <= z.top + 0.3 * a_px):
                    rb_zone = z
                    pats_list.append(f"RB Boğa [{z.bot:.2f}-{z.top:.2f}]")
                    bonus = 20.0 + (10.0 if t in self.alpha_map else 0.0)
                    last["L"] = min(100.0, float(last["L"]) + bonus)
                    break
                elif z.side < 0 and (z.bot - 0.3 * a_px <= c_px <= z.top):
                    rb_zone = z
                    pats_list.append(f"RB Ayı [{z.bot:.2f}-{z.top:.2f}]")
                    bonus = 20.0 + (10.0 if t in self.alpha_map else 0.0)
                    last["S"] = min(100.0, float(last["S"]) + bonus)
                    break

            rows.append({"t": t, "L": float(last["L"]), "S": float(last["S"]), "close": float(last["Close"]),
                         "vwap": float(last["vwap"]), "rsi": float(last["rsi"]), "d_atr": float(last["d_atr"]),
                         "bar": f.index[-1], "patterns": pats_list, "rb_zone": rb_zone})

        # ---- açık pozisyon yönetimi
        positions = {p["symbol"]: p for p in self.broker.positions()}
        for sym in list(self.known):
            if sym not in positions:
                self.say(f"{sym} pozisyonu kapandı (stop/hedef).")
                self.known.pop(sym)
                self.cooldown[sym] = now
        by_t = {r["t"]: r for r in rows}
        if li.get("reversal_exit", True) and not self.analysis_only:
            for sym, p in positions.items():
                r = by_t.get(sym)
                if not r:
                    continue
                opp = r["S"] if p["side"] == "long" else r["L"]
                own = r["L"] if p["side"] == "long" else r["S"]
                if opp >= li["threshold"] and opp - own >= li["min_margin"]:
                    self.broker.close_position(sym)
                    self.known.pop(sym, None)
                    self.cooldown[sym] = now
                    self.say(f"{sym} {p['side']} kapatıldı: ters yönde güçlü sinyal (skor {opp:.0f})", "yellow")
            positions = {p["symbol"]: p for p in self.broker.positions()}

        # ---- yeni giriş
        mins_since_open = (now - clock["next_close"].replace(hour=9, minute=30)).total_seconds() / 60
        can_open = (day["can_open"] and mins_since_open >= li["no_entry_first_minutes"]
                    and mins_to_close >= li["no_entry_last_minutes"]
                    and self.trades_today < li["max_trades_per_day"])
        pdt_block = (float(acct.get("equity", 0)) < 25000 and int(acct.get("daytrade_count", 0) or 0) >= 3
                     and getattr(self.broker, "name", "") == "ALPACA")
        if pdt_block:
            can_open = False
            self.say("PDT kuralı: 25.000$ altı hesapta 5 günde 3 gün-içi işlem sınırı doldu, yeni işlem yok.", "yellow")

        cands = []
        for r in rows:
            side, sc = pick_side(r["L"], r["S"], li, self.mult_long, self.mult_short)
            r["side"], r["score"] = side, sc
            if side:
                cands.append(r)
        cands.sort(key=lambda r: -r["score"])

        opened = []
        slots = self.cfg["live"]["max_positions"] - len(positions)
        if (can_open or self.analysis_only) and slots > 0:
            for r in cands[: li["enrich_top_n"]]:
                if slots <= 0:
                    break
                t = r["t"]
                if t in positions:
                    continue
                if t in self.cooldown and now - self.cooldown[t] < timedelta(minutes=li["cooldown_minutes"]):
                    continue
                news, an = self._enrich(t)
                pts = 10 * news["score"] + an["score"]
                L2, S2 = min(100, r["L"] + pts), min(100, r["S"] - pts)
                side, sc = pick_side(L2, max(0, S2), li, self.mult_long, self.mult_short)
                r.update({"side": side, "score": sc, "news": news["score"], "analyst": an["score"]})
                if not side or an["earnings_soon"]:
                    continue
                if side == "short" and not self.broker.is_shortable(t):
                    continue
                if r.get("rb_zone") is not None:
                    z = r["rb_zone"]
                    c_px, a_px = r["close"], r["d_atr"]
                    if side == "long":
                        stop = z.bot - 0.25 * a_px
                        risk = c_px - stop
                        if risk <= 0:
                            risk = 0.01 * c_px
                            stop = c_px - risk
                        target = c_px + 3.0 * risk
                    else:
                        stop = z.top + 0.25 * a_px
                        risk = stop - c_px
                        if risk <= 0:
                            risk = 0.01 * c_px
                            stop = c_px + risk
                        target = c_px - 3.0 * risk
                    plan = {
                        "entry": c_px,
                        "stop": stop,
                        "target": target,
                        "target_pct": abs(target - c_px) / c_px * 100,
                        "stop_pct": abs(c_px - stop) / c_px * 100,
                        "rr": 3.0
                    }
                else:
                    plan = intraday_plan(side, r["close"], r["d_atr"], li)
                qty = self._size(eq, plan, positions)
                if qty < 1:
                    continue
                r["plan"], r["qty"] = plan, qty
                opened.append(r)
                slots -= 1
                if self.analysis_only:
                    continue
                try:
                    self.broker.place_bracket(t, qty, "buy" if side == "long" else "sell",
                                              plan["target"], plan["stop"], tif="day")
                except Exception as e:  # noqa: BLE001
                    self.say(f"{t} emir hatası: {e}", "red")
                    continue
                self.trades_today += 1
                self.known[t] = side
                self.cooldown[t] = now
                self.say(f"{'LONG' if side == 'long' else 'SHORT'} {t} x{qty} @~{plan['entry']:.2f} | "
                         f"stop {plan['stop']:.2f} | hedef {plan['target']:.2f} ({plan['target_pct']:.1f}%) | "
                         f"skor {sc:.0f} | {', '.join(r['patterns']) or '-'}",
                         "bold green" if side == "long" else "bold magenta")
                self._journal({"zaman": now.isoformat(timespec="minutes"), "mod": self.broker.name,
                               "hisse": t, "yon": side, "adet": qty, "giris": round(plan["entry"], 2),
                               "stop": round(plan["stop"], 2), "hedef": round(plan["target"], 2),
                               "skor": round(sc, 1), "haber": round(news["score"], 2), "analist": round(an["score"], 1),
                               "formasyonlar": "; ".join(r["patterns"])})

        self._print_status(eq, day["pnl_pct"], rows, positions, opened)

    def _size(self, eq: float, plan: dict, positions: dict) -> int:
        li = self.li
        per_share = abs(plan["entry"] - plan["stop"])
        if per_share <= 0:
            return 0
        by_risk = eq * li["risk_per_trade_pct"] / 100 / per_share
        by_cap = eq * li["max_position_pct"] / 100 / plan["entry"]
        gross = sum(float(p["qty"]) * float(p.get("current_price") or p["avg_entry_price"]) for p in positions.values())
        room = max(0.0, eq * li["max_gross_exposure"] - gross) / plan["entry"]
        return int(max(0, min(by_risk, by_cap, room)))

    def _print_status(self, eq, pnl, rows, positions, opened):
        t = Table(title=f"{datetime.now(NY):%H:%M} ET | özsermaye ${eq:,.2f} | günlük {pnl:+.2f}% | "
                        f"işlem {self.trades_today}/{self.li['max_trades_per_day']} | {self.broker.name}",
                  header_style="bold cyan")
        for c in ["Hisse", "Fiyat", "Long", "Short", "VWAP", "RSI", "Mumlar (5dk)", "Karar"]:
            t.add_column(c, justify="left" if c in ("Hisse", "Mumlar (5dk)", "Karar") else "right")
        top = sorted(rows, key=lambda r: -max(r["L"], r["S"]))[:10]
        opened_t = {r["t"] for r in opened}
        for r in top:
            dec = r.get("side") or "-"
            if r["t"] in positions:
                dec = f"AÇIK ({positions[r['t']]['side']})"
            elif r["t"] in opened_t:
                dec = ("GİRİŞ " if not self.analysis_only else "ADAY ") + r["side"].upper()
            t.add_row(r["t"], f"{r['close']:.2f}", f"{r['L']:.0f}", f"{r['S']:.0f}",
                      "üstü" if r["close"] > r["vwap"] else "altı", f"{r['rsi']:.0f}",
                      ", ".join(r["patterns"])[:30] or "-", dec)
        console.print(t)
        for s, p in positions.items():
            console.print(f"  [bold]{s}[/] {p['side']} {float(p['qty']):.0f} adet | giriş {float(p['avg_entry_price']):.2f} | "
                          f"K/Z ${float(p['unrealized_pl']):+,.2f}")

    # ------------------------------------------------------------------ ana döngü
    def run(self, once: bool = False):
        li = self.li
        self.say(f"Canlı bot başladı | broker: {self.broker.name} | short: {'açık' if li.get('allow_short', True) else 'kapalı'} | "
                 f"hedef +%{self.cfg['risk']['daily_profit_target_pct']} / limit -%{self.cfg['risk']['daily_max_loss_pct']}",
                 "bold")
        while True:
            try:
                clock = get_clock(self.broker)
                now = datetime.now(NY)
                today = now.date().isoformat()
                if self.analysis_only and once:
                    if self.day != today:
                        self.prepare_day(today)
                    self.cycle(clock)
                    return
                if not clock["is_open"]:
                    wait = (clock["next_open"] - now).total_seconds()
                    self.say(f"Piyasa kapalı. Sonraki açılış: {clock['next_open']:%d.%m %H:%M} ET "
                             f"(TR {clock['next_open'].astimezone(_TR):%H:%M}). Bekleniyor...")
                    if once:
                        return
                    # açılıştan ~20 dk önce gün hazırlığını yap
                    if 0 < wait <= 25 * 60 and self.day != clock["next_open"].date().isoformat():
                        self.prepare_day(clock["next_open"].date().isoformat())
                    time.sleep(min(max(wait - 20 * 60, 60), 30 * 60) if wait > 25 * 60 else 60)
                    continue
                if self.day != today:
                    self.prepare_day(today)
                if not self.day_done:
                    self.cycle(clock)
                elif once:
                    self.say("Bugünün işlemleri tamamlandı.")
                if once:
                    return
                time.sleep(self._seconds_to_next_bar())
            except KeyboardInterrupt:
                self.say("Kullanıcı tarafından durduruldu. Açık pozisyonlar brokerda stop/hedefleriyle duruyor.", "yellow")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("döngü hatası")
                self.say(f"Hata: {e} -> 60 sn sonra tekrar denenecek", "red")
                if once:
                    raise
                time.sleep(60)

    def _seconds_to_next_bar(self) -> float:
        now = datetime.now(NY)
        step = self.li["cycle_minutes"]
        nxt = (now + timedelta(minutes=step)).replace(second=0, microsecond=0)
        nxt = nxt.replace(minute=(nxt.minute // step) * step) + timedelta(seconds=self.li["bar_delay_seconds"])
        return max(5.0, (nxt - now).total_seconds())
