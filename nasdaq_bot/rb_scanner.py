"""Rejection Block Tarama, Sinyal ve Emir Üretim Modülü.

Öğrenilmiş Rejection Block parametrelerini (state/learned/rb_1d.json ve rb_alpha_tickers.json)
kullanarak güncel piyasada en yüksek olasılıklı SMC / Rejection Block işlemlerini tespit eder.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rich.table import Table

from .data import NY, download_ohlcv, drop_partial_bar
from .indicators import add_indicators
from .intraday import session_only
from .pattern_lab import TIMEFRAMES
from .rejection_blocks import find_rejection_blocks
from .report import console
from .risk import RiskManager
from .strategy import get_universe, load_data

log = logging.getLogger(__name__)


def load_alpha_profile(state_dir: str = "state") -> dict[str, Any]:
    """Geçmiş Walk-Forward OOS testlerinde kanıtlanmış alfa hisse profilini yükler."""
    p = Path(state_dir) / "learned" / "rb_alpha_tickers.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Alpha tickers yüklenemedi: %s", e)
    return {"tickers": {}, "portfolio_stats": {}}


def load_learned_cfg(tf: str = "1d", state_dir: str = "state") -> dict[str, Any]:
    """Öğrenilmiş optimal RB kuralını yükler."""
    p = Path(state_dir) / "learned" / f"rb_{tf}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Learned RB config yüklenemedi: %s", e)
    # Varsayılan güvenli parametreler (1D için öğrenilen kural)
    return {
        "cfg": {
            "depart": 0.5,
            "mode": "close",
            "rr": 3.0,
            "buf": 0.25,
            "horizon": 20,
            "filters": {"wick": 0.3, "trend": "any", "sweep": "any", "side": "long", "tick": "any"}
        },
        "aciklama": "varsayılan kural: ret kapanışı, stop fitil ucu ± 0.25 ATR, hedef 3R"
    }


def scan_rb_setups(
    cfg: dict,
    tf: str = "1d",
    tickers: list[str] | None = None,
    only_alpha: bool = False,
    progress=None
) -> list[dict[str, Any]]:
    """Tüm evreni veya seçilen hisseleri RB taktiğine göre tarar.
    
    Dönüş: Her aktif veya tetiklenen sinyal için emir planı ve istatistiksel kalite bilgisi.
    """
    alpha_profile = load_alpha_profile(cfg.get("state_dir", "state"))
    alpha_map = alpha_profile.get("tickers", {})
    learned = load_learned_cfg(tf, cfg.get("state_dir", "state"))
    lcfg = learned.get("cfg", {})
    target_rr = float(lcfg.get("rr", 3.0))
    buf_atr = float(lcfg.get("buf", 0.25))

    if tickers:
        scan_tickers = [t.upper() for t in tickers]
    elif only_alpha and alpha_map:
        scan_tickers = list(alpha_map.keys())
    else:
        scan_tickers = get_universe(cfg)

    spec = TIMEFRAMES.get(tf, TIMEFRAMES["1d"])
    dc = cfg["data"]
    data = download_ohlcv(
        scan_tickers,
        period=spec["period"],
        interval=spec["interval"],
        cache_dir=dc["cache_dir"],
        ttl_minutes=dc["cache_ttl_minutes"]
    )

    setups: list[dict[str, Any]] = []

    for t in scan_tickers:
        df = data.get(t)
        if df is None or len(df) < 60:
            continue
        if spec["intraday"]:
            df = session_only(df)
        elif not dc.get("use_partial_bar", False):
            df = drop_partial_bar(df)

        try:
            d = add_indicators(df)
            sigs, stops, zones = find_rejection_blocks(d)
        except Exception as e:
            log.warning("%s RB analizi başarısız: %s", t, e)
            continue

        if progress:
            progress(t)

        last_px = float(d["Close"].iloc[-1])
        last_atr = float(d["atr"].iloc[-1])
        if last_atr <= 0 or not np.isfinite(last_atr):
            continue

        # Henüz kapanmamış (aktif) bölgeleri incele
        active_zones = [z for z in zones if z.end is None]
        if not active_zones:
            continue

        alpha_info = alpha_map.get(t)
        is_alpha = alpha_info is not None

        for z in active_zones:
            # Öğrenilmiş filtre: Eğer sadece long ise side < 0 atla
            side_req = lcfg.get("filters", {}).get("side", "any")
            if side_req == "long" and z.side < 0:
                continue
            if side_req == "short" and z.side > 0:
                continue

            dist_atr = (z.edge - last_px) / last_atr if z.side > 0 else (last_px - z.edge) / last_atr
            yon_str = "LONG" if z.side > 0 else "SHORT"

            # Yapısal Stop seviyesi (fitil ucu ± buf * ATR)
            stop_px = z.extreme - z.side * buf_atr * last_atr
            
            # Tetiklenme durumu:
            # 1) 'TETİKLENDİ' -> son bar veya önceki barda ret gerçekleşmiş
            # 2) 'BÖLGEDE' -> fiyat tam blok içinde (fitil bölgesi test ediliyor)
            # 3) 'YAKLAŞIYOR' -> fiyata 1 ATR'den yakın, bekleyen limit emir için uygun
            status = z.status
            triggered = False
            last_idx = len(d) - 1

            if z.signal is not None and (last_idx - z.signal) <= 2:
                durum = "TETİKLENDİ (Ret Kapanışı)"
                triggered = True
                entry_px = last_px
            elif z.touched is not None and (last_idx - z.touched) <= 3:
                durum = "BÖLGEDE (Test Ediliyor)"
                triggered = True
                entry_px = last_px
            elif abs(dist_atr) <= 1.0:
                durum = "YAKLAŞIYOR (Limit Fırsatı)"
                entry_px = z.edge  # Limit girişi tam kenardan
            else:
                durum = "İZLEMEDE"
                entry_px = z.edge

            # Risk & Hedef hesabı
            risk_unit = z.side * (entry_px - stop_px)
            if risk_unit <= 0:
                continue

            # Meta-Öğrenme Filtresi 1: Aşırı Geniş Stop Koruması (Disiplinli Risk Kontrolü)
            # Eğer yapısal fitil stopu %3.0'ten derindeyse, stopu %2.5 ile sıkılaştırarak sermayeyi koru
            raw_risk_pct = (risk_unit / entry_px) * 100
            if raw_risk_pct > 3.0:
                stop_px = entry_px - z.side * (entry_px * 0.025)
                risk_unit = z.side * (entry_px - stop_px)

            target_px = entry_px + z.side * target_rr * risk_unit
            risk_pct = (risk_unit / entry_px) * 100
            target_pct = (abs(target_px - entry_px) / entry_px) * 100

            # Meta-Öğrenme Filtresi 2: Gün İçi Aşırı Prim / Tepe Tuzağı Filtresi
            # Güne zaten %4.5'ten fazla fırlamış hisselerde tepeden alım yapılmasını engelle (Her hisseye uygulanır)
            first_open = float(d["Open"].iloc[0]) if len(d) > 0 else entry_px
            day_runup_pct = ((entry_px / first_open) - 1.0) * 100 if first_open > 0 else 0.0
            if z.side > 0 and day_runup_pct > 4.5:
                continue

            # 1. Akademik Hacim Doğrulaması (Volume Anomaly / Institutional Absorption)
            vol_col = d["Volume"] if "Volume" in d.columns else None
            vol_confirmed = False
            rel_vol = 1.0
            if vol_col is not None and len(d) > 25:
                vol_sma20 = vol_col.rolling(20, min_periods=5).mean()
                p_idx = min(z.pivot, len(vol_col) - 1)
                mean_vol = vol_sma20.iloc[p_idx]
                if mean_vol and mean_vol > 0:
                    rel_vol = float(vol_col.iloc[p_idx] / mean_vol)
                    vol_confirmed = rel_vol >= 1.20

            # 2. Akademik Gün İçi Oynaklık U-Eğrisi (Time Seasonality)
            in_optimal_window = True
            current_bar_time = d.index[-1]
            if hasattr(current_bar_time, "hour"):
                h = current_bar_time.hour
                # New York seansında 11:30 - 13:30 (TSİ 18:30 - 20:30) düşük hacimli tuzak periyodudur
                if h in (12, 13):
                    in_optimal_window = False

            is_trend = (
                (last_px > d["sma50"].iloc[-1] and d["sma50"].iloc[-1] > d["sma50"].iloc[-5])
                if z.side > 0 and len(d) >= 5 and "sma50" in d.columns
                else (last_px < d["sma50"].iloc[-1] and d["sma50"].iloc[-1] < d["sma50"].iloc[-5])
                if z.side < 0 and len(d) >= 5 and "sma50" in d.columns
                else False
            )

            # Skorlama: Alfa hissesi + SMC Likidite Süpürmesi + Hacim Anomalisi + Tetiklenme
            score = 50.0
            if is_alpha:
                score += 20.0
                score += min(10.0, alpha_info.get("sum_r", 0) / 2.0)
                score += (alpha_info.get("win_rate_pct", 30) - 30) * 0.5
            if z.sweep:
                score += 15.0  # Kurumsal likidite süpürmesi (SMC Sweep)
            if vol_confirmed:
                score += 15.0  # Kurumsal hacim anomalisi
            if triggered:
                score += 15.0
            elif "YAKLAŞ" in durum:
                score += 10.0
            elif "İZLE" in durum:
                score -= 20.0  # Fiyattan çok uzak pasif seviyeleri geriye at
            if is_trend:
                score += 5.0
            if not in_optimal_window:
                score -= 10.0  # Öğle yatay piyasa cezası

            # 3. Kullanıcı Teyit Taktikleri (Confirmation Candles & Chart Patterns)
            from .tactic_tracker import TacticTracker
            tt = TacticTracker(cfg.get("state_dir", "state"))
            active_tacs = tt.extract_active_tactics(d, recent_bars=2)
            matching_tacs = [tac for tac in active_tacs if (tac["direction"] == "BULL" and z.side > 0) or (tac["direction"] == "BEAR" and z.side < 0)]
            opposing_tacs = [tac for tac in active_tacs if (tac["direction"] == "BEAR" and z.side > 0) or (tac["direction"] == "BULL" and z.side < 0)]

            tactic_names = []
            tactic_ids = []
            for tac in matching_tacs:
                score += 12.0 * tac["multiplier"]
                tactic_names.append(tac["name"])
                tactic_ids.append(tac["id"])

            for tac in opposing_tacs:
                score -= 15.0
                tactic_names.append(f"ZIT: {tac['name']}")

            score = float(np.clip(score, 0, 100))

            setups.append({
                "ticker": t,
                "tf": tf,
                "yon": yon_str,
                "durum": durum,
                "triggered": triggered,
                "is_alpha": is_alpha,
                "alpha_rank": alpha_info.get("rank") if is_alpha else 999,
                "alpha_win": alpha_info.get("win_rate_pct") if is_alpha else None,
                "alpha_pf": alpha_info.get("profit_factor") if is_alpha else None,
                "alpha_r": alpha_info.get("sum_r") if is_alpha else None,
                "close": last_px,
                "entry": entry_px,
                "stop": stop_px,
                "target": target_px,
                "risk_pct": risk_pct,
                "target_pct": target_pct,
                "rr": target_rr,
                "sweep": z.sweep,
                "rel_vol": round(rel_vol, 2),
                "vol_confirmed": vol_confirmed,
                "trend": is_trend,
                "dist_atr": dist_atr,
                "zone_range": f"{z.bot:.2f} – {z.top:.2f}",
                "score": score,
                "pivot_date": d.index[z.pivot].strftime("%Y-%m-%d"),
                "tactics": tactic_names,
                "tactic_ids": tactic_ids,
            })

    # Sıralama: Önce tetiklenenler ve alfa hisseleri, ardından toplam skor
    setups.sort(key=lambda s: (s["triggered"], s["is_alpha"], s["score"]), reverse=True)
    return setups


def print_rb_table(setups: list[dict[str, Any]], rm: RiskManager, equity: float, top_n: int = 15):
    """Bulunan Rejection Block sinyallerini formatlı bir Rich tablosunda gösterir."""
    if not setups:
        console.print("[yellow]Şu anda kriterleri karşılayan aktif Rejection Block bölgesi bulunamadı.[/]")
        return

    table = Table(
        title=f"Rejection Block (Ret Bloğu) Sinyalleri & Emir Planı (Özsermaye: ${equity:,.2f})",
        header_style="bold cyan"
    )
    table.add_column("Hisse", style="bold")
    table.add_column("Alfa", justify="center")
    table.add_column("Yön", justify="center")
    table.add_column("Durum", justify="left")
    table.add_column("Bölge", justify="right")
    table.add_column("Fiyat", justify="right")
    table.add_column("Giriş", justify="right")
    table.add_column("Yapısal Stop", justify="right")
    table.add_column("Hedef (3R)", justify="right")
    table.add_column("Risk%", justify="right")
    table.add_column("Lot", justify="right")
    table.add_column("Poz. Tutar", justify="right")
    table.add_column("Skor", justify="right")

    for s in setups[:top_n]:
        # Pozisyon büyüklüğü hesapla (Risk yönetimi kurallarına göre)
        cash_avail = equity * 0.95
        qty = rm.position_size(equity, s["entry"], s["stop"], cash_avail)
        pos_val = qty * s["entry"]

        yon_style = "[green]LONG[/]" if s["yon"] == "LONG" else "[red]SHORT[/]"
        durum_style = (
            f"[bold green]{s['durum']}[/]" if "TETİKLENDİ" in s["durum"]
            else f"[yellow]{s['durum']}[/]" if "BÖLGEDE" in s["durum"]
            else f"[cyan]{s['durum']}[/]"
        )
        alpha_badge = (
            f"[bold gold1]*#{s['alpha_rank']}[/] [dim](PF {s['alpha_pf']})[/]"
            if s["is_alpha"] else "[dim]-[/]"
        )

        table.add_row(
            s["ticker"],
            alpha_badge,
            yon_style,
            durum_style,
            s["zone_range"],
            f"${s['close']:.2f}",
            f"${s['entry']:.2f}",
            f"${s['stop']:.2f}",
            f"${s['target']:.2f} [dim](+{s['target_pct']:.1f}%)[/]",
            f"%{s['risk_pct']:.2f}",
            str(qty) if qty > 0 else "[red]0[/]",
            f"${pos_val:,.0f}" if qty > 0 else "-",
            f"{s['score']:.0f}"
        )

    console.print(table)


def save_rb_scan(setups: list[dict[str, Any]], reports_dir: str = "reports") -> Path:
    """Tarama sonuçlarını CSV ve Markdown raporu olarak kaydeder."""
    out = Path(reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    
    csv_path = out / f"rb_scan_{stamp}.csv"
    md_path = out / f"rb_scan_{stamp}.md"

    df = pd.DataFrame(setups)
    if not df.empty:
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        lines = [
            f"# Rejection Block Tarama Raporu - {datetime.now(NY):%Y-%m-%d %H:%M} ET\n",
            f"Toplam bulunan setup sayısı: **{len(setups)}**\n",
            "| Hisse | Alfa | Yön | Durum | Giriş | Stop | Hedef (3R) | Risk% | Skor |",
            "| :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |",
        ]
        for s in setups[:25]:
            alpha = f"* #{s['alpha_rank']} (PF {s['alpha_pf']})" if s['is_alpha'] else "-"
            lines.append(
                f"| **{s['ticker']}** | {alpha} | {s['yon']} | {s['durum']} | ${s['entry']:.2f} | "
                f"${s['stop']:.2f} | ${s['target']:.2f} (+{s['target_pct']:.1f}%) | %{s['risk_pct']:.2f} | {s['score']:.0f} |"
            )
        md_path.write_text("\n".join(lines), encoding="utf-8")

    return md_path
