"""Formasyon Laboratuvarı: her mum ve grafik formasyonunu istatistiksel olarak test eder.

Yöntem (her zaman dilimi için ayrı):
  1. NASDAQ-100'deki her hisse, her bar için hem LONG hem SHORT işlem sonucu hesaplanır:
     giriş = sonraki barın açılışı, stop = 1 ATR, hedef = 1.5 ATR, en fazla N bar tut,
     gün içi zaman dilimlerinde pozisyon seans sonunda kapatılır. Komisyon + kayma düşülür.
  2. BAZ ORAN = AYNI GÜN, tüm hisselerde, aynı yönde her barda girmenin ortalama sonucu
     (piyasanın o günkü yönü ve maliyet etkisi böylece temizlenir).
  3. Formasyonun FARKI = formasyon sonrası sonuç - aynı günün baz oranı (gün ortalaması).
  4. t-istatistiği GÜN bazında hesaplanır (aynı gün birçok hissede oluşan sinyaller tek gözlem
     sayılır; böylece piyasa geneli hareketler sonucu şişirmez).
  5. Dönem ikiye bölünür: formasyon her iki yarıda da baz orandan iyiyse tutarlıdır.

Karar:
  GEÇERLİ  : n>=30, >=20 farklı gün, t>=2 ve her iki yarıda fark > 0
  ZAYIF    : t>=1 ve her iki yarıda fark > 0 (bota girmez)
  TERS     : t<=-2 ve her iki yarıda fark < 0  (formasyon ters yönde çalışıyor)
  GEÇERSİZ : diğer durumlar
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .candles import PATTERNS, detect_patterns
from .chart_patterns import CHART_PATTERNS, detect_chart_patterns
from .data import download_ohlcv
from .indicators import add_indicators
from .intraday import session_only
from .rejection_blocks import PARAMS as RB_PARAMS
from .rejection_blocks import RB_STRUCT, find_rejection_blocks

TIMEFRAMES = {
    "1d": {"period": "5y", "interval": "1d", "horizon": 5, "intraday": False},
    "1h": {"period": "730d", "interval": "1h", "horizon": 7, "intraday": True},
    "15m": {"period": "60d", "interval": "15m", "horizon": 16, "intraday": True},
    "5m": {"period": "60d", "interval": "5m", "horizon": 24, "intraday": True},
}
ALL = {**PATTERNS, **CHART_PATTERNS}


def _paths(d: pd.DataFrame, horizon: int, intraday: bool) -> dict:
    """Her bar için sonraki `horizon` barın OHLC matrisleri (horizon, n) + geçerlilik maskesi."""
    O, H, L, C = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    n = len(C)
    idx = np.arange(n)[None, :] + np.arange(1, horizon + 1)[:, None]  # (horizon, n)
    inside = idx < n
    j = np.where(inside, idx, n - 1)
    Ok, Hk, Lk, Ck = O[j], H[j], L[j], C[j]
    valid = inside.copy()
    if intraday:
        day = d.index.normalize().to_numpy()
        same_day_entry = np.r_[day[1:] == day[:-1], False]
        valid &= day[j] == day[np.minimum(np.arange(n) + 1, n - 1)][None, :]
        valid[:, ~same_day_entry] = False
    valid = np.cumprod(valid, axis=0).astype(bool)
    nvalid = valid.sum(axis=0)
    entry = np.where(nvalid > 0, Ok[0], np.nan)
    last_close = Ck[np.maximum(nvalid - 1, 0), np.arange(n)]
    return {"Ok": Ok, "Hk": Hk, "Lk": Lk, "valid": valid, "entry": entry, "last_close": last_close,
            "n": n, "horizon": horizon}


def _side(P: dict, side: int, stop: np.ndarray, tgt: np.ndarray, cost: float):
    """Stop önce kontrol edilir (muhafazakâr). Boşlukla stop/hedef geçilirse açılıştan dolar."""
    Ok, Hk, Lk, valid, entry, horizon, n = P["Ok"], P["Hk"], P["Lk"], P["valid"], P["entry"], P["horizon"], P["n"]
    if side == 1:
        hs, ht = (Lk <= stop) & valid, (Hk >= tgt) & valid
    else:
        hs, ht = (Hk >= stop) & valid, (Lk <= tgt) & valid
    fs = np.where(hs.any(0), hs.argmax(0), horizon + 1)
    ft = np.where(ht.any(0), ht.argmax(0), horizon + 1)
    cols = np.arange(n)
    o_s = Ok[np.minimum(fs, horizon - 1), cols]
    o_t = Ok[np.minimum(ft, horizon - 1), cols]
    if side == 1:
        px_s, px_t = np.minimum(o_s, stop), np.maximum(o_t, tgt)
    else:
        px_s, px_t = np.maximum(o_s, stop), np.minimum(o_t, tgt)
    px = np.where((fs <= ft) & (fs <= horizon), px_s, np.where(ft <= horizon, px_t, P["last_close"]))
    gross = side * (px / entry - 1)
    return gross - 2 * cost, gross


def outcomes(d: pd.DataFrame, horizon: int, stop_m: float, tgt_m: float, cost: float, intraday: bool):
    """Her bar için (long_net, short_net, long_gross, short_gross) getirileri (oran)."""
    atr = d["atr"].to_numpy(float)
    P = _paths(d, horizon, intraday)
    entry = P["entry"]
    res = [_side(P, s, entry - s * stop_m * atr, entry + s * tgt_m * atr, cost) for s in (1, -1)]
    (ln, lg), (sn, sg) = res
    bad = ~np.isfinite(entry) | ~np.isfinite(atr) | (atr <= 0)
    for arr in (ln, lg, sn, sg):
        arr[bad] = np.nan
    return ln, sn, lg, sg


def outcomes_struct(d: pd.DataFrame, horizon: int, cost: float, intraday: bool, side: int,
                    stop_level: np.ndarray, rr: float):
    """Yapısal stop: stop = verilen fiyat seviyesi (ör. RB fitil ucu), hedef = giriş ± rr x risk.
    Giriş (sonraki açılış) stopun ötesindeyse işlem açılmaz (NaN)."""
    P = _paths(d, horizon, intraday)
    entry = P["entry"]
    risk = side * (entry - stop_level)
    tgt = entry + side * rr * risk
    net, gross = _side(P, side, stop_level, tgt, cost)
    bad = ~np.isfinite(entry) | ~np.isfinite(stop_level) | ~(risk > 0)
    net[bad] = np.nan
    gross[bad] = np.nan
    return net, gross


def _kind(key: str) -> str:
    if key.startswith("rb_"):
        return "Rejection Block"
    return "Grafik" if key in CHART_PATTERNS else "Mum"


def run_lab(cfg: dict, tf: str, tickers: list[str], progress=None, only: list[str] | None = None) -> pd.DataFrame:
    """only: anahtar önekleri (ör. ["rb_"]) - verilirse sadece o formasyonlar test edilir."""
    spec = TIMEFRAMES[tf]
    lab = cfg.get("lab", {})
    b = cfg["backtest"]
    cost = (b["commission_bps"] + b["slippage_bps"]) / 1e4
    rr = float(RB_PARAMS.get("rr", 2.0))
    data = download_ohlcv(tickers, period=spec["period"], interval=spec["interval"],
                          cache_dir=cfg["data"]["cache_dir"], ttl_minutes=cfg["data"]["cache_ttl_minutes"])

    keep = (lambda k: any(k.startswith(p) for p in only)) if only else (lambda k: True)
    LAB = {k: v for k, v in ALL.items() if keep(k)}
    STRUCT = {k: v for k, v in RB_STRUCT.items() if keep(k)}
    events: dict[str, list[pd.DataFrame]] = {k: [] for k in [*LAB, *STRUCT]}
    base_parts = []
    for t in tickers:
        df = data.get(t)
        if df is None:
            continue
        if spec["intraday"]:
            df = session_only(df)
        if len(df) < 150:
            continue
        d = add_indicators(df)
        pats, _ = detect_patterns(d)
        cp = detect_chart_patterns(d)
        allp = pats.join(cp)
        ln, sn, lg, sg = outcomes(d, spec["horizon"], lab.get("stop_atr", 1.0), lab.get("target_atr", 1.5),
                                  cost, spec["intraday"])
        day = d.index.normalize()
        ok = np.isfinite(ln) & np.isfinite(d["sma50"].to_numpy(float))
        base_parts.append(pd.DataFrame({"day": day[ok], "long": ln[ok], "short": sn[ok]}))
        for key, (_, direction, _) in LAB.items():
            if direction == 0 or key not in allp.columns:
                continue
            m = allp[key].to_numpy() & ok
            if m.any():
                net = ln[m] if direction > 0 else sn[m]
                gross = lg[m] if direction > 0 else sg[m]
                events[key].append(pd.DataFrame({"day": day[m], "ret": net, "gross": gross, "ticker": t}))
        if STRUCT:
            _, rb_stops, _ = find_rejection_blocks(d)
            for key, (_, direction, base_key) in STRUCT.items():
                lvl = rb_stops[base_key].to_numpy(float)
                if not np.isfinite(lvl).any():
                    continue
                net, gross = outcomes_struct(d, spec["horizon"], cost, spec["intraday"], direction, lvl, rr)
                m = np.isfinite(net) & ok
                if m.any():
                    events[key].append(pd.DataFrame({"day": day[m], "ret": net[m], "gross": gross[m], "ticker": t}))
        if progress:
            progress(t)

    base = pd.concat(base_parts, ignore_index=True)
    split = base["day"].min() + (base["day"].max() - base["day"].min()) / 2
    bmean = {s: {"all": base[s].mean(), 1: base.loc[base["day"] < split, s].mean(),
                 2: base.loc[base["day"] >= split, s].mean()} for s in ("long", "short")}
    dbase = {s: base.groupby("day")[s].mean() for s in ("long", "short")}

    rows = []
    for key, (name, direction, _) in {**LAB, **STRUCT}.items():
        if direction == 0:
            continue
        ev = pd.concat(events[key], ignore_index=True) if events[key] else pd.DataFrame(columns=["day", "ret", "gross"])
        side = "long" if direction > 0 else "short"
        n = len(ev)
        row = {"key": key, "Formasyon": name, "Tür": _kind(key),
               "Yön": "AL" if direction > 0 else "SAT", "Adet": n, "Gün": 0, "Kazanma%": np.nan,
               "Net ort%": np.nan, "Baz%": bmean[side]["all"] * 100, "Fark%": np.nan, "t": np.nan,
               "1.yarı%": np.nan, "2.yarı%": np.nan, "Karar": "VERİ YOK", "weight": 0.0}
        if n:
            # aynı günün baz oranı: o gün tüm hisselerde her bara aynı yönde girmenin ortalaması
            day_base = ev["day"].map(dbase[side]).to_numpy(float)
            ex = ev["ret"].to_numpy(float) - day_base
            okx = np.isfinite(ex)
            g = pd.Series(ex[okx]).groupby(ev["day"].to_numpy()[okx]).mean()
            nd = len(g)
            tstat = g.mean() / (g.std(ddof=1) / math.sqrt(nd)) if nd > 2 and g.std(ddof=1) > 0 else 0.0
            gi = pd.DatetimeIndex(g.index)
            e1 = g[gi < split].mean() if (gi < split).any() else np.nan
            e2 = g[gi >= split].mean() if (gi >= split).any() else np.nan
            row.update({"Gün": nd, "Kazanma%": (ev["ret"] > 0).mean() * 100, "Net ort%": ev["ret"].mean() * 100,
                        "Fark%": g.mean() * 100, "t": tstat, "1.yarı%": e1 * 100, "2.yarı%": e2 * 100})
            enough = n >= lab.get("min_n", 30) and nd >= lab.get("min_days", 20)
            min_t = lab.get("min_t", 2.0)
            both_pos = np.isfinite(e1) and np.isfinite(e2) and e1 > 0 and e2 > 0
            both_neg = np.isfinite(e1) and np.isfinite(e2) and e1 < 0 and e2 < 0
            if enough and tstat >= min_t and both_pos:
                row["Karar"], row["weight"] = "GEÇERLİ", direction * min(tstat, 4.0)
            elif enough and tstat <= -min_t and both_neg:
                row["Karar"], row["weight"] = "TERS", -direction * min(abs(tstat), 4.0)
            elif enough and tstat >= 1 and both_pos:
                row["Karar"] = "ZAYIF"
            elif not enough:
                row["Karar"] = "AZ ÖRNEK"
            else:
                row["Karar"] = "GEÇERSİZ"
        rows.append(row)
    res = pd.DataFrame(rows).sort_values("t", ascending=False, na_position="last")
    res.attrs.update({"tf": tf, "split": str(pd.Timestamp(split).date()), "base_long": bmean["long"]["all"] * 100,
                      "base_short": bmean["short"]["all"] * 100, "n_tickers": len(base_parts),
                      "start": str(pd.Timestamp(base["day"].min()).date()), "end": str(pd.Timestamp(base["day"].max()).date())})
    return res


def save_weights(cfg: dict, tf: str, res: pd.DataFrame) -> Path:
    path = Path(cfg["state_dir"]) / "pattern_weights.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    allw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    old = allw.get(tf, {}).get("patterns", {})
    new = {r["key"]: {"weight": round(float(r["weight"]), 3), "verdict": r["Karar"], "n": int(r["Adet"]),
                      "t": None if pd.isna(r["t"]) else round(float(r["t"]), 2),
                      "edge_pct": None if pd.isna(r["Fark%"]) else round(float(r["Fark%"]), 4)}
           for _, r in res.iterrows()}
    allw[tf] = {"generated": datetime.now().isoformat(timespec="minutes"), **res.attrs,
                "patterns": {**old, **new}}   # kısmi test (--only) diğer formasyonları silmez
    path.write_text(json.dumps(allw, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
