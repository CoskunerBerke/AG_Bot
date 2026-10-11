"""Kaan'ın Telegram işlem özetlerini toplar (Dumb Money Hub → "Bing-Bongs" başlığı, sadece kaanermi).

Bu mesajlar seans SONRASI atılır; canlı sinyal değil, botun Kaan'a uyumunu ölçen test verisidir.
Kayıtlar yerelde kalır (state/telegram/, git-ignored): kanal içeriği üçüncü kişiye aittir.

    python main.py tg-login     # bir kez: telefon + Telegram kodu (kullanıcı girer)
    python main.py tg-sync      # yeni mesajları indir (ilk seferde tüm geçmiş)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import timezone
from pathlib import Path

from ..common import NY

GROUP = "DumbMoneyyHub"
TOPIC_HINT = "bing"
AUTHOR = "kaanermig"            # görünen ad: kααnєrmí

RX_RR = re.compile(r"(\d+(?:[.,]\d+)?)\s*R{1,2}\b", re.I)
RX_USD = re.compile(r"\$\s?([\d.,]+)|([\d.,]+)\s?\$")
INSTR = {"NQ": r"\bNQ\b|NASDAQ|NAS100|NAZDAQ", "ES": r"\bES\b|S&P|SPX|US500", "XAU": r"XAU|GOLD|ALTIN",
         "BTC": r"\bBTC\b|BITCOIN"}
DIR = {"long": r"\blong\b|\bbuy\b|al[ıi]m|yukar[ıi]", "short": r"\bshort\b|\bsell\b|sat[ıi][sş]|a[sş]a[gğ][ıi]"}


def _dir(cfg) -> Path:
    p = Path(cfg["state_dir"]) / "telegram"
    (p / "media").mkdir(parents=True, exist_ok=True)
    return p


def _client(cfg):
    from telethon import TelegramClient
    api_id, api_hash = os.getenv("TELEGRAM_API_ID", ""), os.getenv("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH .env'de yok")
    return TelegramClient(str(_dir(cfg) / "kaan_reader"), int(api_id), api_hash)


def parse_text(text: str) -> dict:
    t = text or ""
    rr = RX_RR.search(t)
    usd = RX_USD.search(t)
    out = {"rr": float(rr.group(1).replace(",", ".")) if rr else None,
           "usd": usd.group(1) or usd.group(2) if usd else None,
           "instrument": next((k for k, rx in INSTR.items() if re.search(rx, t, re.I)), None),
           "dir_words": sorted(k for k, rx in DIR.items() if re.search(rx, t, re.I))}
    if out["usd"]:
        try:
            out["usd"] = float(out["usd"].replace(",", ""))
        except ValueError:
            out["usd"] = None
    return out


async def _login(cfg):
    async with _client(cfg) as c:                       # start(): telefon, kod, (varsa) 2FA şifresini sorar
        me = await c.get_me()
        print(f"Giriş tamam: {me.first_name} (@{me.username})")


async def _sync(cfg, limit: int | None = None) -> dict:
    from telethon.tl.functions.messages import GetForumTopicsRequest
    out_dir = _dir(cfg)
    store = out_dir / "bing_bongs.jsonl"
    seen = set()
    if store.exists():
        seen = {json.loads(l)["id"] for l in store.read_text(encoding="utf-8").splitlines() if l.strip()}
    c = _client(cfg)
    await c.connect()
    if not await c.is_user_authorized():
        raise SystemExit("Önce giriş: python main.py tg-login")
    group = await c.get_entity(GROUP)
    res = await c(GetForumTopicsRequest(peer=group, offset_date=None, offset_id=0, offset_topic=0, limit=100))
    topic = next((t for t in res.topics if TOPIC_HINT in (t.title or "").lower()), None)
    if topic is None:
        raise SystemExit(f"'{TOPIC_HINT}' başlığı bulunamadı: {[t.title for t in res.topics]}")
    new = 0
    with store.open("a", encoding="utf-8") as fh:
        async for m in c.iter_messages(group, reply_to=topic.id, reverse=True, limit=limit):
            if m.id in seen or not (m.message or m.photo):
                continue
            s = await m.get_sender()
            if (getattr(s, "username", "") or "").lower() != AUTHOR:
                continue
            media = None
            if m.photo:
                media = str(await m.download_media(file=str(out_dir / "media" / f"{m.id}.jpg")))
            ny = m.date.replace(tzinfo=timezone.utc).astimezone(NY)
            rec = {"id": m.id, "utc": m.date.isoformat(), "ny": ny.isoformat(), "date_ny": ny.date().isoformat(),
                   "text": m.message or "", "media": media, **parse_text(m.message or "")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            new += 1
    await c.disconnect()
    total = len(seen) + new
    return {"yeni": new, "toplam": total, "dosya": str(store), "başlık": topic.title}


def login(cfg):
    asyncio.run(_login(cfg))


def sync(cfg, limit: int | None = None) -> dict:
    return asyncio.run(_sync(cfg, limit))
