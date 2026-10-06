"""Görsel Mum Grafiği Kaydedici, Mum Varyasyon Analizörü ve AI Mum Kapı Bekçisi.

İşlevler:
1. Her işleme girildiğinde ve çıkıldığında 1m, 5m ve 1h zaman dilimlerinin mum grafiklerini (PNG) üretip kaydeder.
2. Mum morfolojisini (Gövde, Üst/Alt Fitil, Çekiç, Yutan Ayı/Boğa, Doji, Marubozu) matematiksel olarak analiz eder.
3. AI Mum Kapı Bekçisi (Gatekeeper): Bir Rejection Block tespit edilse dahi, eğer 1m veya 5m'de
   şiddetli bir 'Yutan Ayı' veya 'Bıçak Düşüşü (Climax Dump)' mumu iniyorsa emri durdurur.
4. Kapanan her işlem için görsel grafikli kapsamlı Adli Otopsi Raporu (Post-Mortem Report) hazırlar.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Başsız (headless) sunucu ve arka plan desteği
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import yfinance as yf

from .data import NY

log = logging.getLogger("chart_engine")


# ----------------------------------------------------------------------------- 1. Mum Morfolojisi Analizörü
def analyze_candle_formation(df: pd.DataFrame) -> dict[str, Any]:
    """Son barların mum morfolojisini ve formasyon varyasyonlarını inceler."""
    if df is None or len(df) < 3:
        return {"pattern": "YETERSIZ_VERI", "bias": "NEUTR", "desc": "Yetersiz bar sayısı"}

    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)

    last = d.iloc[-1]
    prev = d.iloc[-2]

    o, h, l, c = float(last["Open"]), float(last["High"]), float(last["Low"]), float(last["Close"])
    po, ph, pl, pc = float(prev["Open"]), float(prev["High"]), float(prev["Low"]), float(prev["Close"])

    rng = max(h - l, 0.001)
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    body_ratio = body / rng
    upper_ratio = upper_wick / rng
    lower_ratio = lower_wick / rng
    is_green = c >= o

    # Önceki mum gövdesi
    prev_rng = max(ph - pl, 0.001)
    prev_body = abs(pc - po)
    prev_green = pc >= po

    # Formasyon Tespiti:
    pattern = "STANDART"
    bias = "BULL" if is_green else "BEAR"
    desc = []

    # 1. Çekiç / Alt Fitil Reddi (Hammer / Bullish Pinbar)
    if lower_ratio >= 0.55 and upper_ratio <= 0.15:
        pattern = "CEKIC_ALICI_RETI"
        bias = "STRONG_BULL"
        desc.append(f"Güçlü Alt Fitil Reddi (%{lower_ratio*100:.0f} fitil) -> Kurumsal Alıcı Desteği")

    # 2. Kayan Yıldız / Üst Fitil Reddi (Shooting Star / Bearish Pinbar)
    elif upper_ratio >= 0.55 and lower_ratio <= 0.15:
        pattern = "KAYAN_YILDIZ_SATICI_RETI"
        bias = "STRONG_BEAR"
        desc.append(f"Şiddetli Üst Fitil Reddi (%{upper_ratio*100:.0f} fitil) -> Dirençten Satış Baskısı")

    # 3. Yutan Boğa (Bullish Engulfing)
    elif is_green and not prev_green and c > po and o < pc and body > prev_body * 1.1:
        pattern = "YUTAN_BOGA"
        bias = "STRONG_BULL"
        desc.append("Yutan Boğa Mumu -> Satıcıları tamamen yutan güçlü alım dalgası")

    # 4. Yutan Ayı (Bearish Engulfing)
    elif not is_green and prev_green and c < po and o > pc and body > prev_body * 1.1:
        pattern = "YUTAN_AYI"
        bias = "STRONG_BEAR"
        desc.append("Yutan Ayı Mumu -> Alıcıları tamamen yutan kurumsal satış dalgası")

    # 5. Marubozu / Hızlı Momentum Mumu (Climax Bar)
    elif body_ratio >= 0.80:
        if is_green:
            pattern = "MARUBOZU_BOGA"
            bias = "STRONG_BULL"
            desc.append("Gövdesi Tam Yeşil Momentum Mumu (Alıcılar Kontrolde)")
        else:
            pattern = "MARUBOZU_AYI"
            bias = "STRONG_BEAR"
            desc.append("Gövdesi Tam Kırmızı Satış Mumu (Düşen Bıçak)")

    # 6. Doji (Kararsızlık)
    elif body_ratio <= 0.12:
        pattern = "DOJI_KARARSIZLIK"
        bias = "NEUTRAL"
        desc.append("Doji Mumu -> Alıcı ve satıcı dengede, kararsızlık")

    else:
        desc.append(f"{'Yeşil' if is_green else 'Kırmızı'} Normal Gövde (Gövde: %{body_ratio*100:.0f})")

    return {
        "pattern": pattern,
        "bias": bias,
        "is_green": is_green,
        "body_ratio": round(body_ratio, 2),
        "upper_wick_ratio": round(upper_ratio, 2),
        "lower_wick_ratio": round(lower_ratio, 2),
        "desc": " | ".join(desc)
    }


# ----------------------------------------------------------------------------- 2. Görsel 3-Zaman Dilimi Mum Grafiği
def create_trade_chart_snapshot(
    ticker: str,
    trade_id: str,
    entry_px: float,
    stop_px: float,
    target_px: float,
    current_px: float | None = None,
    tag: str = "entry",
    output_dir: str = "reports/charts"
) -> tuple[Path, dict[str, Any]]:
    """1m, 5m ve 1h mum grafiklerini çizgi ve seviyelerle birleştirip PNG olarak kaydeder."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_dir / f"{ticker}_{trade_id}_{tag}.png"

    # Verileri Çek
    try:
        df_1m = yf.download(ticker, period="1d", interval="1m", progress=False).tail(35)
        df_5m = yf.download(ticker, period="1d", interval="5m", progress=False).tail(35)
        df_1h = yf.download(ticker, period="5d", interval="1h", progress=False).tail(35)
    except Exception as e:
        log.warning("Mum verisi indirilemedi: %s", e)
        return filename, {}

    # MultiIndex düzelt
    frames = []
    for f in [df_1m, df_5m, df_1h]:
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = f.columns.get_level_values(0)
        frames.append(f)
    df_1m, df_5m, df_1h = frames

    # Formasyon analizlerini topla
    patterns = {
        "1m": analyze_candle_formation(df_1m),
        "5m": analyze_candle_formation(df_5m),
        "1h": analyze_candle_formation(df_1h)
    }

    # Çizim
    fig, axes = plt.subplots(3, 1, figsize=(11, 13), facecolor="#121824")
    titles = [
        f"{ticker} [1 Dakika] - Hassas Giriş/Çıkış | {patterns['1m']['pattern']} ({patterns['1m']['desc']})",
        f"{ticker} [5 Dakika] - Mikro Trend & Likidite | {patterns['5m']['pattern']} ({patterns['5m']['desc']})",
        f"{ticker} [1 Saat]   - SMC Rejection Block Yapısı | {patterns['1h']['pattern']} ({patterns['1h']['desc']})"
    ]

    mc = mpf.make_marketcolors(up="#00c076", down="#ff4d4f", edge="inherit", wick="inherit", volume="inherit")
    s = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc, facecolor="#121824", edgecolor="#222b3c", gridcolor="#1f293d")

    curr = current_px or entry_px
    hlines = dict(
        hlines=[entry_px, stop_px, target_px, curr],
        colors=["#3b82f6", "#ef4444", "#10b981", "#f59e0b"],
        linestyle=["--", "-", "-", ":"],
        linewidths=[1.5, 1.5, 1.5, 1.2]
    )

    for ax, df, title in zip(axes, [df_1m, df_5m, df_1h], titles):
        if df.empty or len(df) < 5:
            continue
        try:
            mpf.plot(df, type="candle", ax=ax, style=s, hlines=hlines)
            ax.set_title(title, fontsize=10, fontweight="bold", color="#e2e8f0", pad=8)
            ax.tick_params(colors="#94a3b8", labelsize=8)
        except Exception as e:
            log.warning("Plot hatası: %s", e)

    # Açıklama Lejantı
    legend_text = (
        f"Mavi Kesikli: Giriş (${entry_px:.2f}) | Kırmızı: Stop (${stop_px:.2f}) | "
        f"Yeşil: Hedef (${target_px:.2f}) | Sarı Noktalı: Güncel/Çıkış (${curr:.2f})"
    )
    fig.text(0.5, 0.01, legend_text, ha="center", fontsize=10, color="#cbd5e1", fontweight="semibold")

    plt.tight_layout(rect=[0, 0.03, 1, 0.98])
    plt.savefig(filename, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info("Mum grafiği kaydedildi: %s", filename)
    return filename, patterns


# ----------------------------------------------------------------------------- 3. AI Mum Kapı Bekçisi (Gatekeeper)
def evaluate_candlestick_gatekeeper(ticker: str, side: str = "LONG") -> tuple[bool, str, dict[str, Any]]:
    """Yeni işleme girmeden önce 1m ve 5m mumlarını denetler.
    
    Eğer LONG girilecekken 5m'de devasa bir 'Yutan Ayı' veya 'Kayan Yıldız / Bıçak Düşüşü'
    varsa girişi engeller.
    """
    try:
        df_1m = yf.download(ticker, period="1d", interval="1m", progress=False).tail(15)
        df_5m = yf.download(ticker, period="1d", interval="5m", progress=False).tail(15)
    except Exception as e:
        return True, f"Veri çekilemedi, standart kontrole geçildi: {e}", {}

    for f in [df_1m, df_5m]:
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = f.columns.get_level_values(0)

    p1m = analyze_candle_formation(df_1m)
    p5m = analyze_candle_formation(df_5m)

    if side == "LONG":
        # 5m'de şiddetli düşüş mumu (düşen bıçak)
        if p5m["pattern"] in ("YUTAN_AYI", "MARUBOZU_AYI", "KAYAN_YILDIZ_SATICI_RETI"):
            return False, f"5m Mum Reddi: {p5m['desc']} -> Düşen bıçak tutulmaz, giriş engellendi!", {"1m": p1m, "5m": p5m}
        # 1m'de son 2 bar üst fitilli satış reddiyse
        if p1m["pattern"] == "KAYAN_YILDIZ_SATICI_RETI" and p1m["upper_wick_ratio"] >= 0.65:
            return False, f"1m Mum Reddi: {p1m['desc']} -> Mikro dirençten sert satış geliyor.", {"1m": p1m, "5m": p5m}
    else:
        # SHORT için tersi
        if p5m["pattern"] in ("YUTAN_BOGA", "MARUBOZU_BOGA", "CEKIC_ALICI_RETI"):
            return False, f"5m Mum Reddi: {p5m['desc']} -> Güçlü boğa momentumu var, short açılmaz!", {"1m": p1m, "5m": p5m}

    return True, f"Mum Onayı: 5m={p5m['pattern']}, 1m={p1m['pattern']} (Girişe uygun)", {"1m": p1m, "5m": p5m}


# ----------------------------------------------------------------------------- 4. Kapsamlı Adli Otopsi Raporu (Post-Mortem)
def generate_trade_postmortem_report(
    trade: dict[str, Any],
    chart_path: Path | str,
    patterns: dict[str, Any],
    output_dir: str = "reports/postmortems"
) -> Path:
    """Kapanan bir işlemin mum analiziyle birlikte görsel adli otopsi raporunu oluşturur."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"postmortem_{trade['ticker']}_{trade['trade_id']}.md"

    pnl_usd = trade.get("pnl_usd", 0.0)
    pnl_pct = trade.get("pnl_pct", 0.0)
    win = trade.get("win", False)
    status_emoji = "🟢 KÂR İLE KAPANDI" if win else "🔴 ZARARLA KAPANDI"

    md = f"""# 🔬 İşlem Adli Otopsi Raporu: {trade['ticker']} ({status_emoji})

**Tarih / Saat:** {datetime.now(NY):%Y-%m-%d %H:%M} ET  
**İşlem ID:** `{trade.get('trade_id')}`  
**Hisse:** `{trade.get('ticker')}` | **Yön:** `{trade.get('side')}` | **Miktar:** `{trade.get('qty')}` Adet  

---

### 1. Finansal Sonuç Özeti
| Metrik | Değer |
| :--- | :--- |
| **Giriş Fiyatı** | ${trade.get('entry_price', 0):.2f} |
| **Stop Fiyatı** | ${trade.get('stop_price', 0):.2f} (-%{trade.get('stop_dist_pct', 0):.2f}) |
| **Hedef Fiyatı** | ${trade.get('target_price', 0):.2f} (+%{trade.get('target_pct', 0):.2f}) |
| **Çıkış Fiyatı** | ${trade.get('exit_price', 0):.2f} |
| **Görülen En Yüksek Kâr (MFE)** | **${trade.get('mfe_usd', 0):+,.2f} (+%{trade.get('mfe_pct', 0):.2f})** |
| **Net Realize K/Z ($)** | **${pnl_usd:+,.2f}** |
| **Net Realize K/Z (%)** | **%{pnl_pct:+.2f}** |
| **Çıkış Nedeni** | `{trade.get('exit_reason')}` |

---

### 2. 1m, 5m ve 1h Zaman Dilimi Mum Grafiği
İşlem anındaki 1 dakikalık, 5 dakikalık ve 1 saatlik mum yapısı, giriş/stop/hedef çizgileriyle kaydedildi:

![Mum Grafiği]({Path(chart_path).resolve().as_uri()})

---

### 3. Mum Varyasyonları ve Morfolojik Analiz
* **1-Dakikalık Mum Durumu:** `{patterns.get('1m', {}).get('pattern', 'Bilinmiyor')}`  
  *{patterns.get('1m', {}).get('desc', '-') }*
* **5-Dakikalık Mum Durumu:** `{patterns.get('5m', {}).get('pattern', 'Bilinmiyor')}`  
  *{patterns.get('5m', {}).get('desc', '-') }*
* **1-Saatlik Mum Durumu:** `{patterns.get('1h', {}).get('pattern', 'Bilinmiyor')}`  
  *{patterns.get('1h', {}).get('desc', '-') }*

---

### 4. Neden-Sonuç ve Kök Neden Analizi (Root Cause Attribution)
"""
    if win:
        md += f"""- **Kurumsal Onay:** Rejection Block bölgesinde alıcılar tahtayı tuttu ve hedef seviyeye ulaşıldı.\n"""
        md += f"""- **Momentum:** 5 dakikalık mumlardaki boğa yapısı işlemi hedefe taşıdı.\n"""
    else:
        md += f"""- **Zararın Kök Nedeni:**\n"""
        for lesson in trade.get("lessons_learned") or []:
            md += f"""  * **{lesson}**\n"""
        if trade.get("mfe_pct", 0) >= 0.6:
            md += f"""  * **Kâr Koruma Hatası:** Pozisyon seans içinde **+${trade.get('mfe_usd', 0):.2f}** kâr gördü ancak Trailing Stop devrede olmadığı için kâr eriyerek zarara dönüştü.\n"""
        if trade.get("intraday_gap_pct", 0) >= 4.5:
            md += f"""  * **Aşırı Prim Tuzağı:** Hisse güne %{trade.get('intraday_gap_pct', 0):.1f} primle başlamıştı, tepe bölgesindeki fon kâr satışlarına yakalandı.\n"""

    md += f"""\n---\n### 5. Botun Bu İşlemden Çıkardığı Evrensel Kural\n"""
    md += f"""Bu işlemden öğrenilen kurallar `state/learned/learned_rules.json` ve `reports/trade_journal.csv` içine işlenmiş olup, **gelecekte taranacak tüm hisselere zorunlu filtre olarak uygulanacaktır**.\n"""

    report_file.write_text(md, encoding="utf-8")
    log.info("Adli otopsi raporu oluşturuldu: %s", report_file)
    return report_file
