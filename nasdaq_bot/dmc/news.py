"""ForexFactory haber filtresi (STRATEGY_SPEC §9). Sadece canlı yürütücüde kullanılır — geçmiş takvim
olmadığı için backtest haber filtresiz çalışır.

Not: faireconomy XML'indeki saatler UTC'dir (ör. Unemployment Claims '12:30pm' = 08:30 ET, EDT).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from ..data import NY

log = logging.getLogger(__name__)
URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
SPEECH_WORDS = ("speaks", "press conference", "testifies", "testimony", "fomc statement")


@dataclass
class Event:
    t: pd.Timestamp        # NY, tz-naive
    title: str
    impact: str            # High | Medium | Low | Holiday

    @property
    def speech(self) -> bool:
        return any(w in self.title.lower() for w in SPEECH_WORDS)


def parse(xml: bytes, currency: str = "USD") -> list[Event]:
    out = []
    for ev in ET.fromstring(xml).findall("event"):
        if ev.findtext("country", "") != currency:
            continue
        d, t = ev.findtext("date", ""), (ev.findtext("time", "") or "").strip()
        try:
            utc = datetime.strptime(f"{d} {t}", "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
        except ValueError:                   # 'All Day' / 'Tentative'
            continue
        out.append(Event(pd.Timestamp(utc.astimezone(NY).replace(tzinfo=None)), ev.findtext("title", ""),
                         ev.findtext("impact", "Low")))
    return out


def fetch_events(state_dir: str | Path, currency: str = "USD") -> list[Event] | None:
    """Bu haftanın olayları; ağ hatasında önbellek. Hiç veri yoksa None (çağıran fail-closed davranır)."""
    cache = Path(state_dir) / "dmc_ff_calendar.xml"
    xml = None
    try:
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200 and len(r.content) > 100:
            xml = r.content
            cache.write_bytes(xml)
    except requests.RequestException as e:
        log.warning("ForexFactory alınamadı: %s", e)
    if xml is None and cache.exists():
        xml = cache.read_bytes()
    if xml is None:
        return None
    try:
        return parse(xml, currency)
    except ET.ParseError as e:
        log.warning("ForexFactory XML bozuk: %s", e)
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
