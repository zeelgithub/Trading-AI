"""Tiny file-based JSON cache with TTL.

Used to avoid re-fetching large datasets (House/Senate stock-watcher dumps,
SEC ticker map) and re-running Haiku summaries on every call within a session.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def get(key: str, ttl_seconds: int) -> Any | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - payload.get("ts", 0) > ttl_seconds:
        return None
    return payload.get("data")


def set(key: str, data: Any) -> None:
    path = _cache_path(key)
    path.write_text(json.dumps({"ts": time.time(), "data": data}), encoding="utf-8")
