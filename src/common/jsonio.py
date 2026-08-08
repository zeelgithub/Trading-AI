"""
Crash-safe JSON file IO -- common layer.

State files (positions, halt, proposals, rotation) must never be half-written:
a crash mid-write would otherwise truncate the file and the next run would blow
up before its halt handling even exists. `atomic_write_json` writes to a temp
file in the same directory, fsyncs, then `os.replace`s -- readers see either the
old file or the new one, never a torn write.

`load_json_or_quarantine` is the matching read side: a corrupt file is moved
aside (never deleted) and the caller gets `None` plus the backup path, so it can
fall back to a safe default instead of crashing.

Boundary: places orders NO.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def atomic_write_json(path: str | Path, payload, *, indent: int = 2, default=str) -> None:
    """Write `payload` as JSON to `path` atomically (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent, default=default)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json_or_quarantine(path: str | Path) -> tuple[dict | list | None, Path | None]:
    """Load JSON from `path`. Returns (payload, None) on success, (None, None)
    if the file does not exist, and (None, backup_path) if it is corrupt --
    the corrupt file is renamed aside (kept, never deleted) so the caller can
    proceed with a safe default and the evidence survives for forensics."""
    path = Path(path)
    if not path.exists():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, backup)
        except OSError:
            return None, path  # could not move it; report the original
        return None, backup
