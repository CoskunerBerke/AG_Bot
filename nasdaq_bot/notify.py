"""Telegram bildirimleri (isteğe bağlı): günlük rapor ve hata uyarısı.

Sunucudaki .env / .env.b'de TELEGRAM_BOT_TOKEN (@BotFather'dan) ve TELEGRAM_CHAT_ID varsa mesaj gönderilir;
yoksa sessizce atlanır. Rapor her durumda reports/ klasörüne de yazılır.
"""
from __future__ import annotations

import logging
import os
import time

import requests


def send(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text[:4000]}, timeout=15)
        return r.ok
    except requests.RequestException:
        return False


class TelegramErrorHandler(logging.Handler):
    """ERROR kayıtlarını Telegram'a iletir; en fazla `every` saniyede bir mesaj (arada biriken hatalar sayılır)."""

    def __init__(self, bot: str, every: int = 900):
        super().__init__(level=logging.ERROR)
        self.bot, self.every, self.last, self.skipped = bot, every, 0.0, 0

    def emit(self, record: logging.LogRecord) -> None:
        now = time.time()
        if now - self.last < self.every:
            self.skipped += 1
            return
        extra = f" (+{self.skipped} hata daha)" if self.skipped else ""
        self.last, self.skipped = now, 0
        send(f"⚠️ Bot {self.bot.upper()} hata{extra}: {record.getMessage()[:500]}")
