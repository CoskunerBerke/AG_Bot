"""Konsol çıktıları (rich) ve rapor dosyaları (CSV / Markdown)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(width=None if sys.stdout.isatty() else 200)


def _color(v: float, good: float, bad: float) -> str:
    return "green" if v >= good else ("red" if v <= bad else "yellow")


def print_regime(regime: dict) -> None:
    txt = "\n".join(f"• {n}" for n in regime["notes"])
    txt += f"\nSkor çarpanı: [bold]{regime['multiplier']:.2f}[/bold]"
    console.print(Panel(txt, title="Piyasa Rejimi (NASDAQ)", border_style="cyan"))


def print_scan(results: list[dict], top: int, rm=None, equity: float | None = None) -> None:
    t = Table(title=f"NASDAQ Tarama - en iyi {top}", header_style="bold cyan", show_lines=False)
    for col in ["#", "Hisse", "Fiyat", "Skor", "Tekn.", "Haber", "Anal.", "RSI", "ATR%",
                "Mum formasyonları", "Limit", "Stop", "Hedef", "Hdf%", "R:R", "Adet", "Sinyal"]:
        t.add_column(col, justify="right" if col not in ("Hisse", "Mum formasyonları", "Sinyal") else "left")
    for i, r in enumerate(results[:top], 1):
        qty = rm.position_size(equity, r["limit"], r["stop"]) if rm and equity else ""
        sig_style = {"AL": "bold green", "İZLE": "yellow"}.get(r["signal"], "dim")
        sig = r["signal"] + (" ⚠ " + ",".join(r["flags"]) if r["flags"] else "")
        t.add_row(
            str(i), r["ticker"], f"{r['close']:.2f}",
            f"[{_color(r['score'], 60, 40)}]{r['score']:.0f}[/]", f"{r['tech']:.0f}",
            f"{r['news_pts']:+.1f}" if r["news"] else "·", f"{r['analyst_pts']:+.1f}" if r["analyst"] else "·",
            f"{r['rsi']:.0f}", f"{r['atr_pct']:.1f}", ", ".join(r["patterns"])[:38] or "-",
            f"{r['limit']:.2f}", f"{r['stop']:.2f}", f"{r['target']:.2f}", f"{r['target_pct']:.1f}",
            f"{r['rr']:.1f}", str(qty), f"[{sig_style}]{sig}[/]",
        )
    console.print(t)


def save_scan(results: list[dict], regime: dict, reports_dir: str) -> Path:
    d = Path(reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    rows = []
    for r in results:
        rows.append({
            "ticker": r["ticker"], "date": r["date"], "close": r["close"], "score": round(r["score"], 1),
            "tech": round(r["tech"], 1), "news_pts": round(r["news_pts"], 2), "analyst_pts": round(r["analyst_pts"], 2),
            "rsi": round(r["rsi"], 1), "atr_pct": round(r["atr_pct"], 2), "patterns": "; ".join(r["patterns"]),
            "limit": round(r["limit"], 2), "stop": round(r["stop"], 2), "target": round(r["target"], 2),
            "target_pct": round(r["target_pct"], 2), "rr": round(r["rr"], 2), "signal": r["signal"],
            "flags": "; ".join(r["flags"]), **{k: round(v, 1) for k, v in r["components"].items()},
        })
    path = d / f"scan_{stamp}.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    md = [f"# NASDAQ Tarama Raporu - {datetime.now():%d.%m.%Y %H:%M}", "", "## Piyasa rejimi"]
    md += [f"- {n}" for n in regime["notes"]] + ["", "## AL sinyalleri", ""]
    buys = [r for r in results if r["signal"] == "AL"]
    if not buys:
        md.append("_Bugün kriterleri karşılayan AL sinyali yok. İşlem yapmamak da bir stratejidir._")
    for r in buys:
        md.append(f"### {r['ticker']} - skor {r['score']:.0f}")
        md.append(f"- Limit giriş **{r['limit']:.2f}**, stop **{r['stop']:.2f}** (-%{r['stop_pct']:.1f}), "
                  f"hedef **{r['target']:.2f}** (+%{r['target_pct']:.1f}), R:R {r['rr']:.1f}")
        md.append(f"- Mumlar: {', '.join(r['patterns']) or '-'} | RSI {r['rsi']:.0f} | ATR %{r['atr_pct']:.1f}")
        if r["analyst"]:
            a = r["analyst"]
            md.append(f"- Analist: {a.get('rec_key') or '-'} (ort. {a.get('rec_mean') or '-'}, {a.get('n_analysts') or 0} analist), "
                      f"hedef fiyat potansiyeli %{a['upside_pct']:.1f}" if a.get("upside_pct") is not None else
                      f"- Analist: {a.get('rec_key') or '-'}")
        if r["news"] and r["news"]["headlines"]:
            md.append("- Son haberler:")
            md += [f"  - ({h['sentiment']:+.2f}) {h['title']} — _{h['source']}_" for h in r["news"]["headlines"][:4]]
        md.append("")
    (d / f"scan_{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    return path


def print_analysis(t: str, d: pd.DataFrame, pats_last: list[list[str]], stats: pd.DataFrame,
                   news: dict, an: dict, plan: dict, score_parts: dict) -> None:
    last = d.iloc[-1]
    ind = Table(title=f"{t} - {an.get('name', t)} ({d.index[-1].date()})", header_style="bold cyan")
    ind.add_column("Gösterge")
    ind.add_column("Değer", justify="right")
    ind.add_column("Yorum")
    c = last["Close"]
    rows = [
        ("Kapanış", f"{c:.2f}", ""),
        ("EMA20 / EMA50", f"{last['ema20']:.2f} / {last['ema50']:.2f}", "yükseliş" if last["ema20"] > last["ema50"] else "düşüş"),
        ("SMA200", f"{last['sma200']:.2f}", "fiyat üstünde" if c > last["sma200"] else "fiyat altında"),
        ("RSI(14)", f"{last['rsi']:.1f}", "aşırı alım" if last["rsi"] > 70 else "aşırı satım" if last["rsi"] < 30 else "nötr"),
        ("MACD hist.", f"{last['macd_hist']:.3f}", "pozitif" if last["macd_hist"] > 0 else "negatif"),
        ("ADX / +DI / -DI", f"{last['adx']:.1f} / {last['plus_di']:.1f} / {last['minus_di']:.1f}",
         "güçlü trend" if last["adx"] > 25 else "zayıf trend"),
        ("Bollinger %B", f"{last['bb_pctb']:.2f}", ""),
        ("Stokastik K/D", f"{last['stoch_k']:.0f} / {last['stoch_d']:.0f}", ""),
        ("ATR / ATR%", f"{last['atr']:.2f} / %{last['atr_pct']:.2f}", "%1 hedef için uygun" if last["atr_pct"] >= 1.2 else "oynaklık düşük"),
        ("Göreli hacim", f"{last['rel_vol']:.2f}x", ""),
        ("20g destek / direnç", f"{last['low20']:.2f} / {last['high20']:.2f}", ""),
    ]
    for r in rows:
        ind.add_row(*r)
    console.print(ind)

    sp = "  ".join(f"{k.replace('sc_', '')}: {v:+.0f}" for k, v in score_parts.items())
    console.print(Panel(sp, title="Skor bileşenleri", border_style="magenta"))

    recent = [f"{d.index[-5 + i].date()}: {', '.join(p) or '-'}" for i, p in enumerate(pats_last)]
    console.print(Panel("\n".join(recent), title="Son 5 günün mum formasyonları", border_style="yellow"))

    st = Table(title="Bu hissede formasyonların GEÇMİŞ başarısı (sonraki 3 gün)", header_style="bold cyan")
    for col in stats.columns:
        st.add_column(col, justify="right" if col not in ("Formasyon", "Yön") else "left")
    for _, r in stats.iterrows():
        st.add_row(r["Formasyon"], r["Yön"], str(r["Adet"]), f"{r['Hedef%']:.0f}", f"{r['Kazanma%']:.0f}", f"{r['OrtGetiri%']:+.2f}")
    console.print(st)
    console.print("[dim]Hedef% = 3 gün içinde fiyatın en az %1 yükselme oranı. Baz orandan belirgin yüksek değilse formasyon bu hissede güvenilir değildir; az örnekli (<10) satırlara itibar etmeyin.[/dim]")

    a = an
    at = (f"Tavsiye: {a.get('rec_key') or '-'} (ort. {a.get('rec_mean') or '-'}/5, {a.get('n_analysts') or 0} analist)\n"
          f"Ort. hedef fiyat: {a.get('target_mean') or '-'}  potansiyel: "
          f"{'%' + format(a['upside_pct'], '.1f') if a.get('upside_pct') is not None else '-'}\n"
          f"Son 30 gün not artırım/indirim: {a['ups_30d']} / {a['downs_30d']}\n"
          f"Bilanço tarihi: {a.get('earnings_date') or '-'}" + ("  [bold red]⚠ YAKIN![/]" if a["earnings_soon"] else ""))
    console.print(Panel(at, title=f"Analist görüşleri (puan {a['score']:+.1f})", border_style="blue"))

    nt = Table(title=f"Haberler (duyarlılık {news['score']:+.2f}, {news['count']} haber)", header_style="bold cyan")
    nt.add_column("Saat önce", justify="right")
    nt.add_column("Duyg.", justify="right")
    nt.add_column("Başlık")
    nt.add_column("Kaynak")
    for h in news["headlines"]:
        nt.add_row(f"{h['age_h']:.0f}", f"[{_color(h['sentiment'], 0.2, -0.2)}]{h['sentiment']:+.2f}[/]", h["title"][:90], str(h["source"])[:20])
    console.print(nt)

    console.print(Panel(
        f"Limit giriş: {plan['limit']:.2f} | Stop: {plan['stop']:.2f} (-%{plan['stop_pct']:.2f}) | "
        f"Hedef: {plan['target']:.2f} (+%{plan['target_pct']:.2f}) | R:R {plan['rr']:.2f}\n"
        f"Nihai skor: [bold]{plan['score']:.0f}[/] -> [bold]{plan['signal']}[/]",
        title="İşlem planı", border_style="green"))


def print_backtest(res: dict) -> None:
    t = Table(title="Backtest sonuçları (yalnızca teknik + rejim; haber/analist hariç)", header_style="bold cyan")
    t.add_column("Metrik")
    t.add_column("Değer", justify="right")
    for k, v in res["stats"].items():
        t.add_row(k, f"{v:,.2f}" if isinstance(v, float) else str(v))
    console.print(t)


def save_backtest(res: dict, reports_dir: str) -> Path:
    d = Path(reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    res["trades"].to_csv(d / f"backtest_trades_{stamp}.csv", index=False, encoding="utf-8-sig")
    eq = res["equity"].to_frame()
    if res["benchmark"] is not None:
        eq["QQQ"] = res["benchmark"]
    eq.to_csv(d / f"backtest_equity_{stamp}.csv", encoding="utf-8-sig")
    return d
