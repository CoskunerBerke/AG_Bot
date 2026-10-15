"""Kaanermi (DMC) v2 yerel işlem botu — Kaan'ın 10AM, Rejection Block, Kaan, Lunch ve Box modelleri, Alpaca paper.

Kurallar ve kaynaklar: knowledge/ (STRATEGY_SPEC.md, notes/, fixtures/).

    python main.py dmc-backtest --start 2025-10-01 [--set symbol=NQ.v.0]
    python main.py dmc-scan --date 2026-10-07          # bir günü bot gözüyle yeniden oynat
    python main.py dmc-fidelity --set symbol=NQ.v.0    # botun kararları vs Kaan'ın gerçek işlemleri
    python main.py dmc-run [--dry-run]                 # yerel canlı yürütücü (Alpaca paper)
    python main.py tg-login | tg-sync                  # Kaan'ın Telegram işlem özetlerini topla
    python main.py status                              # Alpaca hesap / pozisyonlar
    python main.py panel                               # yerel izleme paneli: http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd
from rich.console import Console

from nasdaq_bot.common import NY
from nasdaq_bot.config import load_config

console = Console()
ROOT = Path(__file__).resolve().parent


def _merge(base: dict, over: dict) -> None:
    """İç içe sözlükleri birleştir (over kazanır)."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def _apply_overrides(cfg, sets):
    """--set setups.ten_am.entry_mode=confirm → cfg['dmc'] içinde nokta yolu."""
    import yaml
    for kv in sets or []:
        k, v = kv.split("=", 1)
        node = cfg["dmc"]
        *path, last = k.split(".")
        for p in path:
            node = node[p]
        node[last] = yaml.safe_load(v)


def cmd_dmc_backtest(cfg, args):
    from nasdaq_bot.dmc.backtest import run_backtest, stats
    from nasdaq_bot.dmc.data import load_history
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else datetime.now(NY).date() - timedelta(days=1)
    df = load_history(cfg, cfg["dmc"]["symbol"], start - timedelta(days=40), end)
    tr = run_backtest(cfg, df, start, end)
    reports_dir = Path(cfg["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"dmc_backtest_{datetime.now(NY):%Y%m%d_%H%M}.csv"
    tr.to_csv(out, index=False)
    fmt = lambda s: "  ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in s.items())
    console.print(f"[bold]DMC backtest {start} → {end}[/]  ({cfg['dmc']['symbol']})")
    console.print("TÜMÜ  " + fmt(stats(tr, cfg)))
    if len(tr):
        for k, g in tr.groupby(["setup", "side"]):
            console.print(f"{k[0]:>7} {k[1]:<5} " + fmt(stats(g, cfg)))
    console.print(f"işlemler: {out}")


def cmd_dmc_scan(cfg, args):
    from nasdaq_bot.dmc.backtest import PEER_MAP, simulate_day
    from nasdaq_bot.dmc.data import daily_bars, load_history
    from nasdaq_bot.dmc.engine import DayEngine
    from nasdaq_bot.dmc.levels import am_range_median, am_ranges, prepare_day
    sym = cfg["dmc"]["symbol"]
    day = date.fromisoformat(args.date) if args.date else datetime.now(NY).date() - timedelta(days=1)
    df = load_history(cfg, sym, day - timedelta(days=40), day)
    t = df.index.time
    bars = df[(df.index.date == day) & (t >= dtime(9, 30)) & (t < dtime(16, 0))]
    if bars.empty:
        console.print(f"{day} için bar yok (tatil/hafta sonu ya da veri yok)")
        return
    pm = df[(df.index.date == day) & (t < dtime(9, 30))]
    lv = prepare_day(df[df.index < pd.Timestamp(datetime.combine(day, dtime(9, 30)))], day,
                     daily=daily_bars(df[df.index < pd.Timestamp(day)], sym), fvg_cfg=cfg["dmc"].get("fvg"))
    console.print(f"[bold]{day} {sym}[/] bias={lv.bias} PDH={lv.pdh} PDL={lv.pdl} PMH={lv.pmh} PML={lv.pml} "
                  f"TDO={lv.tdo} gece_yarısı={lv.mid_open} FVG={len(lv.fvgs)} RB={len(lv.rbs)} ATR1={lv.atr1}")
    peer_sym = PEER_MAP.get(sym)
    peer_bars = pd.DataFrame()
    if peer_sym:
        try:
            pdf = load_history(cfg, peer_sym, day - timedelta(days=5), day)
            pt = pdf.index.time
            peer_bars = pdf[(pdf.index.date == day) & (pt >= dtime(9, 30)) & (pt < dtime(16, 0))]
        except Exception:
            pass
    eng = DayEngine(cfg, lv, {"am_range_median": am_range_median(am_ranges(df), day), "peer": peer_bars})
    if len(pm):
        eng.set_premarket(pm)
    orig = eng.on_bar

    def traced(ts, *a):
        out = orig(ts, *a)
        for it in out:
            console.print(f"  {ts:%H:%M} {it.kind:<6} {it.side:<5} {it.tag:<22} giriş {it.entry:.2f} stop {it.stop:.2f} "
                          f"tp1 {it.tp1 if it.tp1 is None else round(it.tp1, 2)} tp2 {it.tp2:.2f} {it.targets} {it.confluence}")
        return out
    eng.on_bar = traced
    for tr in simulate_day(cfg, bars, eng):
        console.print(f"[bold]İŞLEM[/] {tr.setup} {tr.side} {tr.filled_at:%H:%M} giriş {tr.entry:.2f} stop {tr.stop:.2f} "
                      f"→ R={tr.r:+.2f}  {[(f'{e[0]:%H:%M}', round(e[1], 2), e[3]) for e in tr.exits]}")
    console.print(f"ret/izleme sebepleri: {eng.reasons()}")


def cmd_dmc_fidelity(cfg, args):
    from nasdaq_bot.dmc.fidelity import run_all, score
    res = run_all(cfg, ROOT)
    for r in res:
        console.print(f"{r.day} {r.day:%a}  beklenen={r.expect:<5} {r.exp_setup:<8} bot=({r.bot})  → [bold]{r.verdict}[/]")
        if args.verbose_reasons and r.reasons:
            console.print(f"      sebepler: {r.reasons}")
    console.print(score(res))


def cmd_dmc_run(cfg, args):
    from logging.handlers import TimedRotatingFileHandler
    from nasdaq_bot.broker import AlpacaBroker
    from nasdaq_bot.dmc.live import run_multi
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    if args.name:                                # ikinci bot (ayrı Alpaca hesabı): ayrı durum klasörü ve log
        cfg["state_dir"] = str(Path(cfg["state_dir"]) / args.name)
        Path(cfg["state_dir"]).mkdir(parents=True, exist_ok=True)
        overlay = ROOT / f"config.{args.name}.yaml"  # deneme ayarları: config.yaml'ın üzerine yazılır
        if overlay.exists():
            import yaml
            _merge(cfg, yaml.safe_load(overlay.read_text(encoding="utf-8")) or {})
            _apply_overrides(cfg, getattr(args, "set", None))
    cfg["bot_name"] = args.name or "a"
    log_name = f"dmc_live_{args.name}.log" if args.name else "dmc_live.log"
    fh = TimedRotatingFileHandler(logs / log_name, when="midnight", backupCount=30, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(fh)
    root.setLevel(logging.INFO)
    from nasdaq_bot.notify import TelegramErrorHandler
    root.addHandler(TelegramErrorHandler(cfg["bot_name"]))
    broker = AlpacaBroker.from_env(cfg)
    if not args.dry_run and "paper-api" not in broker.base and not args.live_onay:
        raise SystemExit(f"GERÇEK HESAP ({broker.base}) — --live-onay olmadan çalışmaz.")
    root.info("bot %s, izlemedeki modeller: %s", args.name or "a", cfg["dmc"].get("watch_setups") or "yok")
    run_multi(cfg, broker, dry_run=args.dry_run)


def cmd_tg_login(cfg, args):
    from nasdaq_bot.dmc.telegram_reader import login
    login(cfg)


def cmd_tg_sync(cfg, args):
    from nasdaq_bot.dmc.telegram_reader import sync
    console.print(sync(cfg, args.limit))


def cmd_status(cfg, args):
    from nasdaq_bot.broker import AlpacaBroker
    b = AlpacaBroker.from_env(cfg)
    a = b.account()
    console.print(f"{b.base} | {a['status']} | equity {a['equity']} | cash {a['cash']} | buying power {a['buying_power']}")
    for p in b.positions():
        console.print(f"  {p['symbol']} {p['side']} {p['qty']} @ {p['avg_entry_price']} → {p['current_price']}  "
                      f"PnL {p['unrealized_pl']}")
    console.print(f"açık emir: {len(b.open_orders())}")


def cmd_panel(cfg, args):
    from nasdaq_bot.panel import serve
    serve(cfg, args.port)


def main():
    ap = argparse.ArgumentParser(description="Kaanermi (DMC) v2 yerel işlem botu")
    ap.add_argument("--config", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dmc-backtest", help="geçmiş veride test")
    p.add_argument("--start", default="2025-10-01")
    p.add_argument("--end", default=None)
    p.add_argument("--set", nargs="*", help="dmc ayarı geçersiz kıl: symbol=NQ.v.0 setups.ten_am.entry_mode=confirm")
    p = sub.add_parser("dmc-scan", help="bir günü bot gözüyle yeniden oynat")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (varsayılan: dün)")
    p.add_argument("--set", nargs="*")
    p = sub.add_parser("dmc-fidelity", help="botun kararlarını Kaan'ın tarihli işlemleriyle karşılaştır")
    p.add_argument("--set", nargs="*")
    p.add_argument("--verbose-reasons", action="store_true")
    for run_cmd in ("dmc-run", "dmc-live"):
        p = sub.add_parser(run_cmd, help="canlı yürütücü (Alpaca paper, sürekli)")
        p.add_argument("-v", "--verbose", action="store_true")
        p.add_argument("--dry-run", action="store_true", help="emir göndermeden sadece logla")
        p.add_argument("--live-onay", action="store_true", help="gerçek para hesabı için açık onay")
        p.add_argument("--name", default="", help="ikinci bot adı (ör. b): state/<ad>/ ve logs/dmc_live_<ad>.log")
        p.add_argument("--set", nargs="*")
    sub.add_parser("tg-login", help="Telegram'a bir kez giriş (telefon + kod)")
    p = sub.add_parser("tg-sync", help="Kaan'ın Bing-Bongs işlem özetlerini indir")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--set", nargs="*")
    sub.add_parser("status", help="Alpaca hesap durumu")
    p = sub.add_parser("panel", help="yerel izleme paneli (hesap, emirler, yerel/sunucu logu)")
    p.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    _apply_overrides(cfg, getattr(args, "set", None))
    {"dmc-backtest": cmd_dmc_backtest, "dmc-scan": cmd_dmc_scan, "dmc-fidelity": cmd_dmc_fidelity,
     "dmc-run": cmd_dmc_run, "dmc-live": cmd_dmc_run, "tg-login": cmd_tg_login, "tg-sync": cmd_tg_sync,
     "status": cmd_status, "panel": cmd_panel}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
