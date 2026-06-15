"""
Structured logging -- common layer.

Console logger plus an append-only JSONL audit trail. Every meaningful event
(signal, risk verdict, order, halt, reconcile) is recorded so a run can be
replayed and "why did it do that?" can always be answered.

Boundary: places orders NO.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_logger(name: str = "bot", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


class AuditLog:
    """Append-only JSONL event log."""

    def __init__(self, path: str | Path = PROJECT_ROOT / "logs" / "audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
