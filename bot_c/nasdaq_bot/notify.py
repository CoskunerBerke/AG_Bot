"""Telegram bildirimleri: botun tüm düşünceleri, sinyalleri, filtre kararları, işlem otopsileri ve günlük rapor.

.env dosyasında TELEGRAM_BOT_TOKEN (veya TELEGRAM_TOKEN) ve TELEGRAM_CHAT_ID / TELEGRAM_DATA_CHAT_ID varsa
mesaj gönderilir; yoksa sessizce atlanır.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
import time

import requests

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg_notify")
_LOCK = threading.Lock()
_DEDUP: dict[str, float] = {}


def _chat_ids() -> list[str]:
    raw_c = os.getenv("TELEGRAM_CHAT_ID", "")
    raw_d = os.getenv("TELEGRAM_DATA_CHAT_ID", "")
    # Eğer veri/işlem kanalı (TELEGRAM_DATA_CHAT_ID) tanımlıysa öncelikle oraya ve ana chat_id'ye gönder
    ids: list[str] = []
    for part in (raw_d + "," + raw_c).split(","):
        p = part.strip()
        if p and p not in ids:
            ids.append(p)
    return ids


def send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", "")
    chats = _chat_ids()
    if not token or not chats:
        return False
    ok_any = False
    for chat in chats:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text[:4000]},
                timeout=12,
            )
            ok_any = ok_any or r.ok
        except requests.RequestException:
            pass
    return ok_any


def send_async(text: str, dedup_key: str | None = None, cooldown: int = 120) -> None:
    """Canlı işlem döngüsünü 1 milisaniye bile yavaşlatmadan arka planda Telegram'a düşünce/karar gönderir."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", "")
    if not token or not _chat_ids():
        return
    now = time.monotonic()
    key = dedup_key or text[:180]
    with _LOCK:
        last = _DEDUP.get(key)
        if last is not None and (now - last) < cooldown:
            return
        _DEDUP[key] = now
        if len(_DEDUP) > 500:
            cutoff = now - 3600
            for k in [k for k, ts in _DEDUP.items() if ts < cutoff]:
                _DEDUP.pop(k, None)
    try:
        _POOL.submit(send, text)
    except Exception:
        pass


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
        send_async(f"⚠️ Bot {self.bot.upper()} hata{extra}: {record.getMessage()[:500]}", dedup_key=f"err_{self.bot}", cooldown=60)

