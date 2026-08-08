"""
Cross-process lock -- common layer.

Atomic writes (jsonio.py) stop a reader from ever seeing a half-written file,
but they do nothing about a lost-update RACE: the always-on Telegram listener
(TradeService: /buy, /close, /flatten, /halt, /reset) and the scheduled paper
cycle (Orchestrator.run_cycle) both do read -> mutate in memory -> save against
the SAME state/positions.json and state/halt.json. If both run in the same
window, whichever saves last silently overwrites the other's change -- a
stop-raise or a manual close can simply vanish, with no error anywhere.

`bot_lock()` serializes every such critical section through one file lock
(state/.bot.lock). Callers choose what "can't get the lock" means for them:
the scheduled cycle skips this run quietly (the next one picks it up); a phone
action tells the user to retry. Neither should hang forever or silently
proceed unlocked.

Boundary: places orders NO.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = PROJECT_ROOT / "state" / ".bot.lock"


class BotBusy(Exception):
    """Raised when `bot_lock()` cannot acquire the lock within its timeout --
    another process (listener action or scheduled cycle) is mid-operation."""


@contextmanager
def bot_lock(timeout: float = 30.0, path: str | Path = DEFAULT_LOCK_PATH):
    """Hold the cross-process state lock for the duration of the `with` block.
    Raises BotBusy (never blocks indefinitely) if another process holds it
    past `timeout` seconds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path), timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise BotBusy(
            f"could not acquire state lock within {timeout}s -- "
            "another operation is in progress"
        ) from exc
