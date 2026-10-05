"""Strateji Öğrenme (Walk-Forward) - Rejection Block taktiğini geçmiş veriyle mükemmelleştirme.

AMAÇ: Tek bir taktiği (RB) alıp binlerce varyasyonunu geçmiş veride denemek ve EZBERLEMEDEN
(overfitting olmadan) en iyisini bulmak.

YÖNTEM
  1. Olaylar : NASDAQ-100'deki tüm RB bölgeleri, zengin özelliklerle (rb_engine.extract_events).
  2. Kurallar: giriş modu x RR x stop tamponu x tutma süresi x seans sonu kapama x uzaklaşma
               x filtreler (fitil oranı, trend, likidite süpürme, yön, hisse geçmişi)
               = 1h için ~46.000 kombinasyon.
  3. WALK-FORWARD: zaman ileri doğru kaydırılır. Her adımda SADECE o tarihe kadar KAPANMIŞ
               işlemlerle en iyi kombinasyon seçilir, sonra hiç görmediği SONRAKİ dönemde işlem
               yapılır. Raporlanan sonuçlar yalnızca bu "görmediği dönem" (out-of-sample)
               sonuçlarıdır; gerçek hayatta beklenebilecek performansın en dürüst tahmini budur.
  4. MAKİNE ÖĞRENMESİ (meta-labeling): seçilen kuralın ürettiği işlemler arasından hangisinin
               kazanacağını tahmin eden bir model (gradient boosting) yine walk-forward eğitilir;
               düşük olasılıklı işlemler elenir. ML'nin işe yarayıp yaramadığı da OOS ile ölçülür.
  5. HİSSEYE ÖZEL ÖĞRENME: her işlemde "bu hissede geçmiş RB işlemleri ne kadar kazandırdı"
               (sadece o ana kadar kapanmış işlemler) özellik/filtre olarak kullanılır.
  6. Son model tüm geçmişle eğitilip state/learned/ altına kaydedilir (canlı kullanım için).
"""
from __future__ import annotations

import itertools
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .history import history_summary, update_history
from .indicators import add_indicators
from .intraday import session_only
from .rb_engine import FEATURES, extract_events, simulate, ticker_arrays

TF_SPEC = {
    #       tutma (bar)       seans sonu kapama     ilk eğitim / test penceresi (gün)   min işlem
    "1d": {"horizons": [5, 10, 20], "flat": [False], "train_days": 3 * 365, "test_days": 365, "min_train": 120},
    "1h": {"horizons": [7, 14, 28], "flat": [True, False], "train_days": 300, "test_days": 60, "min_train": 150},
    "15m": {"horizons": [16, 32], "flat": [True], "train_days": 30, "test_days": 10, "min_train": 100},
    "5m": {"horizons": [24, 48], "flat": [True], "train_days": 30, "test_days": 10, "min_train": 100},
}
GRID = {"depart": [0.5, 1.0, 1.5], "mode": ["edge", "mid", "close"], "rr": [1.0, 1.5, 2.0, 3.0], "buf": [0.05, 0.25]}
STATIC_FILTERS = {"wick": [0.3, 0.5, 0.65], "trend": ["any", "with", "against"], "sweep": ["any", "only"],
                  "side": ["both", "long", "short"]}
MODE_TR = {"edge": "bölge kenarına limit emir", "mid": "fitilin ortasına (%50) limit emir",
           "close": "ret kapanışı sonrası piyasa emri"}
DAY_NS = 86_400 * 10**9


# ----------------------------------------------------------------------------- yardımcılar
def _ticker_history(tk: np.ndarray, entry: np.ndarray, exit_: np.ndarray, R: np.ndarray):
    """Her işlem için: aynı hissede, bu işlemin girişinden ÖNCE kapanmış işlemlerin ort. R'si ve sayısı."""
    hist_r = np.zeros(len(tk), dtype=np.float32)
    hist_n = np.zeros(len(tk), dtype=np.float32)
    ok = np.isfinite(R)
    for t in np.unique(tk[ok]):
        m = np.where((tk == t) & ok)[0]
        order = np.argsort(exit_[m], kind="stable")
        ex_sorted = exit_[m][order]
        csum = np.r_[0.0, np.cumsum(np.clip(R[m][order], -3, 5))]
        cnt = np.searchsorted(ex_sorted, entry[m], side="left")
        hist_n[m] = cnt
        hist_r[m] = np.where(cnt > 0, csum[cnt] / np.maximum(cnt, 1), 0.0)
    return hist_r, hist_n


def _static_masks(ev: pd.DataFrame):
    """Olay özelliklerine bağlı filtreler (her uzaklaşma değeri için bir kez)."""
    keys, rows = [], []
    w = ev["wick_ratio"].to_numpy()
    tr = ev["trend50_al"].to_numpy()
    sw = ev["sweep"].to_numpy() == 1
    sd = ev["side"].to_numpy()
    for wk, tn, sp, si in itertools.product(*STATIC_FILTERS.values()):
        m = w >= wk
        if tn == "with":
            m = m & (tr > 0)
        elif tn == "against":
            m = m & (tr < 0)
        if sp == "only":
            m = m & sw
        if si == "long":
            m = m & (sd > 0)
        elif si == "short":
            m = m & (sd < 0)
        keys.append({"wick": wk, "trend": tn, "sweep": sp, "side": si})
        rows.append(m)
    return keys, np.vstack(rows)


def _stats(x: np.ndarray) -> dict:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": np.nan, "win": np.nan, "pf": np.nan, "t": np.nan}
    sd = x.std(ddof=1) if n > 1 else np.nan
    gains, losses = x[x > 0].sum(), -x[x < 0].sum()
    return {"n": n, "mean": x.mean(), "win": (x > 0).mean(), "pf": gains / losses if losses > 0 else np.inf,
            "t": x.mean() / (sd / math.sqrt(n)) if n > 1 and sd > 0 else 0.0}


def describe(c: dict) -> str:
    f = c["filters"]
    parts = [MODE_TR[c["mode"]], f"stop fitil ucu ± {c['buf']} ATR", f"hedef {c['rr']:g}R",
             f"en fazla {c['horizon']} bar", "seans sonunda kapat" if c["flat"] else "gece taşıyabilir",
             f"uzaklaşma ≥ {c['depart']} ATR", f"fitil ≥ %{int(f['wick'] * 100)}",
             {"with": "sadece trend yönünde", "against": "sadece trend tersine"}.get(f["trend"], ""),
             "sadece likidite süpürmeli" if f["sweep"] == "only" else "",
             {"long": "sadece LONG", "short": "sadece SHORT"}.get(f["side"], ""),
             "sadece RB'nin geçmişte kazandırdığı hisseler" if f["tick"] == "pos" else ""]
    return " · ".join(p for p in parts if p)


# ----------------------------------------------------------------------------- ana süreç
class RBLearner:
    def __init__(self, cfg: dict, tf: str, tickers: list[str], log=print, ml: bool = True):
        self.cfg, self.tf, self.tickers, self.log, self.use_ml = cfg, tf, tickers, log, ml
        self.spec = TF_SPEC[tf]
        b = cfg["backtest"]
        self.comm, self.slip = b["commission_bps"] / 1e4, b["slippage_bps"] / 1e4
        self.intraday = tf != "1d"
        rj = cfg.get("rejection", {})
        self.k, self.max_age = int(rj.get("pivot_k", 3)), int(rj.get("max_age", 60))
        self._res_cache: dict[int, pd.DataFrame] = {}

    # -- 1) veri + olaylar
    def load(self):
        data = update_history(self.cfg, self.tickers + ["QQQ"], self.tf)
        q = data.pop("QQQ", None)
        self.log(f"Veri: {history_summary(data)}")
        if q is not None and self.intraday:
            q = session_only(q)
        elif q is not None and self.tf == "1d":
            q = q.loc[q.index >= "2016-01-01"]
        self.qqq = add_indicators(q) if q is not None else None
        self.frames = {}
        for t, df in data.items():
            if self.intraday:
                df = session_only(df)
            elif self.tf == "1d":
                df = df.loc[df.index >= "2016-01-01"]
            if len(df) >= 300:
                self.frames[t] = add_indicators(df)
        self.arrays = {t: ticker_arrays(d) for t, d in self.frames.items()}
        self.days = pd.DatetimeIndex(sorted({x for d in self.frames.values() for x in d.index.normalize().unique()}))

    def build_events(self):
        self.events, self.static = {}, {}
        for dep in GRID["depart"]:
            parts = []
            for t, d in self.frames.items():
                ev = extract_events(d, self.qqq, k=self.k, depart_atr=dep, max_age=self.max_age)
                if len(ev):
                    ev["ticker"] = t
                    parts.append(ev)
            ev = pd.concat(parts, ignore_index=True)
            self.events[dep] = ev
            self.static[dep] = _static_masks(ev)
        self.log("Olaylar: " + ", ".join(f"uzaklaşma {k} ATR → {len(v):,} RB bölgesi" for k, v in self.events.items()))

    # -- 2) tüm kural kombinasyonlarını simüle et
    def _run(self, dep, mode, rr, buf, hz, flat) -> pd.DataFrame:
        ev = self.events[dep]
        res = pd.concat([simulate(self.arrays[t], g, mode, rr, buf, hz, flat, self.comm, self.slip)
                         for t, g in ev.groupby("ticker")])
        return res.reindex(range(len(ev)))

    def simulate_all(self, progress=None):
        self.sims = []
        combos = list(itertools.product(GRID["mode"], GRID["rr"], GRID["buf"], self.spec["horizons"], self.spec["flat"]))
        for dep, ev in self.events.items():
            tk = ev["ticker"].to_numpy()
            for mode, rr, buf, hz, flat in combos:
                res = self._run(dep, mode, rr, buf, hz, flat)
                entry = res["entry_time"].to_numpy("datetime64[ns]").astype("int64")
                exit_ = res["exit_time"].to_numpy("datetime64[ns]").astype("int64")
                net, R = res["net"].to_numpy(float), res["R"].to_numpy(float)
                hr, hn = _ticker_history(tk, entry, exit_, R)
                self.sims.append({"depart": dep, "mode": mode, "rr": rr, "buf": buf, "horizon": hz, "flat": flat,
                                  "entry": entry, "exit": exit_, "net": net, "R": R.astype(np.float32),
                                  "hist_r": hr, "hist_n": hn, "pos": (hr > 0) & (hn >= 3)})
                if progress:
                    progress()
        nf = len(self.static[GRID["depart"][0]][0]) * 2
        self.log(f"Simülasyon: {len(self.sims)} kural seti x {nf} filtre = {len(self.sims) * nf:,} kombinasyon")

    def mask(self, si: int, fi: int) -> np.ndarray:
        s = self.sims[si]
        keys, M = self.static[s["depart"]]
        m = M[fi % len(keys)] & np.isfinite(s["net"])
        return m & s["pos"] if fi >= len(keys) else m

    def fkey(self, si: int, fi: int) -> dict:
        keys = self.static[self.sims[si]["depart"]][0]
        return {**keys[fi % len(keys)], "tick": "pos" if fi >= len(keys) else "any"}

    # -- seçim: cutoff'tan önce KAPANMIŞ işlemlerle en iyi kombinasyon (t-istatistiği)
    def select(self, cutoff: int):
        best = None
        min_n = self.spec["min_train"]
        for si, s in enumerate(self.sims):
            tw = (s["exit"] < cutoff) & np.isfinite(s["net"])
            if tw.sum() < min_n:
                continue
            _, M = self.static[s["depart"]]
            x = np.where(tw, s["net"], 0.0)
            ns, s1s, s2s = [], [], []
            for extra in (tw, tw & s["pos"]):
                Mm = (M & extra[None, :]).astype(np.float64)
                ns.append(Mm.sum(1))
                s1s.append(Mm @ x)
                s2s.append(Mm @ (x * x))
            n, s1, s2 = (np.concatenate(v) for v in (ns, s1s, s2s))
            with np.errstate(invalid="ignore", divide="ignore"):
                mean = s1 / n
                var = (s2 - n * mean ** 2) / (n - 1)
                t = mean / np.sqrt(var / n)
            t = np.where((n >= min_n) & (mean > 0) & np.isfinite(t), t, -np.inf)
            fi = int(np.argmax(t))
            if np.isfinite(t[fi]) and (best is None or t[fi] > best["t"]):
                best = {"si": si, "fi": fi, "t": float(t[fi]), "n": int(n[fi]), "mean": float(mean[fi])}
        return best

    def cfg_of(self, si: int, fi: int) -> dict:
        s = self.sims[si]
        return {"depart": s["depart"], "mode": s["mode"], "rr": s["rr"], "buf": s["buf"], "horizon": s["horizon"],
                "flat": s["flat"], "filters": self.fkey(si, fi)}

    # -- ML (meta-labeling)
    def _X(self, si: int, idx: np.ndarray) -> np.ndarray:
        s = self.sims[si]
        X = self.events[s["depart"]].iloc[idx].copy()
        X["tick_hist_r"] = s["hist_r"][idx]
        X["tick_hist_n"] = s["hist_n"][idx]
        return X[FEATURES].fillna(0.0).to_numpy(float)

    @staticmethod
    def _model():
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=40,
                                              l2_regularization=1.0, random_state=0)

    def fit_ml(self, si: int, train_idx: np.ndarray):
        """Eğitimin son %25'iyle (zaman sırası) eşik seçer, sonra tüm eğitimle modeli kurar."""
        s = self.sims[si]
        if len(train_idx) < 300:
            return None
        order = train_idx[np.argsort(s["entry"][train_idx], kind="stable")]
        y_all = (s["net"][order] > 0).astype(int)
        cut = int(len(order) * 0.75)
        if y_all[:cut].min() == y_all[:cut].max() or y_all.min() == y_all.max():
            return None
        m = self._model().fit(self._X(si, order[:cut]), y_all[:cut])
        pv = m.predict_proba(self._X(si, order[cut:]))[:, 1]
        netv = s["net"][order[cut:]]
        best_q, best_score = 0.0, netv.mean()
        for q in (0.3, 0.5, 0.7):
            sel = pv >= np.quantile(pv, q)
            if sel.sum() >= 50 and netv[sel].mean() > best_score:
                best_q, best_score = q, netv[sel].mean()
        model = self._model().fit(self._X(si, order), y_all)
        p_tr = model.predict_proba(self._X(si, order))[:, 1]
        thr = float(np.quantile(p_tr, best_q)) if best_q > 0 else 0.0
        return {"model": model, "thr": thr, "q": best_q}

    # -- 3) walk-forward
    def walk_forward(self):
        s0 = self.sims[0]
        fin = np.isfinite(s0["net"])
        t0, t1 = int(s0["entry"][fin].min()), int(s0["entry"][fin].max())
        start = t0 + self.spec["train_days"] * DAY_NS
        step = self.spec["test_days"] * DAY_NS
        raw_si = next(i for i, s in enumerate(self.sims) if s["mode"] == "edge" and s["rr"] == 1.5
                      and s["buf"] == 0.05 and s["depart"] == 1.0)
        folds, oos_rule, oos_ml, oos_raw = [], [], [], []
        while start <= t1:
            end = start + step
            row = {"start": pd.Timestamp(start), "end": pd.Timestamp(min(end, t1))}
            rs = self.sims[raw_si]
            raw = np.where(np.isfinite(rs["net"]) & (rs["entry"] >= start) & (rs["entry"] < end))[0]
            oos_raw.append(self.trades(raw_si, raw))
            best = self.select(start)
            if best is None:
                row["cfg"] = None
                folds.append(row)
                start = end
                continue
            si, fi = best["si"], best["fi"]
            s, m = self.sims[si], self.mask(si, fi)
            ti = np.where(m & (s["entry"] >= start) & (s["entry"] < end))[0]
            oos_rule.append(self.trades(si, ti))
            ml_st = None
            if self.use_ml:
                fitted = self.fit_ml(si, np.where(m & (s["exit"] < start))[0])
                if fitted is not None and len(ti):
                    p = fitted["model"].predict_proba(self._X(si, ti))[:, 1]
                    keep = p >= fitted["thr"]
                    oos_ml.append(self.trades(si, ti[keep], prob=p[keep]))
                    ml_st = _stats(s["net"][ti[keep]])
                else:
                    oos_ml.append(self.trades(si, ti))
                    ml_st = _stats(s["net"][ti])
            row.update({"cfg": self.cfg_of(si, fi), "train_t": best["t"], "train_n": best["n"],
                        "train_mean": best["mean"], "test": _stats(s["net"][ti]), "test_ml": ml_st})
            folds.append(row)
            start = end
        cat = lambda L: pd.concat([x for x in L if len(x)], ignore_index=True) if any(len(x) for x in L) else pd.DataFrame()  # noqa: E731
        self.folds, self.oos_rule, self.oos_ml, self.oos_raw = folds, cat(oos_rule), cat(oos_ml), cat(oos_raw)
        self.oos_days = self.days[(self.days >= pd.Timestamp(t0 + self.spec["train_days"] * DAY_NS).normalize())]

    def trades(self, si: int, idx: np.ndarray, prob=None) -> pd.DataFrame:
        if si not in self._res_cache:
            s = self.sims[si]
            self._res_cache[si] = self._run(s["depart"], s["mode"], s["rr"], s["buf"], s["horizon"], s["flat"])
        res = self._res_cache[si]
        ev = self.events[self.sims[si]["depart"]]
        df = res.iloc[idx][["entry_time", "exit_time", "entry", "stop", "target", "exit_px", "reason",
                            "net", "R", "risk_pct"]].copy()
        df.insert(0, "ticker", ev["ticker"].to_numpy()[idx])
        df.insert(1, "yon", np.where(ev["side"].to_numpy()[idx] > 0, "LONG", "SHORT"))
        if prob is not None:
            df["ml_olasilik"] = prob
        return df

    # -- 4) son model (tüm geçmiş) + kayıt
    def final_fit(self) -> dict:
        s0 = self.sims[0]
        now = int(s0["exit"][np.isfinite(s0["net"])].max()) + 1
        best = self.select(now)
        self.final, self.final_model = {}, None
        if best is None:
            return {}
        si, fi = best["si"], best["fi"]
        self.final = {"cfg": self.cfg_of(si, fi), "aciklama": describe(self.cfg_of(si, fi)),
                      "train_t": best["t"], "train_n": best["n"], "train_mean_pct": best["mean"] * 100}
        if self.use_ml:
            fitted = self.fit_ml(si, np.where(self.mask(si, fi))[0])
            if fitted is not None:
                self.final_model = fitted
                self.final["ml_threshold"] = fitted["thr"]
                self.final["ml_quantile"] = fitted["q"]
        return self.final

    def save(self, summary: dict) -> Path:
        d = Path(self.cfg["state_dir"]) / "learned"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"rb_{self.tf}.json"
        payload = {"generated": datetime.now().isoformat(timespec="minutes"), "tf": self.tf,
                   "tickers": len(self.frames), **self.final, "oos": summary, "features": FEATURES}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        if self.final_model is not None:
            import joblib
            joblib.dump(self.final_model, d / f"rb_{self.tf}_model.joblib")
        return path


def daily_metrics(trades: pd.DataFrame, days: pd.DatetimeIndex, risk_per_trade: float = 0.005) -> dict:
    """Her işlemde sermayenin %0.5'i riske edilirse günlük getiri profili (R bazlı, basitleştirilmiş)."""
    if trades is None or trades.empty:
        return {}
    d = trades.assign(day=pd.to_datetime(trades["exit_time"]).dt.normalize())
    daily = (d.groupby("day")["R"].sum() * risk_per_trade * 100).reindex(days, fill_value=0.0)
    eq = (1 + daily / 100).cumprod()
    return {"gun": len(daily), "ort_gunluk%": daily.mean(), "%1+_gun%": (daily >= 1).mean() * 100,
            "zararli_gun%": (daily < 0).mean() * 100, "toplam%": (eq.iloc[-1] - 1) * 100,
            "maks_dusus%": ((eq / eq.cummax()) - 1).min() * 100, "islem/gun": len(trades) / max(len(daily), 1)}
