"""Haber duyarlılık analizi: Yahoo Finance + Google News, finans sözlüğüyle güçlendirilmiş VADER."""
from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .data import UA

log = logging.getLogger(__name__)

# VADER genel amaçlıdır; finans haberlerine özgü kelimeleri ekliyoruz (-4..+4 ölçeği)
FIN_LEXICON = {
    "beat": 2.0, "beats": 2.0, "tops": 1.5, "upgrade": 2.5, "upgraded": 2.5, "upgrades": 2.5,
    "downgrade": -2.5, "downgraded": -2.5, "downgrades": -2.5, "miss": -2.0, "misses": -2.0,
    "missed": -2.0, "surge": 2.0, "surges": 2.0, "soar": 2.5, "soars": 2.5, "plunge": -2.5,
    "plunges": -2.5, "tumble": -2.0, "tumbles": -2.0, "rally": 1.8, "rallies": 1.8,
    "outperform": 2.0, "underperform": -2.0, "bullish": 2.0, "bearish": -2.0, "lawsuit": -1.8,
    "probe": -1.5, "investigation": -1.5, "recall": -1.5, "layoffs": -1.2, "record": 1.2,
    "raises": 1.2, "raised": 1.2, "cuts": -1.2, "slashes": -2.0, "buyback": 1.5, "dividend": 0.8,
    "selloff": -2.0, "slump": -2.0, "slumps": -2.0, "jump": 1.5, "jumps": 1.5, "gain": 1.2,
    "gains": 1.2, "drop": -1.3, "drops": -1.3, "falls": -1.3, "fall": -1.3, "rises": 1.3,
    "rise": 1.3, "overweight": 1.5, "underweight": -1.5, "bankruptcy": -3.0, "fraud": -3.0,
    "breakthrough": 2.0, "approval": 1.8, "approved": 1.8, "rejected": -1.8, "warning": -1.5,
    "weak": -1.5, "strong": 1.5, "sinks": -1.8, "sink": -1.8, "climbs": 1.3, "soaring": 2.3,
    "headwinds": -1.2, "tailwinds": 1.2, "antitrust": -1.3, "tariffs": -1.0, "halted": -1.5,
}

# Bilgi taşımayan rutin başlıklar (13F fon pozisyon bildirimleri, "hisse %x yükseldi - alınır mı?" vb.)
NOISE_RE = re.compile(
    r"(shares (sold|bought|acquired|purchased) by|stock (sold|bought|acquired|purchased) by"
    r"|(position|stake|holdings?) (in|of) .* (by|lowered|raised|trimmed|boosted|increased|decreased)"
    r"|(raises|lowers|trims|boosts|cuts|increases|decreases|grows|reduces|sells|buys|acquires) (its )?"
    r"(position|stake|holdings?)|has \$[\d.,]+ (million|billion|thousand) (stock )?(position|stake|holdings?)"
    r"|stock price (up|down) [\d.]+%|shares (up|down) [\d.]+% - |time to buy\?|should you buy)",
    re.IGNORECASE,
)

_analyzer: SentimentIntensityAnalyzer | None = None


def _get_analyzer() -> SentimentIntensityAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
        _analyzer.lexicon.update(FIN_LEXICON)
    return _analyzer


def _parse_time(v) -> datetime | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(v)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _yahoo_news(ticker: str) -> list[dict]:
    items = []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:  # noqa: BLE001
        return items
    for n in raw:
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = c.get("title")
        if not title:
            continue
        prov = c.get("provider")
        src = prov.get("displayName") if isinstance(prov, dict) else c.get("publisher")
        cu = c.get("canonicalUrl")
        url = cu.get("url") if isinstance(cu, dict) else c.get("link")
        items.append({
            "title": title.strip(), "summary": (c.get("summary") or "").strip(),
            "time": _parse_time(c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")),
            "source": src or "Yahoo", "url": url,
        })
    return items


def _google_news(ticker: str) -> list[dict]:
    items = []
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f'"{ticker}" stock when:7d', "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=UA, timeout=10,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:  # noqa: BLE001
        log.debug("Google News %s: %s", ticker, e)
        return items
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        src = it.findtext("source") or "Google News"
        title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()  # "Başlık - Kaynak" -> "Başlık"
        if title:
            items.append({"title": title, "summary": "", "time": _parse_time(it.findtext("pubDate")),
                          "source": src, "url": it.findtext("link")})
    return items


def news_sentiment(ticker: str, max_age_hours: int = 72) -> dict:
    """-1..+1 arası, yeniliğe göre ağırlıklı haber duyarlılık skoru."""
    items, seen = [], set()
    for it in _yahoo_news(ticker) + _google_news(ticker):
        if NOISE_RE.search(it["title"]):
            continue
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:70]
        if key and key not in seen:
            seen.add(key)
            items.append(it)

    an = _get_analyzer()
    now = datetime.now(timezone.utc)
    scored = []
    for it in items:
        if it["time"] is None:
            continue
        age = max((now - it["time"]).total_seconds() / 3600, 0)
        if age > max_age_hours:
            continue
        text = it["title"] + (". " + it["summary"][:300] if it["summary"] else "")
        s = an.polarity_scores(text)["compound"]
        scored.append({**it, "sentiment": s, "age_h": age, "w": math.exp(-age / 36)})

    if not scored:
        return {"score": 0.0, "count": 0, "headlines": []}
    raw = sum(x["sentiment"] * x["w"] for x in scored) / sum(x["w"] for x in scored)
    confidence = min(1.0, len(scored) / 5)  # az haber -> düşük güven
    scored.sort(key=lambda x: x["age_h"])
    return {"score": raw * confidence, "count": len(scored), "headlines": scored[:8]}
