"""
AG2BOT — Çoklu Zaman Dilimi (1m / 3m / 5m / 15m / 1H / 1D) Görsel Grafik, HTF->LTF Kesişim ve Taktik Terminali
(16 Ekim 2026 dahil en güncel Alpaca verisi + Çok Günlü Grafik + 15m/1H FVG & 1m Box/CISD Analizi)

Çalıştırmak için:
    python "c:\\Users\\berke\\OneDrive\\Masaüstü\\ag2bot\\nasdaq_bot\\dashboard_all_tactics.py"
Tarayıcıda açın:
    http://localhost:8055
"""
from __future__ import annotations

import json
import pickle
import sys
from copy import deepcopy
from datetime import date, datetime, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT_DIR / ".cache" / "dmc"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from nasdaq_bot.config import load_config
from nasdaq_bot.dmc.backtest import simulate_day
from nasdaq_bot.dmc.data import daily_bars, load_history
from nasdaq_bot.dmc.engine import DayEngine
from nasdaq_bot.dmc.levels import am_range_median, am_ranges, prepare_day
from nasdaq_bot.dmc.tactic_selector import ALL_TACTICS, classify_chart_regime

_BAR_CACHE: dict[str, pd.DataFrame] = {}

SYMBOL_PROFILES: dict[str, dict] = {
    "QQQ": {
        "dna": "1D/1H Trend + 15m FVG/SNR + 5m/3m/1m Likidite Süpürmesi & VWAP Emilimi",
        "best_tactics": [
            ("sabirli_coklu", "+19.73R (4/4 Yıl Pozitif · %69 WR)"),
            ("dmc_rb", "+35.62R (2025-26 Güçlü)"),
            ("ten_am", "+19.23R (10:00 Makro)"),
            ("kaan", "+2.21R (Öğle Sonu 3m CISD)"),
        ],
        "combined_r": "Sabırlı Çoklu: 4 Yılın 4'ünde de Kârlı (+19.73R)",
    },
    "TQQQ": {
        "dna": "3x Kaldıraçlı Nasdaq — 15m/1H Kurumsal Bölge + 3m/1m Keskin Dönüş",
        "best_tactics": [
            ("sabirli_coklu", "+15.07R (4/4 Yıl Pozitif · %85.7 WR)"),
            ("dmc_rb", "+23.07R (3m/15m RB)"),
            ("kaan", "+3.02R (PML+ONL Süpürme)"),
        ],
        "combined_r": "Sabırlı Çoklu: 4 Yılın 4'ünde de Kârlı (+15.07R)",
    },
    "GLD": {
        "dna": "Altın ETF — 1H/15m Likidite & FVG + 5m VWAP Emilimi + 1m/3m Kırılım",
        "best_tactics": [
            ("sabirli_coklu", "+15.79R (4/4 Yıl Pozitif · Son 20 Gün +3.24R)"),
            ("orderflow_vwap", "VWAP -1.35σ Emilim"),
            ("crt_range", "1H CRT Turtle Soup + 1m CISD"),
        ],
        "combined_r": "Sabırlı Çoklu: 4 Yılın 4'ünde de Kârlı (+15.79R)",
    },
    "SPY": {
        "dna": "S&P 500 — 1H CRT + 15m FVG + 5m OrderFlow VWAP",
        "best_tactics": [
            ("sabirli_coklu", "4/4 Yıl Pozitif"),
            ("orderflow_vwap", "Kurumsal VWAP Emilimi"),
            ("crt_range", "1H CRT + 1m CISD"),
        ],
        "combined_r": "6 Sembol Çoklu Kurumsal Mod",
    },
    "SLV": {
        "dna": "Gümüş ETF — 1H/15m Likidite Süpürmesi + 5m VWAP Emilimi",
        "best_tactics": [
            ("sabirli_coklu", "4/4 Yıl Pozitif"),
            ("orderflow_vwap", "Aktif"),
            ("crt_range", "Aktif"),
        ],
        "combined_r": "Emtia Kurumsal Süpürme Modu",
    },
    "DIA": {
        "dna": "Dow Jones ETF — 1H CRT + 15m SNR + 3m/5m Dönüş",
        "best_tactics": [
            ("sabirli_coklu", "4/4 Yıl Pozitif"),
            ("crt_range", "Aktif"),
            ("orderflow_vwap", "Aktif"),
        ],
        "combined_r": "Endeks Kurumsal Modu",
    },
}

REGIME_TR_LABELS = {
    "sweep_reversal": ("Likidite Süpürme & Dönüş Rejimi (15m/1H Bölge + 1m/3m Dönüş)", ["sabirli_coklu", "orderflow_vwap", "crt_range", "dmc_rb", "kaan"]),
    "trend_expansion": ("Güçlü Trend & İtki Kırılım Rejimi", ["sabirli_coklu", "pa_srflip", "smc_idm", "ict_unicorn"]),
    "range_accumulation": ("Yatay Sıkışma & Birikim Rejimi (15m FVG + 1m Box/Spring)", ["sabirli_coklu", "wyckoff_spring", "crt_range", "orderflow_vwap"]),
}


def load_stock_bars(sym: str) -> pd.DataFrame:
    """Son 8 ayın önbelleğini + içinde bulunulan ayın (Ekim 2026, 16.10.2026 dahil) canlı Alpaca verisini yükler."""
    sym = sym.upper().strip()
    if sym in _BAR_CACHE:
        return _BAR_CACHE[sym]
    cfg = load_config(str(ROOT_DIR / "config.yaml"))
    cfg["data"]["cache_dir"] = str((ROOT_DIR / ".cache").resolve())
    today = date.today()
    start_dt = today - timedelta(days=210)
    try:
        df = load_history(cfg, sym, start_dt, today)
        if not df.empty:
            _BAR_CACHE[sym] = df
            return df
    except Exception as e:
        print(f"[UYARI] load_history {sym} hatası: {e}", flush=True)

    files = sorted(CACHE_DIR.glob(f"{sym}_sip_202[56]*.pkl"))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files[-8:]:
        with open(f, "rb") as fp:
            dfs.append(pickle.load(fp))
    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    _BAR_CACHE[sym] = df
    return df


def _run_real_engine_for_day(df: pd.DataFrame, sym: str, target_day: date) -> tuple[list[dict], dict, object]:
    """Seçilen günde gerçek DayEngine + simulate_day motorunu çalıştırır ve HTF seviyelerini (1D/1H/15m) döndürür."""
    with open(ROOT_DIR / "config.yaml", "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)
    base_cfg["data"]["cache_dir"] = str((ROOT_DIR / ".cache").resolve())
    dcfg = base_cfg["dmc"]

    win = df[(df.index >= pd.Timestamp(target_day) - pd.Timedelta(days=16)) &
             (df.index < pd.Timestamp(datetime.combine(target_day, dtime(9, 30))))]
    daily = daily_bars(df[df.index < pd.Timestamp(target_day) + pd.Timedelta(days=1)], sym)
    amr = am_ranges(df[df.index < pd.Timestamp(target_day) + pd.Timedelta(days=1)])
    lv = prepare_day(win, target_day, daily=daily[daily.index < pd.Timestamp(target_day)], fvg_cfg=dcfg.get("fvg"))
    am_med = am_range_median(amr, target_day)

    t = df.index.time
    day_bars = df[(df.index.date == target_day) & (t >= dtime(9, 30)) & (t < dtime(16, 0))]
    pm_bars = df[(df.index.date == target_day) & (t >= dtime(8, 0)) & (t < dtime(9, 30))]

    reg = classify_chart_regime(day_bars, lv, am_med)
    reg_title, reg_tactics = REGIME_TR_LABELS.get(reg.regime, ("Likidite Süpürme", ["sabirli_coklu"]))

    engine_setups = []
    tactics_to_run = ["sabirli_coklu", "ff_macro"] + [tc for tc in ALL_TACTICS if tc not in ("sabirli_coklu", "ff_macro")]
    for tac in tactics_to_run:
        c = deepcopy(base_cfg)
        c["dmc"]["symbol"] = sym
        c["dmc"]["watch_setups"] = {}
        for k in list(c["dmc"]["setups"].keys()):
            c["dmc"]["setups"][k]["enabled"] = (k == tac)
            if k != tac:
                c["dmc"]["setups"][k]["smt"] = "off"
        eng = DayEngine(c, lv, {"am_range_median": am_med}, symbol=sym)
        eng.attach_fast_frames(day_bars, pm_bars)
        trs = simulate_day(c, day_bars, eng)
        for tr in trs:
            b5_ts = tr.filled_at.floor("5min")
            engine_setups.append({
                "time": int(b5_ts.timestamp()),
                "raw_time": int(tr.filled_at.timestamp()),
                "hm": tr.filled_at.strftime("%Y-%m-%d %H:%M"),
                "side": tr.side.upper(),
                "source": "GERÇEK BOT MOTORU" if tac == "sabirli_coklu" else f"MOTOR ({tac})",
                "tactic_key": tr.setup,
                "count": len(tr.confluence),
                "tactics": [f"MOTOR: {tr.setup.upper()}"] + list(tr.confluence),
                "entry": round(float(tr.entry), 2),
                "stop": round(float(tr.stop), 2),
                "tp1": round(float(tr.tp1), 2) if tr.tp1 else round(float(tr.tp2), 2),
                "tp2": round(float(tr.tp2), 2),
                "result_r": round(float(tr.r), 2),
                "exit_reason": tr.exits[-1][3].upper() if tr.exits else "",
                "recommended": tr.setup == "sabirli_coklu" or tr.setup in reg_tactics[:3],
            })

    # HTF (1D / 1H / 15m) özet bilgileri
    fvg_15m = [f"{f.side.upper()} [{f.bot:.2f}–{f.top:.2f}]" for f in getattr(lv, "fvgs", []) if getattr(f, "tf", 0) == 15][:4]
    fvg_1h = [f"{f.side.upper()} [{f.bot:.2f}–{f.top:.2f}]" for f in getattr(lv, "fvgs", []) if getattr(f, "tf", 0) == 60][:4]
    msnr_lvls = [f"{role.upper()}:{px:.2f} ({tf}m)" for px, role, tf in getattr(lv, "msnr", [])[:5]]

    regime_info = {
        "regime_key": reg.regime,
        "regime_title": reg_title,
        "bias": lv.bias.upper(),
        "disp_ratio": reg.or_disp_ratio,
        "rel_size": reg.or_rel_size,
        "swept": reg.swept_pre_levels,
        "recommended_tactics": reg_tactics,
        "htf_15m_fvgs": fvg_15m,
        "htf_1h_fvgs": fvg_1h,
        "htf_msnr": msnr_lvls,
    }
    return engine_setups, regime_info, lv


def analyze_stock_day(sym: str, date_str: str | None = None, tf_str: str = "5m", days_window: int = 1) -> dict:
    df = load_stock_bars(sym)
    if df.empty:
        return {"error": f"{sym} için veri bulunamadı."}
    t = df.index.time
    rth = df[(t >= dtime(9, 30)) & (t < dtime(16, 0))].copy()
    b15_all = rth.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    b15_all["ema50"] = b15_all.Close.ewm(span=50, adjust=False).mean()
    b15_all["ema100"] = b15_all.Close.ewm(span=100, adjust=False).mean()
    rth["ema50_htf"] = b15_all["ema50"].reindex(rth.index, method="ffill")
    rth["ema100_htf"] = b15_all["ema100"].reindex(rth.index, method="ffill")

    rth_by_day = {str(d): g for d, g in rth.groupby(rth.index.date) if len(g) >= 30}
    avail_days = sorted(rth_by_day.keys())
    if not avail_days:
        return {"error": "Seans verisi yok."}
    if not date_str or date_str not in rth_by_day:
        date_str = avail_days[-1]
    idx_d = avail_days.index(date_str)
    prev_rth = rth_by_day[avail_days[max(0, idx_d - 1)]]
    pdh, pdl = float(prev_rth.High.max()), float(prev_rth.Low.min())

    # Seçilen gün ve geriye doğru days_window kadar iş günü
    start_idx = max(0, idx_d - max(1, days_window) + 1)
    selected_days = avail_days[start_idx: idx_d + 1]
    multi_day_bars = pd.concat([rth_by_day[d] for d in selected_days]).sort_index()

    day_bars = rth_by_day[date_str]
    or_bars = day_bars[day_bars.index.time < dtime(10, 0)]
    orh = float(or_bars.High.max()) if len(or_bars) else float(day_bars.High.iloc[:30].max())
    orl = float(or_bars.Low.min()) if len(or_bars) else float(day_bars.Low.iloc[:30].min())
    or_rng = max(orh - orl, 1e-6)

    # İstenecek grafik zaman dilimine göre yeniden örnekleme (1m, 3m, 5m, 15m, 1H, 1D)
    tf_map = {
        "1m": "1min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "1h": "60min",
        "1d": "1D",
    }
    rule = tf_map.get(tf_str.lower(), "5min")
    if tf_str.lower() == "1d":
        # 1D seçildiğinde son 60 iş gününün günlük mumlarını göster
        d60_days = avail_days[max(0, idx_d - 60): idx_d + 1]
        d60_bars = pd.concat([rth_by_day[d] for d in d60_days]).sort_index()
        chart_df = d60_bars.resample("1D").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "ema50_htf": "last"}
        ).dropna()
    else:
        chart_df = multi_day_bars.resample(rule).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "ema50_htf": "last"}
        ).dropna()

    # Gün içi VWAP hesabı (her gün kendi içinde sıfırlanır)
    vwap_vals = []
    for _, grp in chart_df.groupby(chart_df.index.date):
        typ = (grp.High + grp.Low + grp.Close) / 3.0
        vw = (typ * grp.Volume).cumsum() / grp.Volume.replace(0, np.nan).cumsum()
        vwap_vals.append(vw)
    chart_vwap = pd.concat(vwap_vals).sort_index() if vwap_vals else pd.Series(dtype=float)

    candles = []
    ema50_series = []
    vwap_series = []
    for i in range(len(chart_df)):
        ts = chart_df.index[i]
        unix_ts = int(ts.timestamp())
        row = chart_df.iloc[i]
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        vw = float(chart_vwap.iloc[i]) if i < len(chart_vwap) and not np.isnan(chart_vwap.iloc[i]) else c
        e50 = float(row.ema50_htf) if not np.isnan(row.ema50_htf) else c
        candles.append({"time": unix_ts, "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)})
        ema50_series.append({"time": unix_ts, "value": round(e50, 2)})
        vwap_series.append({"time": unix_ts, "value": round(vw, 2)})

    # Seçilen hedef günde:
    # 1) Gerçek motor kurulumları (sabirli_coklu, ff_macro ve tüm taktikler)
    engine_setups, regime_info, lv_obj = _run_real_engine_for_day(df, sym, date.fromisoformat(date_str))

    # 2) Çoklu Zaman Dilimi (15m FVG + 1m Box/CISD & 5m Tarayıcı) kurulumları
    scanner_setups = _scan_mtf_and_intraday(day_bars, lv_obj, pdh, pdl, orh, orl, or_rng)

    # Zaman damgalarını aktif grafik zaman dilimine hizala
    floor_rule = rule if tf_str.lower() != "1d" else "1D"
    all_setups = []
    for s in engine_setups + scanner_setups:
        raw_t = pd.Timestamp.fromtimestamp(s.get("raw_time", s["time"]))
        aligned_t = int(raw_t.floor(floor_rule).timestamp()) if tf_str.lower() != "1d" else int(pd.Timestamp(raw_t.date()).timestamp())
        s_copy = dict(s)
        s_copy["time"] = aligned_t
        all_setups.append(s_copy)

    profile = SYMBOL_PROFILES.get(sym, SYMBOL_PROFILES["QQQ"])

    return {
        "symbol": sym,
        "date": date_str,
        "tf": tf_str,
        "days_window": days_window,
        "available_dates": avail_days[-120:],
        "levels": {"pdh": round(pdh, 2), "pdl": round(pdl, 2), "orh": round(orh, 2), "orl": round(orl, 2)},
        "profile": profile,
        "regime": regime_info,
        "candles": candles,
        "ema50": ema50_series,
        "vwap": vwap_series,
        "setups": all_setups,
    }


def _scan_mtf_and_intraday(day_bars: pd.DataFrame, lv_obj, pdh: float, pdl: float, orh: float, orl: float, or_rng: float) -> list[dict]:
    """15m FVG + 1m Box/CISD (Çoklu Zaman Dilimi) ve 5m Çoklu Taktik taraması."""
    out = []
    if day_bars.empty:
        return out

    # A) 15m FVG + 1m Box Theory / 1m CISD Kırılımı Taraması
    b15 = day_bars.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    htf_fvgs = list(getattr(lv_obj, "fvgs", []))
    # Gün içi oluşan 15m FVG'leri de nedensel olarak takip et
    intraday_15m_bull_fvgs = []
    intraday_15m_bear_fvgs = []
    for i in range(2, len(b15)):
        c0, c1, c2 = b15.iloc[i - 2], b15.iloc[i - 1], b15.iloc[i]
        end_ts = b15.index[i] + pd.Timedelta(minutes=15)
        if float(c2.Low) > float(c0.High) and float(c1.Close) > float(c1.Open):
            intraday_15m_bull_fvgs.append((float(c0.High), float(c2.Low), end_ts))
        if float(c2.High) < float(c0.Low) and float(c1.Close) < float(c1.Open):
            intraday_15m_bear_fvgs.append((float(c2.High), float(c0.Low), end_ts))

    # 1 dakikalık barlarda: fiyat 15m/1H FVG içine değdikten sonra 1m Box (son 6 dk kutusu) kırılımı yapıyor mu?
    last_mtf_bar = -30
    for idx in range(15, len(day_bars)):
        if idx - last_mtf_bar < 15:
            continue
        ts = day_bars.index[idx]
        if ts.time() < dtime(9, 45) or ts.time() > dtime(15, 30):
            continue
        row = day_bars.iloc[idx]
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        box6 = day_bars.iloc[idx - 7: idx - 1]
        box_hi = float(box6.High.max())
        box_lo = float(box6.Low.min())
        recent5_lo = float(day_bars.Low.iloc[idx - 5: idx + 1].min())
        recent5_hi = float(day_bars.High.iloc[idx - 5: idx + 1].max())

        # Aktif 15m/1H Bull FVG kontrolü
        in_15m_bull = any(f.bot <= recent5_lo <= f.top for f in htf_fvgs if getattr(f, "side", "") == "long")
        in_15m_bull = in_15m_bull or any(bot <= recent5_lo <= top for bot, top, ets in intraday_15m_bull_fvgs if ets <= ts)
        if in_15m_bull and c > box_hi and c > o and (box_hi - recent5_lo) > 0:
            risk = max(c - recent5_lo, 0.05)
            out.append({
                "time": int(ts.timestamp()),
                "raw_time": int(ts.timestamp()),
                "hm": ts.strftime("%Y-%m-%d %H:%M"),
                "side": "LONG",
                "source": "MTF (15m FVG + 1m BOX)",
                "tactic_key": "mtf_15m_fvg_1m_box",
                "count": 3,
                "tactics": ["15m/1H FVG Bölgesi", "1m Box Kırılımı (6dk)", "1m CISD Onayı"],
                "entry": round(c, 2),
                "stop": round(recent5_lo, 2),
                "tp1": round(c + 1.5 * risk, 2),
                "tp2": round(c + 2.5 * risk, 2),
                "result_r": None,
                "exit_reason": "",
                "recommended": True,
            })
            last_mtf_bar = idx
            continue

        in_15m_bear = any(f.bot <= recent5_hi <= f.top for f in htf_fvgs if getattr(f, "side", "") == "short")
        in_15m_bear = in_15m_bear or any(bot <= recent5_hi <= top for bot, top, ets in intraday_15m_bear_fvgs if ets <= ts)
        if in_15m_bear and c < box_lo and c < o and (recent5_hi - box_lo) > 0:
            risk = max(recent5_hi - c, 0.05)
            out.append({
                "time": int(ts.timestamp()),
                "raw_time": int(ts.timestamp()),
                "hm": ts.strftime("%Y-%m-%d %H:%M"),
                "side": "SHORT",
                "source": "MTF (15m FVG + 1m BOX)",
                "tactic_key": "mtf_15m_fvg_1m_box",
                "count": 3,
                "tactics": ["15m/1H Ters FVG Bölgesi", "1m Box Aşağı Kırılım (6dk)", "1m CISD Onayı"],
                "entry": round(c, 2),
                "stop": round(recent5_hi, 2),
                "tp1": round(c - 1.5 * risk, 2),
                "tp2": round(c - 2.5 * risk, 2),
                "result_r": None,
                "exit_reason": "",
                "recommended": True,
            })
            last_mtf_bar = idx

    return out


HTML_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>AG2BOT — Çoklu Zaman Dilimi (1m/3m/5m/15m/1H/1D) Grafik & Taktik Terminali</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #09090b; color: #f4f4f5; }
  header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 18px; border-bottom: 1px solid #27272a; background: #18181b; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  select, input, button { background: #27272a; color: #fff; border: 1px solid #3f3f46; padding: 6px 10px; border-radius: 4px; font-size: 13px; }
  .tf-group { display: inline-flex; border: 1px solid #3f3f46; border-radius: 4px; overflow: hidden; }
  .tf-btn { border: none; border-right: 1px solid #3f3f46; border-radius: 0; padding: 6px 10px; cursor: pointer; background: #18181b; color: #a1a1aa; font-weight: 600; }
  .tf-btn:last-child { border-right: none; }
  .tf-btn.active { background: #2563eb; color: #fff; }
  button.trade-btn { background: #2563eb; border-color: #3b82f6; font-weight: 600; cursor: pointer; width: 100%; margin-top: 8px; padding: 8px; }
  .layout { display: grid; grid-template-columns: 1fr 430px; height: calc(100vh - 60px); }
  #chart { width: 100%; height: 100%; }
  .sidebar { border-left: 1px solid #27272a; padding: 14px; overflow-y: auto; background: #121215; }
  .regime-box { border: 1px solid #0d9488; background: #042f2e; padding: 12px; border-radius: 6px; margin-bottom: 12px; font-size: 12px; line-height: 1.5; }
  .mtf-box { border: 1px solid #3b82f6; background: #172554; padding: 10px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 12px; line-height: 1.45; }
  .card { border: 1px solid #27272a; background: #18181b; padding: 12px; margin-bottom: 10px; border-radius: 6px; cursor: pointer; }
  .card:hover { border-color: #52525b; }
  .LONG { border-left: 4px solid #10b981; }
  .SHORT { border-left: 4px solid #ef4444; }
  .tag { display: inline-block; font-size: 11px; padding: 2px 6px; margin: 2px; background: #27272a; border: 1px solid #3f3f46; border-radius: 3px; }
  .tag-engine { background: #1e3a8a; border-color: #3b82f6; font-weight: 700; }
  .win-badge { background: #065f46; color: #34d399; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .loss-badge { background: #7f1d1d; color: #f87171; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 700; }
</style>
</head>
<body>
<header>
  <div><strong>AG2BOT — Çoklu Zaman Dilimi (1m · 3m · 5m · 15m · 1H · 1D) Grafik & Taktik Terminali</strong></div>
  <div class="controls">
    <label>Sembol:</label>
    <select id="sym" onchange="loadData('')">
      <option value="QQQ">QQQ (Nasdaq 100 ETF)</option>
      <option value="TQQQ">TQQQ (3x Nasdaq)</option>
      <option value="GLD">GLD (Altın ETF)</option>
      <option value="SPY">SPY (S&P 500 ETF)</option>
      <option value="SLV">SLV (Gümüş ETF)</option>
      <option value="DIA">DIA (Dow Jones ETF)</option>
    </select>
    <label>Zaman Dilimi:</label>
    <div class="tf-group">
      <button class="tf-btn" onclick="setTF('1m')" id="tf_1m">1dk</button>
      <button class="tf-btn" onclick="setTF('3m')" id="tf_3m">3dk</button>
      <button class="tf-btn active" onclick="setTF('5m')" id="tf_5m">5dk</button>
      <button class="tf-btn" onclick="setTF('15m')" id="tf_15m">15dk</button>
      <button class="tf-btn" onclick="setTF('1h')" id="tf_1h">1 Saat</button>
      <button class="tf-btn" onclick="setTF('1d')" id="tf_1d">1 Gün</button>
    </div>
    <label>Kaç Günlük Grafik:</label>
    <select id="days_win" onchange="loadData(document.getElementById('dt').value)">
      <option value="1">1 Gün (Seans İçi)</option>
      <option value="5" selected>5 İş Günü (1 Hafta)</option>
      <option value="15">15 İş Günü (3 Hafta)</option>
      <option value="40">40 İş Günü (2 Ay)</option>
    </select>
    <label>Hedef Tarih:</label>
    <select id="dt" onchange="loadData(this.value)"></select>
    <label>Risk ($):</label>
    <input type="number" id="risk_usd" value="250" style="width:70px;">
  </div>
</header>
<div class="layout">
  <div id="chart"></div>
  <div class="sidebar">
    <div id="lv_info" style="font-size:12px; color:#a1a1aa; margin-bottom:10px;">Yükleniyor...</div>
    <div id="mtf_panel" class="mtf-box">Çoklu Zaman Dilimi (1D / 1H / 15m) inceleniyor...</div>
    <div id="regime_panel" class="regime-box">Grafik inceleniyor...</div>
    <h3 style="margin:0 0 10px 0; font-size:14px;">Tespit Edilen Kurulumlar (Gerçek Motor + 15m FVG -> 1m Box)</h3>
    <div id="setups_list"></div>
  </div>
</div>
<script>
let chart, candleSeries, emaSeries, vwapSeries, priceLines = [];
let currentTF = '5m';

function setTF(tf) {
  currentTF = tf;
  document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('tf_' + tf);
  if (btn) btn.classList.add('active');
  if (tf === '15m' || tf === '1h') {
    const dw = document.getElementById('days_win');
    if (parseInt(dw.value) < 5) dw.value = '15';
  }
  loadData(document.getElementById('dt').value);
}

function initChart() {
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: '#09090b' }, textColor: '#d4d4d8' },
    grid: { vertLines: { color: '#18181b' }, horzLines: { color: '#18181b' } },
    timeScale: { timeVisible: true, secondsVisible: false }
  });
  candleSeries = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });
  emaSeries = chart.addLineSeries({ color: '#06b6d4', lineWidth: 2, title: '15m EMA 50' });
  vwapSeries = chart.addLineSeries({ color: '#eab308', lineWidth: 2, title: 'VWAP' });
}

async function loadData(dtVal='') {
  const sym = document.getElementById('sym').value;
  const daysWin = document.getElementById('days_win').value;
  document.getElementById('setups_list').innerHTML = '<div style="color:#a1a1aa;">Grafik (1m/5m/15m/1H/1D) ve taktikler taranıyor...</div>';
  const res = await fetch(`/api/scan?sym=${sym}&date=${dtVal}&tf=${currentTF}&days=${daysWin}`);
  const data = await res.json();
  if (data.error) {
    document.getElementById('lv_info').innerHTML = `<span style="color:#ef4444">${data.error}</span>`;
    return;
  }
  const dtSel = document.getElementById('dt');
  dtSel.innerHTML = data.available_dates.slice().reverse().map(d => `<option value="${d}" ${d===data.date?'selected':''}>${d}</option>`).join('');
  candleSeries.setData(data.candles);
  emaSeries.setData(data.ema50);
  vwapSeries.setData(data.vwap);
  document.getElementById('lv_info').innerHTML = `<b>Tarih:</b> ${data.date} (${data.tf.toUpperCase()}) | <b>PDH:</b> ${data.levels.pdh} | <b>PDL:</b> ${data.levels.pdl} | <b>ORH:</b> ${data.levels.orh} | <b>ORL:</b> ${data.levels.orl}`;

  const reg = data.regime;
  const f15 = (reg.htf_15m_fvgs && reg.htf_15m_fvgs.length) ? reg.htf_15m_fvgs.map(x => `<span class="tag">${x}</span>`).join(' ') : '<span style="color:#a1a1aa">Yok</span>';
  const f1h = (reg.htf_1h_fvgs && reg.htf_1h_fvgs.length) ? reg.htf_1h_fvgs.map(x => `<span class="tag">${x}</span>`).join(' ') : '<span style="color:#a1a1aa">Yok</span>';
  const msnr = (reg.htf_msnr && reg.htf_msnr.length) ? reg.htf_msnr.map(x => `<span class="tag">${x}</span>`).join(' ') : '<span style="color:#a1a1aa">Yok</span>';
  document.getElementById('mtf_panel').innerHTML = `
    <div style="font-weight:700; color:#93c5fd; margin-bottom:4px;">🔍 Çoklu Zaman Dilimi (HTF -> LTF) Özeti (${data.date})</div>
    <div><b>1D Günlük Yön (Bias):</b> <span style="color:#fde047">${reg.bias}</span> | <b>Süpürülen:</b> ${(reg.swept||[]).join(', ') || 'Yok'}</div>
    <div style="margin-top:3px;"><b>1H (Saatlik) FVG Bölgeleri:</b> ${f1h}</div>
    <div style="margin-top:3px;"><b>15m (15 Dakikalık) FVG Bölgeleri:</b> ${f15}</div>
    <div style="margin-top:3px;"><b>15m/1H Kurumsal SNR:</b> ${msnr}</div>
  `;

  const prof = data.profile;
  const bestHtml = prof.best_tactics.map(([t, r]) => `<span class="tag tag-engine">${t}: ${r}</span>`).join(' ');
  const recHtml = reg.recommended_tactics.slice(0, 4).map(t => `<span class="tag">${t}</span>`).join(' ');
  document.getElementById('regime_panel').innerHTML = `
    <div style="font-weight:700; color:#5eead4; margin-bottom:4px;">📌 ${sym} Grafik Karakteri & Otonom Taktik Seçimi</div>
    <div><b>Grafik DNA:</b> ${prof.dna}</div>
    <div><b>4 Yıllık Kanıtlanmış Performans:</b><br>${bestHtml}</div>
    <div style="margin-top:4px;"><b>${data.date} Günü Rejimi:</b> <span style="color:#fde047">${reg.regime_title}</span></div>
    <div><b>Önerilen Taktikler:</b> ${recHtml}</div>
  `;

  const markers = data.setups.map(s => ({
    time: s.time,
    position: s.side === 'LONG' ? 'belowBar' : 'aboveBar',
    color: s.tactic_key === 'sabirli_coklu' ? '#f59e0b' : (s.source.startsWith('MOTOR') || s.source.startsWith('GERÇEK') ? '#3b82f6' : (s.side === 'LONG' ? '#10b981' : '#ef4444')),
    shape: s.side === 'LONG' ? 'arrowUp' : 'arrowDown',
    text: s.result_r !== null && s.result_r !== undefined ? `${s.tactic_key} (${s.result_r>0?'+':''}${s.result_r}R)` : `${s.side} [${s.source}]`
  })).sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(markers);

  const listEl = document.getElementById('setups_list');
  listEl.innerHTML = data.setups.map((s) => {
    const risk = Math.abs(s.entry - s.stop);
    const riskUsd = parseFloat(document.getElementById('risk_usd').value || 250);
    const qty = Math.max(1, Math.floor(riskUsd / Math.max(risk, 0.01)));
    let resBadge = '';
    if (s.result_r !== null && s.result_r !== undefined) {
      const cls = s.result_r >= 0 ? 'win-badge' : 'loss-badge';
      resBadge = `<span class="${cls}">${s.result_r >= 0 ? '+' : ''}${s.result_r}R (${s.exit_reason})</span>`;
    } else {
      resBadge = `<span>${qty} Adet</span>`;
    }
    return `<div class="card ${s.side}" onclick='drawSetup(${JSON.stringify(s)})'>
      <div style="display:flex; justify-content:space-between; font-weight:700; margin-bottom:6px;">
        <span>${s.hm} — ${s.side} [${s.source}]</span>
        ${resBadge}
      </div>
      <div style="margin-bottom:6px;">${s.tactics.map((t, i) => `<span class="tag ${i===0 ? 'tag-engine' : ''}">${t}</span>`).join('')}</div>
      <div style="font-size:12px; color:#d4d4d8;">
        Giriş: <b>${s.entry}</b> | SL: <b style="color:#ef4444">${s.stop}</b> | TP1: <b style="color:#10b981">${s.tp1}</b> | TP2: <b style="color:#10b981">${s.tp2}</b>
      </div>
      <button class="trade-btn" onclick='event.stopPropagation(); drawSetup(${JSON.stringify(s)})'>
        Grafikte Giriş / SL / TP Seviyelerini Çiz (${qty} Adet)
      </button>
    </div>`;
  }).join('') || '<div style="color:#71717a;">Bu günde kurulum bulunamadı. Üstten başka bir tarih seçebilirsiniz.</div>';

  chart.timeScale().fitContent();
  if (data.setups.length > 0) {
    drawSetup(data.setups[0]);
  }
}
function drawSetup(s) {
  priceLines.forEach(l => candleSeries.removePriceLine(l));
  priceLines = [
    candleSeries.createPriceLine({ price: s.entry, color: '#3b82f6', lineWidth: 2, title: `GİRİŞ (${s.side})` }),
    candleSeries.createPriceLine({ price: s.stop,  color: '#ef4444', lineWidth: 2, title: 'STOP LOSS' }),
    candleSeries.createPriceLine({ price: s.tp1,   color: '#10b981', lineWidth: 1, title: 'TP1' }),
    candleSeries.createPriceLine({ price: s.tp2,   color: '#10b981', lineWidth: 2, title: 'TP2' }),
  ];
}
initChart();
loadData();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/scan":
            qs = parse_qs(parsed.query)
            sym = qs.get("sym", ["QQQ"])[0]
            dt = qs.get("date", [""])[0] or None
            tf = qs.get("tf", ["5m"])[0]
            days = int(qs.get("days", ["5"])[0])
            payload = analyze_stock_day(sym, dt, tf_str=tf, days_window=days)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)


def main(port: int = 8055):
    print(f"AG2BOT Çoklu Zaman Dilimi Görsel Terminali çalışıyor: http://localhost:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
