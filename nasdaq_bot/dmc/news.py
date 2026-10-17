"""ForexFactory canlı takvim, veri okuyucu ve makro yön motoru (https://www.forexfactory.com/).

Özellikler:
1. Doğrudan `https://www.forexfactory.com/` ve `https://www.forexfactory.com/calendar` üzerinden
   haftalık/günlük ekonomik takvimi, beklentiyi (`forecast`), önceki veriyi (`previous`) ve
   açıklanan anlık veriyi (`actual`, `actualBetterWorse`) çeker.
2. Yedek olarak `https://nfs.faireconomy.media/ff_calendar_thisweek.xml` beslemesini destekler.
3. Kırmızı haber / Fed konuşması anında (`blocked`) işlem açılmasını engeller; veri açıklandıktan
   hemen sonra ise verinin içeriğine göre (`macro_signal_for_symbol` & `active_macro_bias`)
   endeksler (`QQQ`, `TQQQ`, `SPY`, `DIA`) ile değerli metaller (`GLD`, `SLV`) için kurumsal
   alım/satım yönünü (`long` / `short`) üretir.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from ..common import NY

log = logging.getLogger(__name__)
FF_HOME_URL = "https://www.forexfactory.com/"
FF_CALENDAR_URL = "https://www.forexfactory.com/calendar"
URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
SPEECH_WORDS = ("speaks", "press conference", "testifies", "testimony", "fomc statement")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

INFLATION_WORDS = (
    "cpi", "ppi", "pce", "price index", "inflation",
    "federal funds rate", "hourly earnings", "employment cost",
)
GROWTH_WORDS = (
    "ism", "pmi", "retail sales", "gdp", "consumer confidence",
    "consumer sentiment", "uom", "industrial production", "durable goods",
    "empire state", "philly fed", "existing home", "new home", "building permits",
)
LABOR_WORDS = (
    "non-farm", "nonfarm", "unemployment", "jobless", "jolts", "adp", "employment change",
)
INVERSE_BETTER_WORDS = (
    "unemployment claims", "jobless claims", "unemployment rate",
)


def _parse_num(val: str) -> float | None:
    if not val:
        return None
    s = re.sub(r"[^0-9.\-+]", "", str(val).strip())
    if not s or s in ("-", "+", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class Event:
    t: pd.Timestamp        # NY, tz-naive
    title: str
    impact: str            # High | Medium | Low | Holiday
    actual: str = ""
    forecast: str = ""
    previous: str = ""
    revision: str = ""
    better_worse: int = 0  # 1 = USD için beklentiden iyi (yeşil), 2 = beklentiden kötü (kırmızı), 0 = nötr
    currency: str = "USD"
    event_id: int = 0

    @property
    def speech(self) -> bool:
        return any(w in self.title.lower() for w in SPEECH_WORDS)

    @property
    def has_actual(self) -> bool:
        return bool(self.actual and str(self.actual).strip())

    @property
    def category(self) -> str:
        tl = self.title.lower()
        if self.speech:
            return "speech"
        if any(w in tl for w in INFLATION_WORDS):
            return "inflation_rate"
        if any(w in tl for w in GROWTH_WORDS):
            return "growth_demand"
        if any(w in tl for w in LABOR_WORDS):
            return "labor"
        return "other"

    def resolved_better_worse(self) -> int:
        """ForexFactory `actualBetterWorse` (1=iyi, 2=kötü) veya sayısal `actual` vs `forecast` karşılaştırması."""
        if self.better_worse in (1, 2):
            return self.better_worse
        if self.better_worse == -1:
            return 2
        act = _parse_num(self.actual)
        fc = _parse_num(self.forecast)
        if act is None or fc is None or abs(act - fc) < 1e-9:
            return 0
        tl = self.title.lower()
        if any(w in tl for w in INVERSE_BETTER_WORDS):
            return 1 if act < fc else 2
        return 1 if act > fc else 2


def macro_signal_for_symbol(ev: Event, symbol: str) -> str:
    """Açıklanan ForexFactory verisinin (`ev`) ilgili sembol (`QQQ`, `TQQQ`, `SPY`, `DIA`, `GLD`, `SLV`)
    üzerindeki kurumsal yön etkisini (`long`, `short`, `neutral`) döndürür.
    """
    if not ev.has_actual or ev.impact not in ("High", "Medium"):
        return "neutral"
    bw = ev.resolved_better_worse()
    if bw not in (1, 2):
        return "neutral"
    sym = symbol.upper()
    is_metal = sym in ("GLD", "SLV", "GC", "SI")
    cat = ev.category

    # 1) Enflasyon ve Faiz verileri (CPI, PPI, PCE, Fed Rate, Hourly Earnings):
    #    Yüksek enflasyon (bw == 1) -> Şahin Fed -> Hisse ve Altın/Gümüş için SHORT
    #    Düşük enflasyon (bw == 2) -> Güvercin Fed / Faiz indirimi -> Hisse ve Altın/Gümüş için LONG
    if cat == "inflation_rate":
        return "short" if bw == 1 else "long"

    # 2) Büyüme, Tüketim ve Sanayi verileri (ISM PMI, Retail Sales, GDP, Consumer Confidence, Philly Fed):
    #    Güçlü büyüme (bw == 1) -> Endeksler (QQQ/TQQQ/SPY/DIA) için LONG, DXY güçlendiği için GLD/SLV için SHORT
    #    Zayıf büyüme (bw == 2) -> Endeksler için SHORT, güvenli liman / zayıf DXY ile GLD/SLV için LONG
    if cat == "growth_demand":
        if is_metal:
            return "short" if bw == 1 else "long"
        return "long" if bw == 1 else "short"

    # 3) İstihdam verileri (NFP, Unemployment Claims, JOLTS, ADP):
    if cat == "labor":
        if is_metal:
            return "short" if bw == 1 else "long"
        return "long" if bw == 1 else "short"

    # Diğer ABD verileri:
    if is_metal:
        return "short" if bw == 1 else "long"
    return "long" if bw == 1 else "short"


def active_macro_bias(
    events: list[Event] | None,
    now: pd.Timestamp,
    symbol: str,
    lookback_minutes: int = 120,
) -> tuple[str, Event | None]:
    """`now` anında son açıklanan yüksek/orta etkili ForexFactory verilerinden net makro yönü hesaplar.
    Sabah 08:30 (Premarket) verilerinin etkisi 09:30–11:15 açılış seansı boyunca devam eder.
    """
    if not events:
        return "neutral", None
    score = 0
    latest_ev: Event | None = None
    for ev in events:
        if ev.t.date() != now.date() or not ev.has_actual:
            continue
        if ev.impact not in ("High", "Medium"):
            continue
        mins_since = (now - ev.t).total_seconds() / 60.0
        # 08:30 premarket verisi için RTH 11:15'e kadar (165 dk) pencere tanı
        max_lb = 165.0 if ev.t.hour == 8 else float(lookback_minutes)
        if not (2.0 <= mins_since <= max_lb):
            continue
        sig = macro_signal_for_symbol(ev, symbol)
        if sig == "neutral":
            continue
        weight = 2 if ev.impact == "High" else 1
        score += weight if sig == "long" else -weight
        if latest_ev is None or (ev.impact == "High" and latest_ev.impact != "High") or ev.t >= latest_ev.t:
            latest_ev = ev
    if score > 0:
        return "long", latest_ev
    if score < 0:
        return "short", latest_ev
    return "neutral", latest_ev


def parse_forexfactory_html(html: str, currency: str = "USD") -> list[Event]:
    """`https://www.forexfactory.com/` veya `/calendar` sayfasındaki gömülü `days: [...]` JSON verisini çözer."""
    idx = html.find("days: [")
    if idx < 0:
        return []
    dec = json.JSONDecoder()
    try:
        days, _ = dec.raw_decode(html[idx + 6:])
    except json.JSONDecodeError:
        return []
    impact_map = {"high": "High", "medium": "Medium", "low": "Low", "holiday": "Holiday"}
    out: list[Event] = []
    for d in days:
        for ev in d.get("events") or []:
            cur = (ev.get("currency") or "").strip()
            if currency and cur != currency:
                continue
            dl = ev.get("dateline")
            if not dl:
                continue
            try:
                ts_ny = pd.Timestamp(
                    datetime.fromtimestamp(int(dl), tz=timezone.utc).astimezone(NY).replace(tzinfo=None)
                )
            except (ValueError, OSError):
                continue
            imp_raw = (ev.get("impactName") or "low").lower()
            out.append(
                Event(
                    t=ts_ny,
                    title=(ev.get("name") or "").strip(),
                    impact=impact_map.get(imp_raw, "Low"),
                    actual=str(ev.get("actual") or "").strip(),
                    forecast=str(ev.get("forecast") or "").strip(),
                    previous=str(ev.get("previous") or "").strip(),
                    revision=str(ev.get("revision") or "").strip(),
                    better_worse=int(ev.get("actualBetterWorse") or 0),
                    currency=cur or currency,
                    event_id=int(ev.get("id") or 0),
                )
            )
    return out


def parse(xml: bytes, currency: str = "USD") -> list[Event]:
    out = []
    for ev in ET.fromstring(xml).findall("event"):
        if ev.findtext("country", "") != currency:
            continue
        d, t = ev.findtext("date", ""), (ev.findtext("time", "") or "").strip()
        try:
            utc = datetime.strptime(f"{d} {t}", "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append(
            Event(
                t=pd.Timestamp(utc.astimezone(NY).replace(tzinfo=None)),
                title=ev.findtext("title", ""),
                impact=ev.findtext("impact", "Low"),
                actual=(ev.findtext("actual", "") or "").strip(),
                forecast=(ev.findtext("forecast", "") or "").strip(),
                previous=(ev.findtext("previous", "") or "").strip(),
                currency=currency,
            )
        )
    return out


def fetch_events(
    state_dir: str | Path,
    currency: str = "USD",
    today_only: bool = False,
) -> list[Event] | None:
    """Doğrudan `https://www.forexfactory.com/calendar` (veya anlık güncelleme için `https://www.forexfactory.com/`)
    üzerinden olayları ve açıklanan (`actual`) verileri çeker; ağ hatasında XML ve yerel önbelleğe başvurur.
    """
    state_path = Path(state_dir)
    json_cache = state_path / "dmc_ff_live.json"
    xml_cache = state_path / "dmc_ff_calendar.xml"

    by_key: dict[tuple[str, str], Event] = {}

    # Önce mevcut JSON önbelleğini yükle (varsa haftalık takvim temelini korur)
    if json_cache.exists():
        try:
            raw_list = json.loads(json_cache.read_text(encoding="utf-8"))
            for item in raw_list:
                ev = Event(**{**item, "t": pd.Timestamp(item["t"])})
                if not currency or ev.currency == currency:
                    by_key[(str(ev.t), ev.title)] = ev
        except Exception:
            pass

    urls = [FF_HOME_URL] if (today_only and by_key) else [FF_CALENDAR_URL, FF_HOME_URL]
    fetched_any = False
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=10)
            if r.status_code == 200 and "days: [" in r.text:
                evs = parse_forexfactory_html(r.text, currency=currency)
                if evs:
                    fetched_any = True
                    for ev in evs:
                        by_key[(str(ev.t), ev.title)] = ev
        except requests.RequestException as e:
            log.warning("ForexFactory canlı bağlantı uyarısı (%s): %s", u, e)

    if fetched_any and by_key:
        try:
            state_path.mkdir(parents=True, exist_ok=True)
            serializable = [{**asdict(e), "t": str(e.t)} for e in sorted(by_key.values(), key=lambda x: x.t)]
            json_cache.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.warning("ForexFactory JSON önbellek yazılamadı: %s", e)
        return sorted(by_key.values(), key=lambda x: x.t)

    # Yedek: XML beslemesi (faireconomy)
    xml = None
    try:
        r = requests.get(URL, headers=HEADERS, timeout=10)
        if r.status_code == 200 and len(r.content) > 100:
            xml = r.content
            state_path.mkdir(parents=True, exist_ok=True)
            xml_cache.write_bytes(xml)
    except requests.RequestException as e:
        log.warning("ForexFactory XML alınamadı: %s", e)
    if xml is None and xml_cache.exists():
        xml = xml_cache.read_bytes()
    if xml is not None:
        try:
            evs = parse(xml, currency)
            for ev in evs:
                by_key.setdefault((str(ev.t), ev.title), ev)
        except ET.ParseError as e:
            log.warning("ForexFactory XML bozuk: %s", e)

    if by_key:
        return sorted(by_key.values(), key=lambda x: x.t)
    return None


def blocked(events: list[Event] | None, now: pd.Timestamp, minutes: int, speech_minutes: int = 30) -> str | None:
    """`now` bir kırmızı haberin ±minutes, ya da konuşma tipi kırmızı/turuncu olayın ±speech_minutes içindeyse sebep."""
    if events is None:
        return "news_unavailable"
    for e in events:
        if e.impact == "High" and abs((now - e.t).total_seconds()) <= minutes * 60:
            return f"red_news:{e.title}@{e.t:%H:%M}"
        if e.speech and e.impact in ("High", "Medium") and abs((now - e.t).total_seconds()) <= speech_minutes * 60:
            return f"speech:{e.title}@{e.t:%H:%M}"
    return None


def _verdict_label(score_100: int) -> str:
    if score_100 >= 65:
        return "GÜÇLÜ AL (Boğa)"
    if score_100 >= 55:
        return "POZİTİF (Alım Yönlü)"
    if score_100 <= 35:
        return "GÜÇLÜ SAT (Ayı)"
    if score_100 <= 45:
        return "NEGATİF (Satım Yönlü)"
    return "NÖTR (Dengeli)"


def compute_market_score(events: list[Event] | None, now: pd.Timestamp | None = None) -> dict:
    """ForexFactory'den çekilen tüm güncel verileri analiz ederek:
    - ABD Borsaları (`QQQ`, `TQQQ`, `SPY`, `DIA`) için `0–100` arası Piyasa Puanı,
    - Altın & Gümüş (`GLD`, `SLV`) için `0–100` arası Kıymetli Maden Puanı,
    - ABD Doları (`DXY`) için `0–100` arası Dolar Güç Puanı,
    - Açıklanan verilerin tek tek puan katkısını (`breakdown`) ve yaklaşan kritik haberleri (`upcoming`) üretir.
    (`50` = Nötr, `>55` = Alım/Boğa, `<45` = Satım/Ayı).
    """
    if now is None:
        now = pd.Timestamp(datetime.now(NY).replace(tzinfo=None))
    if not events:
        return {
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "equity_score": 50,
            "equity_net": 0,
            "equity_verdict": "NÖTR (Veri Yok)",
            "metal_score": 50,
            "metal_net": 0,
            "metal_verdict": "NÖTR (Veri Yok)",
            "usd_score": 50,
            "usd_net": 0,
            "released_count": 0,
            "breakdown": [],
            "upcoming": [],
        }

    tier1_words = ("cpi", "ppi", "pce", "non-farm", "nonfarm", "fomc", "federal funds", "gdp", "retail sales", "ism")
    released = [e for e in events if e.has_actual and e.impact in ("High", "Medium")]
    latest_day = max((e.t.date() for e in released), default=now.date())

    eq_raw = 0.0
    mt_raw = 0.0
    usd_raw = 0.0
    total_w = 0.0
    breakdown = []

    for e in released:
        tl = e.title.lower()
        base_w = (14.0 if any(w in tl for w in tier1_w) else 10.0) if (tier1_w := tier1_words) and e.impact == "High" else 6.0
        if e.t.date() == latest_day:
            base_w *= 1.25
        eq_sig = macro_signal_for_symbol(e, "QQQ")
        mt_sig = macro_signal_for_symbol(e, "GLD")
        bw = e.resolved_better_worse()

        eq_delta = base_w if eq_sig == "long" else (-base_w if eq_sig == "short" else 0.0)
        mt_delta = base_w if mt_sig == "long" else (-base_w if mt_sig == "short" else 0.0)
        usd_delta = base_w if bw == 1 else (-base_w if bw == 2 else 0.0)

        if eq_sig != "neutral" or mt_sig != "neutral" or bw in (1, 2):
            total_w += base_w
        eq_raw += eq_delta
        mt_raw += mt_delta
        usd_raw += usd_delta

        breakdown.append({
            "time": e.t.strftime("%Y-%m-%d %H:%M"),
            "title": e.title,
            "impact": e.impact,
            "category": e.category,
            "actual": e.actual,
            "forecast": e.forecast,
            "previous": e.previous,
            "equity_signal": eq_sig,
            "metal_signal": mt_sig,
            "equity_pts": round(eq_delta, 1),
            "metal_pts": round(mt_delta, 1),
        })

    denom = max(total_w, 28.0)
    eq_net = int(round(max(-50.0, min(50.0, (eq_raw / denom) * 50.0))))
    mt_net = int(round(max(-50.0, min(50.0, (mt_raw / denom) * 50.0))))
    usd_net = int(round(max(-50.0, min(50.0, (usd_raw / denom) * 50.0))))

    eq_score = 50 + eq_net
    mt_score = 50 + mt_net
    usd_score = 50 + usd_net

    upcoming = [
        {
            "time": e.t.strftime("%Y-%m-%d %H:%M"),
            "title": e.title,
            "impact": e.impact,
            "forecast": e.forecast,
            "previous": e.previous,
        }
        for e in events
        if not e.has_actual and e.impact in ("High", "Medium") and e.t >= now - pd.Timedelta(hours=2)
    ][:10]

    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "latest_release_day": str(latest_day),
        "equity_score": eq_score,
        "equity_net": eq_net * 2,
        "equity_verdict": _verdict_label(eq_score),
        "metal_score": mt_score,
        "metal_net": mt_net * 2,
        "metal_verdict": _verdict_label(mt_score),
        "usd_score": usd_score,
        "usd_net": usd_net * 2,
        "released_count": len(released),
        "breakdown": breakdown[::-1],
        "upcoming": upcoming,
    }


def save_market_score(state_dir: str | Path, events: list[Event] | None, now: pd.Timestamp | None = None) -> dict:
    """Piyasa puanını hesaplar ve `state/forex_market_score.json` dosyasına yazar."""
    score = compute_market_score(events, now)
    try:
        p = Path(state_dir) / "forex_market_score.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(score, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        log.warning("Piyasa puanı kaydedilemedi: %s", e)
    return score

