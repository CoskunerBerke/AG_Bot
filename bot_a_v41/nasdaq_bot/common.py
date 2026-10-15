"""Ortak sabitler: New York saat dilimi ve Alpaca anahtarları."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def alpaca_keys() -> tuple[str, str] | None:
    k = os.getenv("ALPACA_API_KEY", "") or os.getenv("ALPACA_KEY_ID", "")
    s = os.getenv("ALPACA_API_SECRET", "") or os.getenv("ALPACA_SECRET_KEY", "")
    return (k, s) if k and s else None
