"""Formasyon Laboratuvarı ağırlıklarının yüklenmesi (bağımlılıksız küçük modül)."""
from __future__ import annotations

import json
from pathlib import Path


def load_weights(cfg: dict, tf: str) -> dict | None:
    """Doğrulanmış ağırlıklar. Ayar kapalıysa veya laboratuvar o zaman diliminde çalışmadıysa
    None döner (ders kitabı ağırlıkları kullanılır)."""
    if not cfg.get("strategy", {}).get("use_validated_patterns", True):
        return None
    path = Path(cfg["state_dir"]) / "pattern_weights.json"
    if not path.exists():
        return None
    w = json.loads(path.read_text(encoding="utf-8")).get(tf)
    return w["patterns"] if w else None
