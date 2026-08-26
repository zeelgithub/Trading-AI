"""
Generic JSON-backed id-keyed record store -- common layer.

src/core/proposals.ProposalStore, src/core/rotation.RotationProposalStore, and
src/discovery/weight_advisor.WeightProposalStore each hand-implemented the
identical pattern: load a {id: dict} JSON blob (quarantining it if corrupt),
add/get/list-pending/mark-status a dataclass record by id, save back
atomically. This factors that pattern once; each store becomes a thin
subclass naming its own dataclass and file path.

Boundary: places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Generic, Protocol, TypeVar, runtime_checkable

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger

# Shared "how long does a pending, non-trade agent recommendation (strategy
# rotation, discovery source reweighting) live before expiring" default --
# previously four independent `10080` literals across rotation.py and
# weight_advisor.py. The real, config-driven value lives at
# settings.approval.recommendation_expiry_minutes; this is only the
# fallback used when that key is absent.
DEFAULT_RECOMMENDATION_EXPIRY_MINUTES = 10080


@runtime_checkable
class _PendingRecord(Protocol):
    id: str
    status: str

    def is_expired(self) -> bool: ...


T = TypeVar("T", bound=_PendingRecord)


class JsonRecordStore(Generic[T]):
    """A `{record.id: asdict(record)}` JSON file at `path`, written atomically,
    with corrupt-file quarantine on load. `record_cls` must accept its own
    `asdict()` output as `**kwargs` (every record here already is: a plain
    `@dataclass`)."""

    def __init__(self, path: str | Path, record_cls: type[T], *, log_name: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._record_cls = record_cls
        self._log_name = log_name

    def _load_raw(self) -> dict[str, dict]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger(self._log_name).error(
                "%s corrupt; moved to %s and starting empty", self.path.name, quarantined)
            return {}
        return payload or {}

    def _save_raw(self, payload: dict[str, dict]) -> None:
        atomic_write_json(self.path, payload)

    def add(self, record: T) -> None:
        payload = self._load_raw()
        payload[record.id] = asdict(record)
        self._save_raw(payload)

    def get(self, record_id: str) -> T | None:
        raw = self._load_raw().get(record_id)
        return self._record_cls(**raw) if raw else None

    def list_pending(self) -> list[T]:
        out = [self._record_cls(**d) for d in self._load_raw().values()]
        return [r for r in out if r.status == "pending" and not r.is_expired()]

    def mark(self, record_id: str, status: str) -> None:
        payload = self._load_raw()
        if record_id in payload:
            payload[record_id]["status"] = status
            self._save_raw(payload)
