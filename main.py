"""NASDAQ Analiz & İşlem Botu - komut satırı arayüzü.

Kullanım:
  python main.py scan                 # tüm NASDAQ-100'ü tara, AL sinyallerini listele
  python main.py analyze NVDA         # tek hisse için detaylı analiz
  python main.py backtest --years 3   # stratejiyi geçmiş veride test et
  python main.py trade                # emir planını göster (kuru çalışma)
  python main.py trade --execute      # Alpaca paper hesabına bracket emir gönder
  python main.py guard                # seans boyunca %1 hedef / zarar limitini izle
  python main.py status               # hesap ve günlük kâr/zarar durumu
  python main.py live                 # piyasa açıkken sürekli gün içi long/short bot
  python main.py pattern-lab          # tüm formasyonları 4 zaman diliminde istatistiksel test et
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from nasdaq_bot.config import load_config
from nasdaq_bot.data import NY

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DISCLAIMER = ("[dim]Bu bot yatırım tavsiyesi değildir. Hiçbir sistem her gün %1 kârı GARANTİ edemez; "
              "önce backtest ve paper hesapta deneyin.[/dim]")


def cmd_scan(cfg, args):
    if getattr(args, "strategy", "classic") == "rb":
        return cmd_rb_scan(cfg, args)
    from nasdaq_bot.report import console, print_regime, print_scan, save_scan
    from nasdaq_bot.risk import RiskManager
    from nasdaq_bot.strategy import scan

    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    with console.status("Veriler indiriliyor ve analiz ediliyor..."):
        results, regime = scan(cfg, tickers, enrich=not args.no_news)
    print_regime(regime)
    print_scan(results, args.top, RiskManager(cfg), cfg["risk"]["account_size"])
    path = save_scan(results, regime, cfg["reports_dir"])
    buys = [r["ticker"] for r in results if r["signal"] == "AL"]
    console.print(f"[bold]AL sinyalleri:[/] {', '.join(buys) if buys else 'yok (bugün işlem yapmamak en iyisi olabilir)'}")
    console.print(f"Rapor: {path} (+ .md)")
    console.print(DISCLAIMER)


def cmd_rb_scan(cfg, args):
    from nasdaq_bot.report import console
    from nasdaq_bot.risk import RiskManager
    from nasdaq_bot.rb_scanner import scan_rb_setups, print_rb_table, save_rb_scan

    tf = getattr(args, "tf", "1d")
    only_alpha = not getattr(args, "all", False)
    tickers = getattr(args, "tickers", None)
    top = getattr(args, "top", 15)

    with console.status(f"[{tf}] Rejection Block taraması yapılıyor..."):
        setups = scan_rb_setups(cfg, tf=tf, tickers=tickers, only_alpha=only_alpha)
    rm = RiskManager(cfg)
    print_rb_table(setups, rm, cfg["risk"]["account_size"], top_n=top)
    path = save_rb_scan(setups, cfg["reports_dir"])
    console.print(f"Rapor: {path} (+ .csv)")
    console.print(DISCLAIMER)


def cmd_rb_backtest(cfg, args):
    from nasdaq_bot.rb_backtest import run_portfolio_backtest
    from nasdaq_bot.report import console

    with console.status("Rejection Block portföy simülasyonu çalıştırılıyor..."):
        run_portfolio_backtest(
            trades_path=str(Path(cfg["reports_dir"]) / "learn_rb_1d_oos_kural.csv"),
            alpha_path=str(Path(cfg["state_dir"]) / "learned" / "rb_alpha_tickers.json"),
            reports_dir=cfg["reports_dir"],
            initial_capital=float(cfg["risk"]["account_size"]),
            max_positions=int(cfg["risk"]["max_positions"]),
            risk_per_trade_pct=float(cfg["risk"].get("risk_per_trade_pct", 1.0)) / 100.0,
        )
    console.print(DISCLAIMER)


def cmd_analyze(cfg, args):
    from nasdaq_bot.candles import detect_patterns, pattern_stats, patterns_on
    from nasdaq_bot.fundamentals import analyst_view
    from nasdaq_bot.report import console, print_analysis, print_regime
    from nasdaq_bot.sentiment import news_sentiment
    from nasdaq_bot.strategy import analyze_frame, load_data, regime_notes, trade_plan
    from nasdaq_bot.weights import load_weights

    t = args.ticker.upper()
    s = cfg["strategy"]
    with console.status(f"{t} analiz ediliyor..."):
        data = load_data(cfg, [t, "QQQ", "^VIX"], period="5y")
        if t not in data or len(data[t]) < 220:
            console.print(f"[red]{t} için yeterli veri yok.[/]")
            return
        d, pats = analyze_frame(data[t], s, load_weights(cfg, "1d"))
        stats = pattern_stats(d, pats, horizon=s["max_hold_days"], target_pct=s["min_target_pct"])
        news = news_sentiment(t, s["news_max_age_hours"])
        an = analyst_view(t, s["avoid_earnings_days"])
        regime = regime_notes(data.get("QQQ"), data.get("^VIX"))
    last = d.iloc[-1]
    plan = trade_plan(float(last["Close"]), float(last["atr"]), s)
    raw = last["tech_score"] + 10 * news["score"] + an["score"]
    plan["score"] = float(np.clip(raw * regime["multiplier"], 0, 100))
    ok = (plan["score"] >= s["buy_threshold"] and plan["rr"] >= s["min_reward_risk"]
          and s["min_atr_pct"] <= last["atr_pct"] <= s["max_atr_pct"] and not an["earnings_soon"])
    plan["signal"] = "AL" if ok else ("İZLE" if plan["score"] >= s["buy_threshold"] - 10 else "BEKLE / ALMA")
    parts = {k: float(last[k]) for k in ("sc_trend", "sc_momentum", "sc_candle", "sc_volume", "sc_setup")}
    parts["haber"], parts["analist"] = 10 * news["score"], an["score"]
    print_regime(regime)
    print_analysis(t, d, [patterns_on(pats, i) for i in range(-5, 0)], stats, news, an, plan, parts)
    console.print(DISCLAIMER)


def cmd_backtest(cfg, args):
    from nasdaq_bot.backtest import run_backtest
    from nasdaq_bot.report import console, print_backtest, save_backtest
    from nasdaq_bot.strategy import get_universe, load_data

    tickers = [t.upper() for t in args.tickers] if args.tickers else get_universe(cfg)
    period = f"{int(np.ceil(args.years)) + 1}y"
    with console.status(f"{len(tickers)} hisse için {period} veri indiriliyor ve simüle ediliyor..."):
        cfg["data"]["use_partial_bar"] = False
        data = load_data(cfg, tickers + ["QQQ", "^VIX"], period=period)
        res = run_backtest(data, cfg, args.years)
    print_backtest(res)
    out = save_backtest(res, cfg["reports_dir"])
    console.print(f"İşlem listesi ve sermaye eğrisi: {out}")
    console.print("[yellow]Not: güncel NASDAQ-100 listesi geçmişe uygulandığı için sonuçlar bir miktar iyimserdir (survivorship bias).[/]")


def _broker(cfg, console):
    from nasdaq_bot.broker import AlpacaBroker, BrokerError
    try:
        b = AlpacaBroker.from_env(cfg)
        b.account()
        return b
    except BrokerError as e:
        console.print(f"[red]Broker bağlantısı kurulamadı: {e}[/]")
        return None


def cmd_trade(cfg, args):
    from nasdaq_bot.report import console, print_regime, print_scan, save_scan
    from nasdaq_bot.risk import RiskManager
    from nasdaq_bot.strategy import scan

    rm = RiskManager(cfg)
    broker = None
    if os.getenv("ALPACA_API_KEY"):
        broker = _broker(cfg, console)
    if args.execute:
        if not cfg["broker"].get("paper", True) and not args.live_onay:
            console.print("[red]Gerçek hesapta işlem için --live-onay bayrağı gereklidir.[/]")
            return
        if broker is None:
            broker = _broker(cfg, console)
        if broker is None:
            return

    if broker:
        acct = broker.account()
        equity, cash = float(acct["equity"]), float(acct["cash"])
        day = rm.evaluate(equity, float(acct["last_equity"]))
        held = {p["symbol"] for p in broker.positions()}
        stale = broker.cancel_stale_entries()
        if stale:
            console.print(f"Dolmamış eski giriş emirleri iptal edildi: {', '.join(stale)}")
        held |= {o["symbol"] for o in broker.open_orders() if o.get("side") == "buy"}
        console.print(f"Hesap ({'PAPER' if broker.paper else 'GERÇEK'}): özsermaye ${equity:,.2f}, nakit ${cash:,.2f}, "
                      f"günlük K/Z %{day['pnl_pct']:+.2f}")
        if not day["can_open"]:
            console.print("[yellow]Bugün için günlük hedefe ulaşıldı veya zarar limiti aşıldı -> yeni işlem açılmıyor.[/]")
            return
    else:
        equity = cash = float(cfg["risk"]["account_size"])
        held = set()

    if getattr(args, "strategy", "classic") == "rb":
        from nasdaq_bot.rb_scanner import scan_rb_setups, print_rb_table, save_rb_scan
        tf = getattr(args, "tf", "1d")
        with console.status(f"Rejection Block taraması yapılıyor ({tf})..."):
            setups = scan_rb_setups(cfg, tf=tf, tickers=getattr(args, "tickers", None), only_alpha=not getattr(args, "all", False))
        
        # Sadece hemen aksiyon alınabilecek (tetiklenen, bölgede veya 1 ATR yaklaşan) fırsatları öne al
        actionable = [s for s in setups if any(k in s.get("durum", "") for k in ["TETİK", "BÖLGE", "YAKLAŞ"])]
        
        # Eğer seçilen TF'de hazır fırsat azsa ve TF günlük ise, seans içi 1h ve 15m'den takviye et
        if len(actionable) < cfg["risk"]["max_positions"] and tf == "1d":
            with console.status("Seans içi (1h ve 15m) aktif Rejection Block bölgeleri taranıyor..."):
                extra_1h = scan_rb_setups(cfg, tf="1h", only_alpha=True)
                extra_15m = scan_rb_setups(cfg, tf="15m", only_alpha=True)
                for s in extra_15m + extra_1h:
                    if any(k in s.get("durum", "") for k in ["TETİK", "BÖLGE", "YAKLAŞ"]):
                        if s["ticker"] not in {x["ticker"] for x in actionable}:
                            actionable.append(s)
        
        chosen_setups = actionable if actionable else setups
        print_rb_table(chosen_setups, rm, equity, top_n=15)
        save_rb_scan(chosen_setups, cfg["reports_dir"])
        slots = cfg["risk"]["max_positions"] - len(held)
        orders = []
        for s in chosen_setups:
            if slots <= 0:
                break
            if s["ticker"] in held:
                continue
            qty = rm.position_size(equity, s["entry"], s["stop"], cash)
            if qty < 1:
                continue
            orders.append((s, qty))
            cash -= qty * s["entry"]
            slots -= 1

        if not orders:
            console.print("[yellow]Bugün kriterleri karşılayan uygun RB işlemi bulunamadı.[/]")
            return

        for s, qty in orders:
            risk_usd = qty * abs(s["entry"] - s["stop"])
            side_str = "buy" if s["yon"] == "LONG" else "sell"
            console.print(f"[bold]{s['ticker']}[/] {s['yon']} {qty} adet | limit/giriş ${s['entry']:.2f} | "
                          f"stop ${s['stop']:.2f} | hedef ${s['target']:.2f} (+%{s['target_pct']:.1f}) | risk ${risk_usd:,.0f}")
            if broker and args.execute:
                try:
                    broker.place_bracket(s["ticker"], qty, side_str, s["target"], s["stop"], limit=s["entry"], tif="gtc")
                    rm.record_entry(s["ticker"], s)
                    console.print("   [green]Rejection Block bracket emri gönderildi[/]")
                except Exception as e:
                    console.print(f"   [red]emir hatası: {e}[/]")
        if not args.execute:
            console.print("[dim]Kuru çalışma: emir gönderilmedi. Göndermek için --execute kullanın.[/dim]")
        console.print(DISCLAIMER)
        return

    with console.status("Tarama yapılıyor..."):
        results, regime = scan(cfg)
    print_regime(regime)
    print_scan(results, 15, rm, equity)
    save_scan(results, regime, cfg["reports_dir"])

    slots = cfg["risk"]["max_positions"] - len(held)
    orders = []
    for r in results:
        if slots <= 0:
            break
        if r["signal"] != "AL" or r["ticker"] in held:
            continue
        qty = rm.position_size(equity, r["limit"], r["stop"], cash)
        if qty < 1:
            continue
        orders.append((r, qty))
        cash -= qty * r["limit"]
        slots -= 1

    if not orders:
        console.print("[yellow]Bugün kriterleri karşılayan işlem yok. Bot zorla işlem açmaz.[/]")
        return
    for r, qty in orders:
        risk_usd = qty * (r["limit"] - r["stop"])
        console.print(f"[bold]{r['ticker']}[/] {qty} adet | limit {r['limit']:.2f} | stop {r['stop']:.2f} | "
                      f"hedef {r['target']:.2f} (+%{r['target_pct']:.1f}) | risk ${risk_usd:,.0f}")
        if broker:
            try:
                broker.place_bracket(r["ticker"], qty, "buy", r["target"], r["stop"], limit=r["limit"], tif="gtc")
                rm.record_entry(r["ticker"], r)
                console.print("   [green]emir gönderildi[/]")
            except Exception as e:  # noqa: BLE001
                console.print(f"   [red]emir hatası: {e}[/]")
    if not broker:
        console.print("[dim]Kuru çalışma: emir gönderilmedi. Göndermek için --execute kullanın.[/dim]")
    console.print(DISCLAIMER)


def cmd_guard(cfg, args):
    """Seans boyunca günlük hedefi/zarar limitini ve zaman stoplarını izler."""
    import numpy as _np

    from nasdaq_bot.report import console
    from nasdaq_bot.risk import RiskManager

    broker = _broker(cfg, console)
    if broker is None:
        return
    rm = RiskManager(cfg)
    max_hold = cfg["strategy"]["max_hold_days"]
    console.print(f"Bekçi başladı: hedef +%{cfg['risk']['daily_profit_target_pct']}, "
                  f"zarar limiti -%{cfg['risk']['daily_max_loss_pct']}, her {args.interval} dk. (Ctrl+C ile çık)")
    while True:
        try:
            clock = broker.clock()
            if clock.get("is_open"):
                acct = broker.account()
                day = rm.evaluate(float(acct["equity"]), float(acct["last_equity"]))
                now = datetime.now(NY)
                console.print(f"{now:%H:%M} ET | özsermaye ${float(acct['equity']):,.2f} | günlük %{day['pnl_pct']:+.2f}")
                if day["action"] in ("TARGET", "HALT"):
                    broker.close_all()
                    rm.forget(list(rm.load_positions()))
                    msg = "GÜNLÜK HEDEFE ULAŞILDI - tüm pozisyonlar kapatıldı" if day["action"] == "TARGET" \
                        else "GÜNLÜK ZARAR LİMİTİ - tüm pozisyonlar kapatıldı"
                    console.print(f"[bold {'green' if day['action'] == 'TARGET' else 'red'}]{msg}[/]")
                # Zaman stopu: kapanıştan 10 dk önce, süresi dolan pozisyonları kapat
                if now.time() >= dtime(15, 50):
                    held = {p["symbol"] for p in broker.positions()}
                    rec = rm.load_positions()
                    for sym, info in rec.items():
                        if sym in held and _np.busday_count(info["entry_date"], now.date().isoformat()) >= max_hold - 1:
                            broker.close_position(sym)
                            console.print(f"Zaman stopu: {sym} kapatıldı")
                    rm.forget([s for s in rec if s not in held])
            else:
                console.print(f"{datetime.now(NY):%H:%M} ET | piyasa kapalı (sonraki açılış {clock.get('next_open', '?')})")
            if args.once:
                break
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]Hata: {e}[/] - 60 sn sonra tekrar denenecek")
            time.sleep(60)


def cmd_status(cfg, args):
    from nasdaq_bot.report import console
    from nasdaq_bot.risk import RiskManager

    broker = _broker(cfg, console)
    if broker is None:
        return
    acct = broker.account()
    day = RiskManager(cfg).load_day()
    eq, last = float(acct["equity"]), float(acct["last_equity"])
    console.print(f"Hesap: {'PAPER' if broker.paper else 'GERÇEK'} | özsermaye ${eq:,.2f} | günlük %{(eq / last - 1) * 100:+.2f}")
    console.print(f"Hedef kilidi: {day['target_hit']} | zarar kilidi: {day['loss_halt']}")
    for p in broker.positions():
        console.print(f"  {p['symbol']:6} {p['qty']:>6} adet | ort. {float(p['avg_entry_price']):.2f} | "
                      f"K/Z ${float(p['unrealized_pl']):+,.2f} (%{float(p['unrealized_plpc']) * 100:+.2f})")


def cmd_live(cfg, args):
    from nasdaq_bot.broker import SimBroker
    from nasdaq_bot.live import LiveTrader
    from nasdaq_bot.report import console

    if args.no_short:
        cfg["live"]["allow_short"] = False
    if args.alpaca:
        if not cfg["broker"].get("paper", True) and not args.live_onay:
            console.print("[red]Gerçek para hesabı için --live-onay bayrağı gereklidir.[/]")
            return
        broker = _broker(cfg, console)
        if broker is None:
            return
    else:
        broker = SimBroker(cfg)
        console.print("[cyan]SIM modu: gerçek emir yok, canlı veriyle sanal hesapta işlem yapılır "
                      "(state/sim_account.json). Alpaca için --alpaca kullanın.[/]")
    LiveTrader(cfg, broker, analysis_only=args.analiz).run(once=args.once or args.analiz)


def cmd_backtest_intraday(cfg, args):
    from nasdaq_bot.backtest_intraday import run_intraday_backtest
    from nasdaq_bot.report import console, print_backtest
    from nasdaq_bot.strategy import get_universe

    if args.no_short:
        cfg["live"]["allow_short"] = False
    tickers = [t.upper() for t in args.tickers] if args.tickers else get_universe(cfg)
    with console.status(f"{len(tickers)} hisse için 60 günlük 5 dk veri indiriliyor ve simüle ediliyor..."):
        res = run_intraday_backtest(cfg, tickers)
    res["stats"] = {k: v for k, v in res["stats"].items()}
    print_backtest({"stats": res["stats"]})
    from pathlib import Path
    out = Path(cfg["reports_dir"])
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    res["trades"].to_csv(out / f"intraday_trades_{stamp}.csv", index=False, encoding="utf-8-sig")
    res["daily"].to_csv(out / f"intraday_daily_{stamp}.csv", encoding="utf-8-sig")
    console.print(f"İşlemler ve günlük sonuçlar: {out}")


def cmd_pattern_lab(cfg, args):
    from rich.table import Table

    from nasdaq_bot.pattern_lab import TIMEFRAMES, run_lab, save_weights
    from nasdaq_bot.report import console
    from nasdaq_bot.strategy import get_universe

    tickers = [t.upper() for t in args.tickers] if args.tickers else get_universe(cfg)
    out = Path(cfg["reports_dir"])
    out.mkdir(exist_ok=True)
    styles = {"GEÇERLİ": "bold green", "ZAYIF": "yellow", "TERS": "bold magenta", "GEÇERSİZ": "red",
              "AZ ÖRNEK": "dim", "VERİ YOK": "dim"}
    only = None
    if args.only:
        from nasdaq_bot.candles import PATTERNS
        from nasdaq_bot.chart_patterns import _CLASSIC
        alias = {"rb": ["rb_"], "mum": list(PATTERNS), "grafik": list(_CLASSIC)}
        only = [p for o in args.only for p in alias.get(o.lower(), [o])]
    suffix = "" if not args.only else "_" + "-".join(args.only)
    for tf in args.tf:
        spec = TIMEFRAMES[tf]
        with console.status(f"[{tf}] {len(tickers)} hisse, {spec['period']} veri: formasyonlar test ediliyor..."):
            res = run_lab(cfg, tf, tickers, only=only)
        a = res.attrs
        t = Table(title=f"Formasyon Laboratuvarı [{tf}] {a['start']} → {a['end']} | {a['n_tickers']} hisse | "
                        f"baz: long {a['base_long']:+.3f}% / short {a['base_short']:+.3f}% | bölme: {a['split']}",
                  header_style="bold cyan")
        cols = ["Formasyon", "Tür", "Yön", "Adet", "Gün", "Kazanma%", "Net ort%", "Fark%", "t", "1.yarı%", "2.yarı%", "Karar"]
        for c in cols:
            t.add_column(c, justify="left" if c in ("Formasyon", "Tür", "Yön", "Karar") else "right")
        fmt = lambda v, f: "-" if pd.isna(v) else format(v, f)  # noqa: E731
        for _, r in res.iterrows():
            t.add_row(r["Formasyon"], r["Tür"], r["Yön"], str(r["Adet"]), str(r["Gün"]), fmt(r["Kazanma%"], ".1f"),
                      fmt(r["Net ort%"], "+.3f"), fmt(r["Fark%"], "+.3f"), fmt(r["t"], "+.2f"),
                      fmt(r["1.yarı%"], "+.3f"), fmt(r["2.yarı%"], "+.3f"),
                      f"[{styles.get(r['Karar'], '')}]{r['Karar']}[/]")
        console.print(t)
        res.drop(columns=["weight"]).to_csv(out / f"pattern_lab_{tf}{suffix}.csv", index=False, encoding="utf-8-sig")
        if not args.no_save:
            save_weights(cfg, tf, res)
        ok = res[res["Karar"].isin(["GEÇERLİ", "TERS"])]
        console.print(f"[bold]{tf}: {len(ok)} formasyon {'bota alındı' if not args.no_save else 'geçti (kaydedilmedi)'}[/] "
                      f"({', '.join(f'{n} ({k})' for n, k in zip(ok['Formasyon'], ok['Karar'])) or 'hiçbiri'})\n")
    saved = "state/pattern_weights.json'a yazıldı" if not args.no_save else "KAYDEDİLMEDİ (--no-save)"
    console.print("[dim]Fark% = formasyon sonrası ortalama net getiri - aynı yönde rastgele girişin ortalaması. "
                  f"t = gün bazlı t-istatistiği (≥2 anlamlı). Sonuçlar {saved}; "
                  "bot sadece GEÇERLİ/TERS formasyonları kullanır.[/dim]")


def cmd_rb(cfg, args):
    """Rejection Block: bölgeleri bul, öğret, geçmiş performansı göster, grafiği çiz."""
    import json

    from rich.table import Table

    from nasdaq_bot.data import download_ohlcv
    from nasdaq_bot.indicators import add_indicators
    from nasdaq_bot.intraday import session_only
    from nasdaq_bot.pattern_lab import TIMEFRAMES, outcomes, outcomes_struct
    from nasdaq_bot.rejection_blocks import PARAMS, RB_PATTERNS, RB_STRUCT, find_rejection_blocks, plot_zones
    from nasdaq_bot.report import console

    tk, tf = args.ticker.upper(), args.tf
    spec = TIMEFRAMES[tf]
    df = download_ohlcv([tk], period=spec["period"], interval=spec["interval"], cache_dir=cfg["data"]["cache_dir"],
                        ttl_minutes=cfg["data"]["cache_ttl_minutes"]).get(tk)
    if df is None or df.empty:
        console.print(f"[red]{tk} için {tf} veri yok[/]")
        return
    if spec["intraday"]:
        df = session_only(df)
    d = add_indicators(df)
    sigs, stops, zones = find_rejection_blocks(d)
    px, atr = float(d["Close"].iloc[-1]), float(d["atr"].iloc[-1])
    fmt_t = (lambda i: d.index[i].strftime("%Y-%m-%d %H:%M")) if spec["intraday"] else (lambda i: d.index[i].strftime("%Y-%m-%d"))
    yon = {1: "[green]Boğa (destek)[/]", -1: "[red]Ayı (direnç)[/]"}

    # ---- 1) şu an izlenen bölgeler
    live = [z for z in zones if z.end is None]
    t1 = Table(title=f"{tk} [{tf}] izlenen Rejection Block bölgeleri | fiyat {px:.2f} | ATR {atr:.2f}", header_style="bold cyan")
    for c in ("Yön", "Oluştu", "Bölge (fitil)", "Fiyata uzaklık", "Durum", "Likidite süpürme", "Ne beklenir"):
        t1.add_column(c)
    for z in sorted(live, key=lambda z: abs(px - z.edge)):
        dist = (z.edge - px) / atr if atr > 0 else np.nan
        plan = (f"{z.bot:.2f}-{z.top:.2f} arasına çıkıp {z.bot:.2f} altında kapanırsa SAT, stop {z.top:.2f} üstü"
                if z.side < 0 else
                f"{z.bot:.2f}-{z.top:.2f} arasına inip {z.top:.2f} üstünde kapanırsa AL, stop {z.bot:.2f} altı")
        durum = {"aktif": "aktif (test bekliyor)", "temas": "[yellow]bölgede![/]",
                 "bekliyor": "fiyat henüz uzaklaşmadı"}.get(z.status, z.status)
        t1.add_row(yon[z.side], fmt_t(z.pivot), f"{z.bot:.2f} – {z.top:.2f}", f"{dist:+.1f} ATR", durum,
                   "evet" if z.sweep else "-", plan)
    console.print(t1 if live else f"[dim]{tk} [{tf}]: şu an izlenen RB bölgesi yok.[/]")

    # ---- 2) bu hissedeki geçmiş sinyaller ve sonuçları
    b = cfg["backtest"]
    cost = (b["commission_bps"] + b["slippage_bps"]) / 1e4
    lab = cfg.get("lab", {})
    ln, sn, _, _ = outcomes(d, spec["horizon"], lab.get("stop_atr", 1.0), lab.get("target_atr", 1.5), cost, spec["intraday"])
    t2 = Table(title=f"{tk} [{tf}] geçmiş RB sinyalleri ({d.index[0].date()} → {d.index[-1].date()}, "
                     f"en fazla {spec['horizon']} bar tutma, maliyet dahil)", header_style="bold cyan")
    for c in ("Varyasyon", "Adet", "Kazanma%", "Ort. net%", "Stop/hedef"):
        t2.add_column(c, justify="left" if c in ("Varyasyon", "Stop/hedef") else "right")
    for key, (name, direction, _) in RB_PATTERNS.items():
        m = sigs[key].to_numpy() & np.isfinite(ln)
        r = (ln if direction > 0 else sn)[m]
        if len(r):
            t2.add_row(name, str(len(r)), f"{(r > 0).mean() * 100:.0f}", f"{r.mean() * 100:+.3f}", "1 ATR / 1.5 ATR")
    for key, (name, direction, base_key) in RB_STRUCT.items():
        net, _ = outcomes_struct(d, spec["horizon"], cost, spec["intraday"], direction,
                                 stops[base_key].to_numpy(float), float(PARAMS["rr"]))
        r = net[np.isfinite(net)]
        if len(r):
            t2.add_row(name, str(len(r)), f"{(r > 0).mean() * 100:.0f}", f"{r.mean() * 100:+.3f}",
                       f"fitil ucu / {PARAMS['rr']:g}R")
    console.print(t2)

    # ---- 3) laboratuvarın tüm NASDAQ-100 üzerindeki kararı
    wpath = Path(cfg["state_dir"]) / "pattern_weights.json"
    pats = json.loads(wpath.read_text(encoding="utf-8")).get(tf, {}).get("patterns", {}) if wpath.exists() else {}
    rb_lab = {k: v for k, v in pats.items() if k.startswith("rb_")}
    if rb_lab:
        names = {**{k: v[0] for k, v in RB_PATTERNS.items()}, **{k: v[0] for k, v in RB_STRUCT.items()}}
        console.print(f"[bold]Laboratuvar kararı (NASDAQ-100, {tf}):[/] " + " · ".join(
            f"{names.get(k, k)}: {v['verdict']} (t={v['t']})" for k, v in rb_lab.items()))
    else:
        console.print(f"[dim]Tüm NASDAQ-100'de test için: python main.py pattern-lab --only rb --tf {tf}[/]")

    # ---- 4) grafik
    out = Path(cfg["reports_dir"])
    out.mkdir(exist_ok=True)
    png = out / f"rb_{tk}_{tf}.png"
    plot_zones(d, zones, sigs, png, f"{tk} [{tf}] Rejection Block", last=args.bars)
    console.print(f"Grafik: {png}")


def cmd_learn(cfg, args):
    """RB taktiğini geçmiş veriyle walk-forward öğren."""
    from rich.progress import Progress
    from rich.table import Table

    from nasdaq_bot.learn import RBLearner, _stats, daily_metrics, describe
    from nasdaq_bot.report import console
    from nasdaq_bot.strategy import get_universe

    tickers = [t.upper() for t in args.tickers] if args.tickers else get_universe(cfg)
    L = RBLearner(cfg, args.tf, tickers, log=lambda m: console.print(f"[cyan]•[/] {m}"), ml=not args.no_ml)
    with console.status("Geçmiş veri arşivi güncelleniyor..."):
        L.load()
    with console.status("RB bölgeleri ve özellikleri çıkarılıyor..."):
        L.build_events()
    from nasdaq_bot.learn import GRID, TF_SPEC
    total = len(GRID["depart"]) * len(GRID["mode"]) * len(GRID["rr"]) * len(GRID["buf"]) * \
        len(TF_SPEC[args.tf]["horizons"]) * len(TF_SPEC[args.tf]["flat"])
    with Progress(console=console, transient=True) as pg:
        task = pg.add_task("Kural setleri simüle ediliyor", total=total)
        L.simulate_all(progress=lambda: pg.advance(task))
    with console.status("Walk-forward: her dönemde sadece geçmişle öğren, sonraki dönemde test et..."):
        L.walk_forward()

    pct = lambda v, f="+.3f": "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v * 100, f)  # noqa: E731
    t = Table(title=f"Walk-forward dönemleri [{args.tf}] — seçim SADECE o tarihe kadarki veriyle", header_style="bold cyan")
    for c in ("Test dönemi", "Seçilen kural (o güne kadar en iyi)", "Eğitim n / ort% / t", "OOS n", "OOS kazanma%",
              "OOS ort. net%", "OOS PF", "ML sonrası n / ort%"):
        t.add_column(c, justify="left" if c.startswith(("Test", "Seçilen")) else "right")
    for f in L.folds:
        per = f"{f['start']:%Y-%m-%d} → {f['end']:%Y-%m-%d}"
        if not f.get("cfg"):
            t.add_row(per, "[dim]uygun kural yok → işlem yapma[/]", "-", "0", "-", "-", "-", "-")
            continue
        te, ml = f["test"], f.get("test_ml") or {}
        t.add_row(per, describe(f["cfg"]), f"{f['train_n']} / {f['train_mean'] * 100:+.3f} / {f['train_t']:.1f}",
                  str(te["n"]), "-" if not te["n"] else f"{te['win'] * 100:.0f}", pct(te["mean"]),
                  "-" if not te["n"] else f"{te['pf']:.2f}", f"{ml.get('n', '-')} / {pct(ml.get('mean'))}")
    console.print(t)

    s = Table(title="GÖRÜLMEMİŞ DÖNEM (out-of-sample) toplam sonuçları — gerçekçi beklenti budur", header_style="bold cyan")
    for c in ("Yöntem", "İşlem", "Kazanma%", "Ort. net%", "PF", "t", "Ort. R", "Günlük ort.%*", "%1+ gün*", "Maks düşüş*"):
        s.add_column(c, justify="left" if c == "Yöntem" else "right")
    summary = {}
    for name, df in (("Ham RB (öğrenmesiz: kenara limit, 1.5R)", L.oos_raw), ("Öğrenilmiş kural", L.oos_rule),
                     ("Öğrenilmiş kural + ML filtresi", L.oos_ml)):
        if df is None or df.empty:
            s.add_row(name, "0", *["-"] * 8)
            continue
        st, dm = _stats(df["net"].to_numpy()), daily_metrics(df, L.oos_days)
        summary[name] = {**{k: (float(v) if v is not None else None) for k, v in st.items()},
                         **{k: float(v) for k, v in dm.items()}}
        s.add_row(name, str(st["n"]), f"{st['win'] * 100:.1f}", f"{st['mean'] * 100:+.3f}", f"{st['pf']:.2f}",
                  f"{st['t']:.2f}", f"{df['R'].mean():+.3f}", f"{dm['ort_gunluk%']:+.3f}", f"{dm['%1+_gun%']:.1f}%",
                  f"{dm['maks_dusus%']:.1f}%")
    console.print(s)
    console.print("[dim]* her işlemde sermayenin %0.5'i riske edilirse (eş zamanlı pozisyon sınırı yok, basitleştirilmiş).[/dim]")

    best_df = L.oos_ml if (not L.oos_ml.empty and summary.get("Öğrenilmiş kural + ML filtresi", {}).get("mean", -9)
                           > summary.get("Öğrenilmiş kural", {}).get("mean", -9)) else L.oos_rule
    if not best_df.empty:
        g = best_df.groupby("ticker").agg(n=("net", "size"), win=("net", lambda x: (x > 0).mean() * 100),
                                          ort=("net", lambda x: x.mean() * 100), R=("R", "sum")).sort_values("R")
        g = g[g["n"] >= 5]
        tt = Table(title="Hisse bazında OOS (en az 5 işlem): en iyi 10 ve en kötü 5", header_style="bold cyan")
        for c in ("Hisse", "İşlem", "Kazanma%", "Ort. net%", "Toplam R"):
            tt.add_column(c, justify="left" if c == "Hisse" else "right")
        for tk, r in pd.concat([g.tail(10).iloc[::-1], g.head(5)]).iterrows():
            tt.add_row(tk, str(int(r["n"])), f"{r['win']:.0f}", f"{r['ort']:+.3f}", f"{r['R']:+.1f}")
        console.print(tt)

    final = L.final_fit()
    out = Path(cfg["reports_dir"])
    out.mkdir(exist_ok=True)
    for name, df in (("kural", L.oos_rule), ("ml", L.oos_ml), ("ham", L.oos_raw)):
        if df is not None and not df.empty:
            df.to_csv(out / f"learn_rb_{args.tf}_oos_{name}.csv", index=False, encoding="utf-8-sig")
    if final:
        path = L.save(summary)
        console.print(f"\n[bold]Tüm geçmişle seçilen güncel kural:[/] {final['aciklama']}\n"
                      f"  (eğitim: {final['train_n']} işlem, ort. {final['train_mean_pct']:+.3f}%, t={final['train_t']:.1f}"
                      f"{', ML eşiği aktif' if 'ml_threshold' in final else ''}) → {path}")
    else:
        console.print("[yellow]Tüm geçmişte bile maliyet sonrası pozitif bir kural bulunamadı.[/]")
    console.print(f"OOS işlem listeleri: {out}\\learn_rb_{args.tf}_oos_*.csv")
    console.print("[dim]Eğitim t'si ile OOS sonucu arasındaki fark = ezber (overfitting) payıdır. "
                  "Kararlarınızı yalnızca OOS satırlarına göre verin.[/dim]")


def main():
    ap = argparse.ArgumentParser(description="NASDAQ analiz & işlem botu")
    ap.add_argument("--config", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="NASDAQ-100 taraması")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--no-news", action="store_true", help="haber/analist adımını atla (hızlı)")
    p.add_argument("--strategy", choices=["classic", "rb"], default="classic", help="tarama stratejisi (classic veya rb)")
    p.add_argument("--tf", default="1d", choices=["1d", "1h", "15m", "5m"], help="RB zaman dilimi")
    p.add_argument("--all", action="store_true", help="RB taramasında tüm hisseler (varsayılan: sadece alfa hisseler)")

    p = sub.add_parser("analyze", help="tek hisse detaylı analiz")
    p.add_argument("ticker")

    p = sub.add_parser("backtest", help="geçmiş veride test")
    p.add_argument("--years", type=float, default=None)
    p.add_argument("--tickers", nargs="*")

    p = sub.add_parser("trade", help="sinyallerden emir oluştur")
    p.add_argument("--execute", action="store_true", help="emirleri brokera gönder")
    p.add_argument("--live-onay", action="store_true", help="gerçek para hesabı için açık onay")
    p.add_argument("--strategy", choices=["classic", "rb"], default="classic", help="emir stratejisi (classic veya rb)")
    p.add_argument("--tf", default="1d", choices=["1d", "1h", "15m", "5m"], help="RB zaman dilimi")
    p.add_argument("--all", action="store_true", help="RB işleminde tüm hisseler (varsayılan: sadece alfa hisseler)")
    p.add_argument("--tickers", nargs="*")

    p = sub.add_parser("guard", help="günlük hedef / zarar limiti bekçisi")
    p.add_argument("--interval", type=int, default=5, help="dakika")
    p.add_argument("--once", action="store_true")

    sub.add_parser("status", help="hesap durumu")

    p = sub.add_parser("live", help="SÜREKLİ gün içi long/short bot (borsa açıkken her 5 dk)")
    p.add_argument("--alpaca", action="store_true", help="Alpaca hesabında işlem yap (varsayılan: SIM)")
    p.add_argument("--live-onay", action="store_true", help="gerçek para hesabı için açık onay")
    p.add_argument("--no-short", action="store_true", help="sadece long")
    p.add_argument("--once", action="store_true", help="tek döngü çalıştır ve çık")
    p.add_argument("--analiz", action="store_true", help="piyasa kapalıyken bile son veriyle tek analiz (emir yok)")

    p = sub.add_parser("backtest-intraday", help="gün içi long/short stratejiyi son 60 günde test et")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--no-short", action="store_true")

    p = sub.add_parser("pattern-lab", help="tüm mum/grafik formasyonlarını istatistiksel olarak test et")
    p.add_argument("--tf", nargs="*", default=["1d", "1h", "15m", "5m"], choices=["1d", "1h", "15m", "5m"])
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--no-save", action="store_true", help="ağırlıkları bota kaydetme")
    p.add_argument("--only", nargs="*", help="sadece bu gruplar: rb (rejection block), mum, grafik veya anahtar önekleri")
    p = sub.add_parser("rb", help="Rejection Block bölgelerini bul, grafiğini çiz ve geçmiş başarısını göster")
    p.add_argument("ticker")
    p.add_argument("--tf", default="1h", choices=["1d", "1h", "15m", "5m"])
    p.add_argument("--bars", type=int, default=160, help="grafikte gösterilecek bar sayısı")
    p = sub.add_parser("learn", help="RB taktiğini geçmiş veriyle walk-forward öğren ve mükemmelleştir")
    p.add_argument("--tf", default="1h", choices=["1d", "1h", "15m", "5m"])
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--no-ml", action="store_true", help="makine öğrenmesi filtresini kapat")

    p = sub.add_parser("rb-scan", help="öğrenilmiş Rejection Block bölgelerini ve emir seviyelerini tara")
    p.add_argument("--tf", default="1d", choices=["1d", "1h", "15m", "5m"], help="zaman dilimi")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--all", action="store_true", help="tüm evreni tara (varsayılan: sadece alfa hisseler)")

    sub.add_parser("rb-backtest", help="Rejection Block alfa portföy simülasyonu ve sermaye eğrisi grafiği")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_config(args.config)
    if args.cmd == "backtest" and args.years is None:
        args.years = cfg["backtest"]["years"]
    {"scan": cmd_scan, "analyze": cmd_analyze, "backtest": cmd_backtest, "trade": cmd_trade,
     "guard": cmd_guard, "status": cmd_status, "live": cmd_live,
     "backtest-intraday": cmd_backtest_intraday, "pattern-lab": cmd_pattern_lab, "rb": cmd_rb, "learn": cmd_learn,
     "rb-scan": cmd_rb_scan, "rb-backtest": cmd_rb_backtest}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
