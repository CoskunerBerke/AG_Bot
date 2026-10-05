"""Ayar dosyası ve ortam değişkenlerinin yüklenmesi."""
from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # Göreli yolları proje köküne sabitle
    cache = Path(cfg.setdefault("data", {}).get("cache_dir", ".cache"))
    cfg["data"]["cache_dir"] = str(cache if cache.is_absolute() else ROOT / cache)
    cfg["state_dir"] = str(ROOT / "state")
    cfg["reports_dir"] = str(ROOT / "reports")
    from .rejection_blocks import configure as rb_configure
    rb_configure(cfg.get("rejection"))
    return cfg
