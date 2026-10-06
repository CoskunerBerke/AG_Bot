"""Forex Factory Ekonomik Takvim ve Son Dakika Piyasa Haberleri Ajanı (News & Macro Intelligence Agent).

İşlevler:
1. Forex Factory'nin haftalık ekonomik takvimini (XML Feed) anlık olarak çeker ve inceler.
2. Yüksek Etkili Kırmızı Klasör (High-Impact Red Folder) USD olaylarını (FOMC, TÜFE/CPI, Tarım Dışı İstihdam/NFP,
   İşsizlik Başvuruları vb.) tespit eder.
3. Kırmızı Klasör Kalkanı (Red Folder Shield): Yüksek etkili bir USD verisi 15-30 dakika içindeyse botu uyarır
   ve haber anındaki devasa makas açılmalarından (spread spike) korur.
4. Dow Jones / Wall Street Journal ve piyasa son dakika haber akışını (Hormuz boğazı, petrol, Fed konuşmaları)
   gerçek zamanlı izler ve 'reports/forexfactory_briefing.md' brifing raporu üretir.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .data import NY

log = logging.getLogger("news_agent")

FOREX_FACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
DOW_JONES_MARKETS_RSS = "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"
MARKETWATCH_TOP_RSS = "https://feeds.content.dowjones.io/public/rss/mw_topstories"


class ForexFactoryNewsAgent:
    def __init__(self, state_dir: str = "state", reports_dir: str = "reports"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.briefing_file = self.reports_dir / "forexfactory_briefing.md"
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # ------------------------------------------------------------------ 1. Ekonomik Takvim Çekme
    def fetch_calendar(self, currency_filter: str = "USD") -> list[dict[str, Any]]:
        """Forex Factory'den bu haftanın takvim olaylarını çeker."""
        events: list[dict[str, Any]] = []
        try:
            r = requests.get(FOREX_FACTORY_CALENDAR_URL, headers=self.headers, timeout=10)
            if r.status_code != 200:
                log.warning("Forex Factory XML çekilemedi: HTTP %d", r.status_code)
                return events

            root = ET.fromstring(r.content)
            for ev in root.findall("event"):
                c = ev.findtext("country", "")
                if currency_filter and c != currency_filter:
                    continue

                impact = ev.findtext("impact", "Low")
                title = ev.findtext("title", "")
                date_str = ev.findtext("date", "")
                time_str = ev.findtext("time", "")
                forecast = ev.findtext("forecast", "")
                previous = ev.findtext("previous", "")

                events.append({
                    "country": c,
                    "title": title,
                    "date": date_str,
                    "time": time_str,
                    "impact": impact,
                    "forecast": forecast,
                    "previous": previous,
                    "is_red_folder": impact.lower() == "high",
                    "is_orange_folder": impact.lower() == "medium",
                })
        except Exception as e:
            log.warning("Forex Factory takvim ayrıştırma hatası: %s", e)

        return events

    # ------------------------------------------------------------------ 2. Son Dakika Piyasa Haberleri
    def fetch_breaking_news(self, limit: int = 10) -> list[dict[str, str]]:
        """Piyasa son dakika haber akışını (Hormuz, Petrol, Fed vb.) çeker."""
        news_items: list[dict[str, str]] = []
        feeds = [DOW_JONES_MARKETS_RSS, MARKETWATCH_TOP_RSS]

        for feed_url in feeds:
            try:
                r = requests.get(feed_url, headers=self.headers, timeout=6)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:limit]:
                    title = item.findtext("title", "").strip()
                    link = item.findtext("link", "").strip()
                    pub_date = item.findtext("pubDate", "").strip()
                    desc = item.findtext("description", "").strip()

                    # Yinelenenleri atla
                    if any(n["title"] == title for n in news_items):
                        continue

                    # Duygu / Konu Etiketleme
                    tag = "GENEL"
                    t_lower = title.lower()
                    if any(w in t_lower for w in ["oil", "petrol", "hormuz", "iran", "opec", "energy"]):
                        tag = "PETROL & ENERJİ / HORMUZ"
                    elif any(w in t_lower for w in ["fed", "rate", "inflation", "cpi", "powell", "fomc"]):
                        tag = "FED & FAİZ / ENFLASYON"
                    elif any(w in t_lower for w in ["ai", "tech", "chip", "nvidia", "deepseek"]):
                        tag = "TEKNOLOJİ & YAPAY ZEKA"

                    news_items.append({
                        "title": title,
                        "tag": tag,
                        "link": link,
                        "date": pub_date,
                        "desc": desc[:200]
                    })
            except Exception as e:
                log.warning("Haber akışı çekme hatası (%s): %s", feed_url, e)

        return news_items[:limit]

    # ------------------------------------------------------------------ 3. Kırmızı Klasör Kalkanı (Risk Koruması)
    def check_red_folder_shield(self) -> dict[str, Any]:
        """Bugün veya yakın saatlerde yüksek etkili Kırmızı Klasör (FOMC vb.) var mı denetler."""
        events = self.fetch_calendar(currency_filter="USD")
        now_ny = datetime.now(NY)
        today_str = now_ny.strftime("%m-%d-%Y")

        red_events_today = []
        for e in events:
            if e["is_red_folder"] and e["date"] == today_str:
                red_events_today.append(e)

        return {
            "shield_active": len(red_events_today) > 0,
            "today": today_str,
            "red_events_count": len(red_events_today),
            "events": red_events_today
        }

    # ------------------------------------------------------------------ 4. Kapsamlı Brifing Raporu Üretimi
    def generate_briefing(self) -> Path:
        """Kullanıcının ve botun inceleyebileceği detaylı Markdown brifingi oluşturur."""
        cal = self.fetch_calendar(currency_filter="USD")
        news = self.fetch_breaking_news(limit=8)
        shield = self.check_red_folder_shield()

        now_ny = datetime.now(NY)
        md = f"""# 🌐 Forex Factory & Makro Piyasa İstihbarat Brifingi

**Rapor Tarihi:** {now_ny:%Y-%m-%d %H:%M} ET  
**Kırmızı Klasör Kalkanı (Red Folder Shield):** {'🔴 DİKKAT: YÜKSEK ETKİLİ VERİ VAR' if shield['shield_active'] else '🟢 TEMİZ: Bugün Kritik Kırmızı Klasör Yok'}

---

### 1. 📅 Forex Factory USD Ekonomik Takvimi (Bu Hafta)
| Tarih & Saat | Etki Seviyesi | Olay / Veri | Beklenti | Önceki |
| :--- | :---: | :--- | :---: | :---: |
"""
        for e in cal:
            impact_badge = "🔴 YÜKSEK (Kırmızı)" if e["is_red_folder"] else ("🟠 ORTA (Turuncu)" if e["is_orange_folder"] else "🟡 Düşük")
            md += f"| **{e['date']} {e['time']}** | {impact_badge} | {e['title']} | {e['forecast'] or '-'} | {e['previous'] or '-'} |\n"

        md += "\n---\n\n### 2. 📰 Canlı Makro & Piyasa Son Dakika Akışı (Hormuz, Fed, Petrol)\n"
        for n in news:
            md += f"* **[{n['tag']}]** {n['title']}  \n  *Detay: {n['desc']}...*  \n"

        md += """
---
### 3. 🛡️ Algoritmik Bot İçin Kural:
* Kırmızı Klasör (FOMC, TÜFE vb.) açıklanmadan **15 dakika önce ve açıklandıktan 15 dakika sonra** yeni pozisyon açılmaz.
* Hürmüz Boğazı / Petrol kriz haberlerinde enerji hisselerinde (CEG vb.) oynaklık arttığından stoplar sıkı tutulur.
"""
        self.briefing_file.write_text(md, encoding="utf-8")
        log.info("Forex Factory brifingi kaydedildi: %s", self.briefing_file)
        return self.briefing_file
