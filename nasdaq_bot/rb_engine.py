"""Rejection Block motoru: olay (event) çıkarımı + gerçekçi işlem simülasyonu.

Laboratuvardaki bar-bazlı testten farkı: burada her RB bölgesi bir OLAY'dır ve gerçek bir
trader'ın yapacağı gibi işlenir:

GİRİŞ MODLARI
  edge  : bölgenin gövde kenarına BEKLEYEN LİMİT emir (fiyat bölgeye ilk girdiği anda dolar)
  mid   : fitilin ortasına (%50, ICT "mean threshold") bekleyen limit emir
  close : ret kapanışından sonra bir sonraki barın açılışında piyasa emri

ÇIKIŞ
  stop  = fitil ucu ± tampon x ATR (yapısal),  hedef = giriş ± RR x risk,  en fazla `horizon` bar,
  gün içi zaman dilimlerinde istenirse seans sonunda kapat (flat_eod).

GERÇEKÇİLİK / MUHAFAZAKÂRLIK
  * Limit emrin dolduğu barda stop seviyesine de değilmişse işlem ZARARLA kapanmış sayılır
    (bar içi sıra bilinmediği için kötü senaryo seçilir); hedef ancak sonraki barlarda aranır.
  * Boşlukla (gap) stop/hedef geçilirse açılış fiyatından dolar.
  * Maliyet: her işlemde 2 x komisyon; piyasa girişi, stop ve zaman çıkışlarında + kayma.
    Limit giriş ve hedef (limit) çıkışlarında kayma yok.
  * Tüm özellikler (features) dolum barından ÖNCEKİ barın kapanışıyla hesaplanır - gelecek sızmaz.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "side", "wick_ratio", "zone_atr", "pivot_rvol", "sweep", "age", "depart_max", "bars_from_far",
    "trend50", "trend50_al", "slope50_al", "above200_al", "rsi", "rvol", "atr_pct", "bbw",
    "hour", "dow", "qqq_trend_al", "qqq_ret_al", "tick_hist_r", "tick_hist_n",
]


def _pivot_flags(h: np.ndarray, l: np.ndarray, k: int):
    w = 2 * k + 1
    hmax = pd.Series(h).rolling(w, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(w, center=True).min().to_numpy()
    ph = (h == hmax) & (h > np.r_[np.inf, h[:-1]])
    pl = (l == lmin) & (l < np.r_[-np.inf, l[:-1]])
    return ph, pl


def _first(mask: np.ndarray, start: int, stop: int) -> int:
    """mask[start:stop] içindeki ilk True'nun mutlak indeksi, yoksa -1."""
    if start >= stop:
        return -1
    seg = mask[start:stop]
    i = int(seg.argmax())
    return start + i if seg[i] else -1


def extract_events(d: pd.DataFrame, qqq: pd.DataFrame | None = None, *, k: int = 3, depart_atr: float = 1.0,
                   max_age: int = 60, min_wick: float = 0.3) -> pd.DataFrame:
    """Her RB bölgesi için bir satır (bölgeye en az bir kez temas edilmişse).

    d: add_indicators uygulanmış OHLCV. Dönen indeksler d içindeki pozisyonlardır.
    """
    o, h, l, c = (d[x].to_numpy(float) for x in ("Open", "High", "Low", "Close"))
    atr = d["atr"].to_numpy(float)
    n = len(c)
    if n < 100:
        return pd.DataFrame()
    sma50, sma200 = d["sma50"].to_numpy(float), d["sma200"].to_numpy(float)
    rsi, rvol = d["rsi"].to_numpy(float), d["rel_vol"].to_numpy(float)
    bbw = d["bb_width"].to_numpy(float)
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    rng = np.maximum(h - l, 1e-12)
    ph, pl = _pivot_flags(h, l, k)
    idx = d.index

    q_tr = q_r5 = None
    if qqq is not None and len(qqq):
        qa = qqq["atr"].reindex(idx, method="ffill").to_numpy(float)
        qc = qqq["Close"].reindex(idx, method="ffill").to_numpy(float)
        qs = qqq["sma50"].reindex(idx, method="ffill").to_numpy(float)
        q_tr = (qc - qs) / np.where(qa > 0, qa, np.nan)
        q_r5 = (qc / np.r_[np.full(5, np.nan), qc[:-5]] - 1) * 100

    rows = []
    last_ph: list[int] = []
    last_pl: list[int] = []
    for i in range(k + 1, n - k - 1):
        for side in (-1, 1):
            if side < 0 and not ph[i]:
                continue
            if side > 0 and not pl[i]:
                continue
            a_i = atr[i]
            if not np.isfinite(a_i) or a_i <= 0:
                continue
            lo_i, hi_i = i - 1, i + 2
            if side < 0:
                wick = h[i] - body_hi[i]
                top, bot = h[i], body_hi[lo_i:hi_i].max()
                hist = last_ph
                sweep = any(h[i] > h[j] > body_hi[i] for j in hist[-3:] if i - j <= 60)
            else:
                wick = body_lo[i] - l[i]
                top, bot = body_lo[lo_i:hi_i].min(), l[i]
                hist = last_pl
                sweep = any(l[i] < l[j] < body_lo[i] for j in hist[-3:] if i - j <= 60)
            hist.append(i)
            wr = wick / rng[i]
            if wr < min_wick or wick < 0.25 * a_i or top - bot < 0.15 * a_i:
                continue
            act = i + k                       # pivot bu barın kapanışında kesinleşir
            end = min(n, i + max_age + 1)
            if act + 1 >= end:
                continue
            seg = slice(i + 1, end)
            # geçersizlik: kapanış fitil ucunu geçerse (bu bar dahil sonrası işlem yok)
            inv_mask = (c > top) if side < 0 else (c < bot)
            inv = _first(inv_mask, act + 1, end)
            stop_scan = inv + 1 if inv >= 0 else end
            # uzaklaşma
            far_mask = (l <= bot - depart_atr * a_i) if side < 0 else (h >= top + depart_atr * a_i)
            dep = _first(far_mask, i + 1, stop_scan)
            if dep < 0:
                continue
            start = max(dep + 1, act + 1)
            edge = bot if side < 0 else top
            mid = (top + bot) / 2
            touch_mask = (h >= edge) if side < 0 else (l <= edge)
            t_touch = _first(touch_mask, start, stop_scan)
            if t_touch < 0:
                continue
            mid_mask = (h >= mid) if side < 0 else (l <= mid)
            t_mid = _first(mid_mask, t_touch, stop_scan)
            rej_mask = touch_mask & ((c < edge) if side < 0 else (c > edge))
            t_rej = _first(rej_mask, t_touch, inv if inv >= 0 else end)
            j = t_touch - 1                    # özellikler: dolumdan önceki bar
            if side < 0:
                far_i = i + 1 + int(np.argmin(l[i + 1:t_touch]))
                far_px = l[far_i]
            else:
                far_i = i + 1 + int(np.argmax(h[i + 1:t_touch]))
                far_px = h[far_i]
            al = 1 if side > 0 else -1          # "trend ile aynı yönde" işareti
            aj = atr[j] if atr[j] > 0 else a_i
            rows.append({
                "side": side, "pivot": i, "top": top, "bot": bot, "edge": edge, "mid": mid,
                "extreme": top if side < 0 else bot, "t_touch": t_touch, "t_mid": t_mid, "t_rej": t_rej,
                "t_inv": inv, "time_touch": idx[t_touch],
                "wick_ratio": wr, "zone_atr": (top - bot) / a_i,
                "pivot_rvol": rvol[i] if np.isfinite(rvol[i]) else 1.0, "sweep": int(sweep),
                "age": t_touch - i, "depart_max": abs(edge - far_px) / a_i, "bars_from_far": t_touch - far_i,
                "trend50": (c[j] - sma50[j]) / aj, "trend50_al": al * (c[j] - sma50[j]) / aj,
                "slope50_al": al * (sma50[j] - sma50[max(j - 5, 0)]) / aj,
                "above200_al": al * np.sign(c[j] - sma200[j]) if np.isfinite(sma200[j]) else 0.0,
                "rsi": rsi[j], "rvol": rvol[j], "atr_pct": aj / c[j] * 100, "bbw": bbw[j],
                "hour": idx[t_touch].hour + idx[t_touch].minute / 60, "dow": idx[t_touch].dayofweek,
                "qqq_trend_al": al * q_tr[j] if q_tr is not None else 0.0,
                "qqq_ret_al": al * q_r5[j] if q_r5 is not None else 0.0,
            })
    return pd.DataFrame(rows)


def ticker_arrays(d: pd.DataFrame) -> dict:
    return {"O": d["Open"].to_numpy(float), "H": d["High"].to_numpy(float), "L": d["Low"].to_numpy(float),
            "C": d["Close"].to_numpy(float), "ATR": d["atr"].to_numpy(float),
            "DAY": (d.index.normalize().asi8 // 86_400_000_000_000).astype(np.int64), "IDX": d.index}


def simulate(A: dict, ev: pd.DataFrame, mode: str, rr: float, buf: float, horizon: int, flat_eod: bool,
             comm: float, slip: float) -> pd.DataFrame:
    """Bir hissenin olaylarını verilen kurallarla işler. Dolmayan olaylar atılır."""
    O, H, L, C, ATR, DAY = A["O"], A["H"], A["L"], A["C"], A["ATR"], A["DAY"]
    n = len(C)
    if ev.empty:
        return pd.DataFrame()
    side = ev["side"].to_numpy(int)
    inv = ev["t_inv"].to_numpy(int)
    inv = np.where(inv < 0, n + 10, inv)
    if mode == "edge":
        fill, lvl, limit = ev["t_touch"].to_numpy(int), ev["edge"].to_numpy(float), True
    elif mode == "mid":
        fill, lvl, limit = ev["t_mid"].to_numpy(int), ev["mid"].to_numpy(float), True
    else:
        r = ev["t_rej"].to_numpy(int)
        fill, lvl, limit = np.where(r >= 0, r + 1, -1), np.full(len(ev), np.nan), False
    ok = (fill >= 1) & (fill < n) & ((fill <= inv) if limit else (fill <= inv))
    if not ok.any():
        return pd.DataFrame()
    ev, side, fill, lvl = ev[ok], side[ok], fill[ok], lvl[ok]
    m = len(ev)
    atr_e = ATR[fill - 1]
    stop = ev["extreme"].to_numpy(float) - side * buf * atr_e
    o_f = O[fill]
    if limit:
        entry = np.where(side > 0, np.minimum(o_f, lvl), np.maximum(o_f, lvl))
    else:
        entry = o_f
    risk = side * (entry - stop)
    gap_thru = ~(risk > 0)
    risk = np.where(gap_thru, np.nan, risk)
    tgt = entry + side * rr * risk

    if limit:
        fill_stop = np.where(side > 0, L[fill] <= stop, H[fill] >= stop) & ~gap_thru
        first = fill + 1
    else:
        fill_stop = np.zeros(m, bool)
        first = fill
    k = np.arange(horizon)
    jj = first[:, None] + k[None, :]
    inside = jj < n
    j = np.where(inside, jj, n - 1)
    valid = inside.copy()
    if flat_eod:
        valid &= DAY[j] == DAY[fill][:, None]
    valid = np.cumprod(valid, axis=1).astype(bool)
    Ok, Hk, Lk, Ck = O[j], H[j], L[j], C[j]
    st, tg = stop[:, None], tgt[:, None]
    long_ = (side > 0)[:, None]
    hs = np.where(long_, Lk <= st, Hk >= st) & valid
    ht = np.where(long_, Hk >= tg, Lk <= tg) & valid
    BIG = horizon + 5
    fs = np.where(hs.any(1), hs.argmax(1), BIG)
    ft = np.where(ht.any(1), ht.argmax(1), BIG)
    rows_ = np.arange(m)
    o_s = Ok[rows_, np.minimum(fs, horizon - 1)]
    o_t = Ok[rows_, np.minimum(ft, horizon - 1)]
    px_s = np.where(side > 0, np.minimum(o_s, stop), np.maximum(o_s, stop))
    px_t = np.where(side > 0, np.maximum(o_t, tgt), np.minimum(o_t, tgt))
    nvalid = valid.sum(1)
    last_c = np.where(nvalid > 0, Ck[rows_, np.maximum(nvalid - 1, 0)], C[fill])
    last_j = np.where(nvalid > 0, j[rows_, np.maximum(nvalid - 1, 0)], fill)

    stop_hit = (fs <= ft) & (fs < BIG)
    tgt_hit = ~stop_hit & (ft < BIG)
    px = np.where(stop_hit, px_s, np.where(tgt_hit, px_t, last_c))
    exit_j = np.where(stop_hit, j[rows_, np.minimum(fs, horizon - 1)],
                      np.where(tgt_hit, j[rows_, np.minimum(ft, horizon - 1)], last_j))
    reason = np.where(stop_hit, "stop", np.where(tgt_hit, "hedef", "zaman"))
    # dolum barında stop
    px = np.where(fill_stop, stop, px)
    exit_j = np.where(fill_stop, fill, exit_j)
    reason = np.where(fill_stop, "stop", reason)
    # boşlukla stopun ötesinde dolum -> hemen çık (yaklaşık sıfır brüt, maliyet + kayma)
    px = np.where(gap_thru, entry, px)
    exit_j = np.where(gap_thru, fill, exit_j)
    reason = np.where(gap_thru, "stop", reason)

    gross = side * (px / entry - 1)
    cost = 2 * comm + (0 if limit else slip) + np.where(reason == "hedef", 0.0, slip)
    net = gross - cost
    risk_pct = risk / entry
    med = np.nanmedian(risk_pct) if np.isfinite(risk_pct).any() else 0.01
    R = net / np.where(gap_thru, med, risk_pct)
    return pd.DataFrame({
        "fill": fill, "entry_time": A["IDX"][fill], "exit_time": A["IDX"][exit_j], "entry": entry, "stop": stop,
        "target": tgt, "exit_px": px, "reason": reason, "gross": gross, "net": net, "R": R,
        "risk_pct": np.where(gap_thru, med, risk_pct),
    }, index=ev.index)
