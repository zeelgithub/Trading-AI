"""
State store -- core layer.

Persists the bot's managed positions (lifecycle + ratchet high-water state) to
JSON so a restart -- or a separate scheduled run -- can rebuild the exact stop
ladders and keep raising them. The broker remains the source of truth for what
positions exist; this store remembers the bot's *intent state* around them.

Boundary: places orders NO.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger
from src.common.models import Side
from src.execution.order_manager import ManagedPosition, PositionStatus
from src.risk.ratchet_stop import ratchet_from_state

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "state" / "positions.json"
DEFAULT_HALT_PATH = PROJECT_ROOT / "state" / "halt.json"


class HaltClass:
    """Why the bot halted. The class -- not the free-text reason -- decides whether
    a halt is ever eligible for automatic resume (see src/core/self_heal.py).
    Only STALE_DATA and DISCONNECT are auto-resumable; the rest stay manual."""

    MANUAL = "manual"
    RECONCILE_MISMATCH = "reconcile_mismatch"
    KILL_SWITCH = "kill_switch"
    STALE_DATA = "stale_data"
    DISCONNECT = "disconnect"
    EXCEPTION = "exception"
    SYMBOL_ERRORS = "symbol_errors"
    CONFIG = "config"
    UNKNOWN = "unknown"


class HaltStore:
    """Persists a HALT across cold runs so the bot does NOT self-resume between
    separate scheduled invocations. Cleared only by an explicit manual reset --
    or, for whitelisted transient classes, by the verified self-healer."""

    def __init__(self, path: str | Path = DEFAULT_HALT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def is_halted(self) -> str | None:
        info = self.halt_info()
        return info["reason"] if info else None

    def halt_info(self) -> dict | None:
        """Full halt record {reason, class, ts}, or None if not halted."""
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # A halt file we cannot read still means "halted" (default-to-halt).
            return {"reason": "halt file unreadable (treating as halted)",
                    "class": HaltClass.UNKNOWN, "ts": None}
        return {
            "reason": data.get("reason", "halted"),
            "class": data.get("class", HaltClass.UNKNOWN),
            "ts": data.get("ts"),
        }

    def set(self, reason: str, halt_class: str = HaltClass.UNKNOWN) -> None:
        from datetime import datetime, timezone

        atomic_write_json(self.path, {
            "reason": reason, "class": halt_class,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class StateStore:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, positions: dict[str, ManagedPosition]) -> None:
        payload = {sym: self._to_dict(p) for sym, p in positions.items()}
        atomic_write_json(self.path, payload)

    def load(self) -> dict[str, ManagedPosition]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            # Corrupt file: proceed with empty state -- the reconciler will see
            # any real broker positions as unknown and HALT (default-to-halt),
            # which beats crashing before halt handling exists.
            get_logger("state_store").error(
                "positions file corrupt; moved to %s and starting empty "
                "(reconciler will halt on any untracked broker position)", quarantined)
            return {}
        if payload is None:
            return {}
        return {sym: self._from_dict(d) for sym, d in payload.items()}

    @staticmethod
    def _to_dict(p: ManagedPosition) -> dict:
        return {
            "symbol": p.symbol,
            "side": p.side.value,
            "qty": p.qty,
            "strategy": p.strategy,
            "entry_order_id": p.entry_order_id,
            "stop_order_id": p.stop_order_id,
            "current_stop": p.current_stop,
            "status": p.status.value,
            "filled_qty": p.filled_qty,
            "tp_order_id": p.tp_order_id,
            "take_profit": p.take_profit,
            "ratchet": p.ratchet.state(),
        }

    @staticmethod
    def _from_dict(d: dict) -> ManagedPosition:
        return ManagedPosition(
            symbol=d["symbol"],
            side=Side(d["side"]),
            qty=d["qty"],
            strategy=d["strategy"],
            entry_order_id=d["entry_order_id"],
            stop_order_id=d["stop_order_id"],
            current_stop=d["current_stop"],
            ratchet=ratchet_from_state(d["ratchet"]),
            status=PositionStatus(d.get("status", "open")),
            filled_qty=d.get("filled_qty", 0.0),
            tp_order_id=d.get("tp_order_id"),
            take_profit=d.get("take_profit"),
        )
