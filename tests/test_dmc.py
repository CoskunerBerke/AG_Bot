"""DMC motoru birim testleri (sentetik barlar; ağ erişimi yok)."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

import pandas as pd
import pytest

from nasdaq_bot.config import load_config
from nasdaq_bot.dmc.backtest import simulate_day
from nasdaq_bot.dmc.data import closed_only, resample
from nasdaq_bot.dmc.engine import DayEngine
from nasdaq_bot.dmc.levels import DayLevels
from nasdaq_bot.dmc.setups import Intent, pick_targets
from nasdaq_bot.dmc.structure import FVG, cisd_level, find_fvgs, ote, swing_points

DAY = date(2026, 10, 7)          # çarşamba (pazartesi/cuma kuralları devreye girmesin)


@pytest.fixture(scope="module")
def cfg():
    c = deepcopy(load_config())
    c["dmc"]["setups"]["ten_am"].update(enabled=True, entry_mode="limit")
    c["dmc"]["setups"]["lunch"]["enabled"] = True
    for k in ("kaan", "dmc_rb"):
        c["dmc"]["setups"][k]["enabled"] = False
    c["dmc"]["backtest"] = {"slippage_bps": 0.0, "fill_ticks": 1}
    return c


def bars(rows):
    """rows: [(HH:MM, o, h, l, c)] → 1 dk DataFrame."""
    idx = [pd.Timestamp(f"{DAY} {t}") for t, *_ in rows]
    return pd.DataFrame([r[1:] for r in rows], index=idx, columns=["Open", "High", "Low", "Close"]).assign(Volume=1000.0)


def flat(start, end, px=100.0, w=0.05):
    out, t = [], pd.Timestamp(f"{DAY} {start}")
    while t < pd.Timestamp(f"{DAY} {end}"):
        out.append((t.strftime("%H:%M"), px, px + w, px - w, px))
        t += pd.Timedelta(minutes=1)
    return out


# ------------------------------------------------------------------ 10AM long senaryosu
TEN_AM_ROWS = flat("09:30", "10:00") + [
    ("10:00", 100.00, 100.01, 99.96, 99.97),
    ("10:01", 99.97, 99.99, 99.95, 99.98),
    ("10:02", 99.98, 99.98, 99.90, 99.91),   # düşüş koşusu başlar → CISD = 99.98
    ("10:03", 99.91, 99.92, 99.85, 99.86),
    ("10:04", 99.86, 99.87, 99.80, 99.81),   # dip 99.80 (boğa FVG'ye dokunur)
    ("10:05", 99.81, 99.84, 99.81, 99.83),
    ("10:06", 99.83, 99.92, 99.82, 99.91),
    ("10:07", 99.91, 100.06, 99.90, 100.05),
    ("10:08", 100.05, 100.10, 100.03, 100.08),  # 3 dk kapanış CISD üstünde → armed, bacak 99.80→100.10
]


def long_levels(px=lambda x: x, kind=("bull", "bear"), bias="long"):
    lv = DayLevels(DAY, date(2026, 10, 6), pdh=px(100.30), pdl=px(99.50), pdc=px(100.0), bias=bias)
    lo, hi = sorted((px(99.70), px(99.82)))
    lv.fvgs = [FVG(kind[0], lo, hi, pd.Timestamp(f"{DAY} 09:00"), 15)]
    if px(1) != 1:                               # aynalı senaryoda pdh/pdl yer değiştirir
        lv.pdh, lv.pdl = px(99.50), px(100.30)
    return lv


def run_engine(cfg, lv, rows):
    eng = DayEngine(cfg, lv)
    out = []
    for ts, o, h, l, c, v in bars(rows)[["Open", "High", "Low", "Close", "Volume"]].itertuples(name=None):
        out += [(ts, i) for i in eng.on_bar(ts, o, h, l, c, v)]
    return out


def test_ten_am_long_limit_intent(cfg):
    out = run_engine(cfg, long_levels(), TEN_AM_ROWS)
    ints = [i for _, i in out if i.kind != "cancel"]
    assert ints, "10AM long niyeti üretilmedi"
    it = ints[0]
    pad = 2 * 100.0 / 30000                                  # 2 NQ puanı → QQQ fiyatı
    assert (it.setup, it.side, it.kind) == ("ten_am", "long", "limit")
    assert it.targets["mode"] == "ote"
    assert it.entry == pytest.approx(99.90)                  # OTE içindeki 1 dk FVG'nin yakın (üst) kenarı
    assert it.stop == pytest.approx(100.10 - 0.79 * 0.30 - pad)   # OTE 0.79 seviyesinin 2 puan ötesi
    assert it.tp1 == pytest.approx(100.00) and it.targets["tp1"] == "ten_open"
    assert it.tp2 == pytest.approx(100.10) and it.targets["tp2"] == "hod"   # >= 4R ilk büyük likidite
    assert {"ten_open_draw", "ote", "pda:fvg1m", "bias"} <= set(it.confluence)
    assert it.created == pd.Timestamp(f"{DAY} 10:09")


def test_ten_am_short_is_exact_mirror(cfg):
    m = lambda x: 200.0 - x
    rows = [(t, m(o), m(l), m(h), m(c)) for t, o, h, l, c in TEN_AM_ROWS]
    out = run_engine(cfg, long_levels(px=m, kind=("bear", "bull"), bias="short"), rows)
    ints = [i for _, i in out if i.kind != "cancel"]
    assert ints and ints[0].side == "short"
    it = ints[0]
    pad = 2 * 100.0 / 30000
    assert it.entry == pytest.approx(m(99.90))
    assert it.stop == pytest.approx(m(100.10 - 0.79 * 0.30 - pad))
    assert it.tp1 == pytest.approx(m(100.00)) and it.tp2 == pytest.approx(m(100.10))


def test_ten_am_blocked_when_bias_against(cfg):
    c = deepcopy(cfg)
    c["dmc"]["setups"]["ten_am"]["require_bias"] = "not_against"
    out = run_engine(c, long_levels(bias="short"), TEN_AM_ROWS)
    assert not [i for _, i in out if i.side == "long" and i.kind != "cancel"]


def test_no_lookahead_intents_identical_when_future_removed(cfg):
    full = run_engine(cfg, long_levels(), TEN_AM_ROWS + flat("10:09", "10:40", 100.0, 0.02))
    cut = pd.Timestamp(f"{DAY} 10:09")
    trunc = run_engine(cfg, long_levels(), TEN_AM_ROWS)
    key = lambda xs: [(ts, i.kind, i.side, round(i.entry, 4), round(i.stop, 4)) for ts, i in xs if ts < cut]
    assert key(full) == key(trunc)


# ------------------------------------------------------------------ Lunch
def test_lunch_long_on_5m_sweep(cfg):
    rows = flat("09:30", "12:00", 100.50, 0.50) + flat("12:00", "13:00", 100.55, 0.15) + [
        ("13:00", 100.45, 100.46, 100.38, 100.39),
        ("13:01", 100.39, 100.40, 100.37, 100.38),   # lunch low 100.40 süpürüldü
        ("13:02", 100.38, 100.42, 100.38, 100.41),
        ("13:03", 100.41, 100.43, 100.40, 100.42),
        ("13:04", 100.42, 100.45, 100.41, 100.44),   # 5 dk mum lunch low üstünde kapandı
    ]
    lv = DayLevels(DAY, date(2026, 10, 6), pdh=102.0, pdl=99.0, pdc=100.5, bias="long")
    c = deepcopy(cfg)
    c["dmc"]["setups"]["ten_am"]["enabled"] = False
    out = [i for _, i in run_engine(c, lv, rows)]
    assert len(out) == 1
    it = out[0]
    assert (it.setup, it.side, it.kind) == ("lunch", "long", "market")
    assert it.entry == pytest.approx(100.44)
    assert it.stop == pytest.approx(100.37 - 0.01)          # iğne ucu + 1 tick
    assert it.targets["tp2"] in ("lunch_hi", "am_hi")


# ------------------------------------------------------------------ backtest dolum kuralları
class FakeEngine:
    def __init__(self, at, intent):
        self.at, self.intent, self.fills, self.exits = at, intent, [], []

    def on_bar(self, ts, *a):
        return [self.intent] if ts == self.at else []

    def on_fill(self, it):
        self.fills.append(it)

    def on_exit(self, it, r):
        self.exits.append(r)


def test_backtest_tp1_then_breakeven(cfg):
    it = Intent("ten_am", "t1", "long", "limit", 99.89, 99.79, 100.00, 100.30,
                pd.Timestamp(f"{DAY} 10:09"), pd.Timestamp(f"{DAY} 11:30"), 100.00)
    rows = [("10:08", 100.0, 100.1, 100.0, 100.08),
            ("10:09", 99.95, 99.99, 99.87, 99.90),      # limit doldu (99.87 ≤ 99.88), TP1 iptal seviyesine değmedi
            ("10:10", 99.90, 100.01, 99.90, 100.0),     # TP1
            ("10:11", 100.0, 100.0, 99.88, 99.90)]      # BE
    eng = FakeEngine(pd.Timestamp(f"{DAY} 10:08"), it)
    tr = simulate_day(cfg, bars(rows), eng)
    assert len(tr) == 1
    assert [e[3] for e in tr[0].exits] == ["tp1", "be"]
    assert tr[0].r == pytest.approx(0.5 * (100.00 - 99.89) / 0.10)


def test_backtest_stop_wins_same_bar_ambiguity(cfg):
    it = Intent("ten_am", "t1", "long", "limit", 99.89, 99.79, 100.00, 100.30,
                pd.Timestamp(f"{DAY} 10:09"), pd.Timestamp(f"{DAY} 11:30"), 100.00)
    rows = [("10:08", 100.0, 100.1, 100.0, 100.08),
            ("10:09", 99.95, 99.99, 99.87, 99.90),
            ("10:10", 99.90, 100.40, 99.70, 100.0)]     # aynı barda stop ve TP2 → stop
    tr = simulate_day(cfg, bars(rows), FakeEngine(pd.Timestamp(f"{DAY} 10:08"), it))
    assert tr[0].exits[-1][3] == "stop" and tr[0].r == pytest.approx(-1.0)


# ------------------------------------------------------------------ primitifler
def test_resample_alignment_and_closed_only():
    b = bars(flat("09:30", "10:30"))
    b5, b60 = resample(b, 5), resample(b, 60)
    assert b5.index[0] == pd.Timestamp(f"{DAY} 09:30") and b5.index[1] == pd.Timestamp(f"{DAY} 09:35")
    assert b60.index[0] == pd.Timestamp(f"{DAY} 09:30")
    now = pd.Timestamp(f"{DAY} 09:37")
    assert closed_only(b5, 5, now).index[-1] == pd.Timestamp(f"{DAY} 09:30")


def test_fvg_swing_cisd_ote():
    b = bars([("10:00", 10, 10.5, 9.9, 10.4), ("10:01", 10.4, 11.5, 10.4, 11.4), ("10:02", 11.4, 12, 10.8, 11.9),
              ("10:03", 11.9, 11.95, 10.2, 10.3)])
    g = find_fvgs(b, 1)
    assert g[0].kind == "bull" and (g[0].bottom, g[0].top) == (10.5, 10.8)
    assert g[0].closed_through                              # 10:03 kapanışı 10.5'in altında
    sh, sl = swing_points(b, 1)
    assert sh.iloc[2] and not sh.iloc[1]
    run = bars([("10:00", 10, 10.1, 9.9, 10.05), ("10:01", 10.05, 10.06, 9.8, 9.85), ("10:02", 9.85, 9.86, 9.6, 9.65)])
    assert cisd_level(run, run.index[-1]) == 10.05
    z = ote(9.0, 10.0)
    assert z["entry"] == pytest.approx(9.295) and z["zone_bottom"] == pytest.approx(9.21)


def test_pick_targets_rr_rules():
    tp1, tp2 = pick_targets(100.0, 99.9, {"a": 100.05, "b": 100.15, "ih": 100.4, "c": 100.35}, {"ih"}, 1.0, 3.0)
    assert tp1 == ("b", 100.15) and tp2 == ("c", 100.35)


def test_rejection_block_body_to_wick_and_sweep():
    from nasdaq_bot.dmc.structure import find_rbs, stdv_levels
    b = bars([("10:00", 100.0, 100.2, 99.9, 100.1),
              ("10:01", 100.1, 100.15, 99.80, 100.0),    # swing low A
              ("10:02", 100.0, 100.3, 99.95, 100.25),
              ("10:03", 100.25, 100.3, 100.0, 100.05),
              ("10:04", 100.05, 100.1, 99.70, 100.02),   # swing low B, A'yı süpürür → RB [99.70, 100.02]
              ("10:05", 100.02, 100.4, 100.01, 100.35),
              ("10:06", 100.35, 100.4, 100.1, 100.3),    # bölgeden çıktı
              ("10:07", 100.3, 100.3, 100.0, 100.1)])    # ilk temas
    rbs = [r for r in find_rbs(b, 1) if r.kind == "bull" and r.swept]
    assert len(rbs) == 1
    r = rbs[0]
    assert (r.lo, r.hi, r.body, r.wick) == (99.70, 100.02, 100.02, 99.70)
    assert r.mid == pytest.approx(99.86) and r.touches == 1 and not r.invalid
    assert r.known_at == pd.Timestamp(f"{DAY} 10:06")
    assert stdv_levels(100.0, 99.0) == {-1.0: 98.0, -2.0: 97.0, -2.5: 96.5, -4.0: 95.0}


def test_live_bp_split_unique_ids_and_cancel(cfg, tmp_path):
    """Canlı: alım gücü eş zamanlı emirlere bölünür, emir kimliği sembol içerir, limit dolunca bekleyenler iptal."""
    from nasdaq_bot.dmc.live import DmcLive, size_position
    assert size_position(100_000, 400_000, 100.0, 99.9, 0.43, 0.9) == 3600            # tek emir: BP tavanı
    assert size_position(100_000, 400_000, 100.0, 99.9, 0.43, 0.9, 400_000, 4) == 900  # 4'e bölünmüş
    assert size_position(100_000, 50_000, 100.0, 99.9, 0.43, 0.9, 400_000, 4) == 450   # kalan BP daha az

    class FakeBroker:
        def __init__(self):
            self.sent, self.cancelled = [], []

        def account(self):
            return {"equity": "100000", "buying_power": "400000", "multiplier": "4"}

        def submit(self, body):
            self.sent.append(body)
            return {"id": f"o{len(self.sent)}"}

        def cancel_order(self, oid):
            self.cancelled.append(oid)
            return True

    c = deepcopy(cfg)
    c["state_dir"] = str(tmp_path)
    c["dmc"]["bp_slots"] = 4
    br, shared = FakeBroker(), {"trades": 0, "open": set(), "day": None}
    bots = [DmcLive(c, br, symbol=s, shared=shared) for s in ("QQQ", "SPY")]
    it = Intent("dmc_rb", "dmc_rb-long-1", "long", "limit", entry=100.0, stop=99.9, tp1=None, tp2=101.0)
    for b in bots:
        b.day, b.st = DAY, {"day": DAY.isoformat(), "tags": {}, "filled_setups": [], "flat_done": False}
        b._submit(it, ("k",), 1)
    ids = [x["client_order_id"] for x in br.sent]
    assert len(set(ids)) == 2 and "-QQQ-" in ids[0] and "-SPY-" in ids[1]
    assert all(x["qty"] == "900" for x in br.sent)
    bots[1].cancel_pending("test")
    assert br.cancelled == ["o2"] and bots[1].st["tags"]["dmc_rb-long-1"]["orders"] == []
    # Aynı sembolde ters yön: Alpaca ikisini birden bekletmez → yakın olan gönderilir, diğeri sırada bekler
    q = bots[0]
    q.events = []                                                 # haber verisi var, kırmızı haber yok
    q.bars = pd.DataFrame({"Close": [100.5]}, index=[pd.Timestamp("2026-10-07 10:05")])
    short = Intent("dmc_rb", "dmc_rb-short-1", "short", "limit", entry=102.0, stop=102.1, tp1=None, tp2=101.0)
    n = len(br.sent)
    q._submit(short, ("s",), 1)                                   # long 100.0 daha yakın → short sırada
    assert len(br.sent) == n and q.st["tags"]["dmc_rb-short-1"]["wait"]
    q.bars = pd.DataFrame({"Close": [101.8]}, index=[pd.Timestamp("2026-10-07 10:20")])
    q._retry_waiting(pd.Timestamp("2026-10-07 10:21"))            # fiyat short'a yaklaştı → long iptal, short gönderilir
    assert br.cancelled[-1] == "o1" and q.st["tags"]["dmc_rb-long-1"]["wait"]
    assert br.sent[-1]["side"] == "sell" and q.st["tags"]["dmc_rb-short-1"]["orders"]
    # Fiyat girişi geçmişse limit gönderilmez (anında piyasadan dolup stopa yapışmasın)
    q.bars = pd.DataFrame({"Close": [99.9], "Low": [99.8], "High": [100.2]}, index=[pd.Timestamp("2026-10-07 10:30")])
    n = len(br.sent)
    late = Intent("ten_am", "ten_am-long-1", "long", "limit", entry=100.0, stop=99.7, tp1=None, tp2=101.0,
                  created=pd.Timestamp("2026-10-07 10:29"))
    q._submit(late, ("l",), 1)
    assert len(br.sent) == n and q._traded_through(late)
