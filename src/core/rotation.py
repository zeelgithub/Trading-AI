"""
Strategy rotation -- core layer.

Propose-only strategy coordination (decision #5). The strategy-analyst agent can
recommend enabling, disabling, or reweighting a strategy, but a recommendation is
only ever a *proposal*: it changes nothing until you approve it from the phone --
exactly like a trade proposal.

Two pieces of state:
  * RotationState  -- the APPLIED per-strategy {enabled, weight}, read by the
    orchestrator to decide which strategies may generate. Absent file => all
    enabled (so this is a no-op until you approve something).
  * RotationProposal -- a PENDING recommendation awaiting approval.

Guardrails are enforced server-side at both propose AND approve time, so an agent
(or a stale proposal) can never disable the last active strategy or set a weight
outside bounds.

Boundary: places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "state" / "rotation.json"
DEFAULT_PROPOSALS_PATH = PROJECT_ROOT / "state" / "rotation_proposals.json"

ACTIONS = ("enable", "disable", "reweight")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- applied state -----------------------------------------------------------

@dataclass
class RotationState:
    """Applied per-strategy enable/weight. Unknown strategies default to enabled."""
    strategies: dict[str, dict] = field(default_factory=dict)

    def is_enabled(self, strategy: str) -> bool:
        s = self.strategies.get(strategy)
        if s is None:
            return True
        return bool(s.get("enabled", True)) and float(s.get("weight", 1.0)) > 0.0

    def weight(self, strategy: str) -> float:
        s = self.strategies.get(strategy)
        return float(s.get("weight", 1.0)) if s else 1.0

    def apply(self, action: str, strategy: str, weight: float | None = None) -> None:
        cur = self.strategies.setdefault(strategy, {"enabled": True, "weight": 1.0})
        if action == "disable":
            cur["enabled"] = False
        elif action == "enable":
            cur["enabled"] = True
        elif action == "reweight":
            w = float(weight if weight is not None else 1.0)
            cur["weight"] = w
            cur["enabled"] = w > 0.0


class RotationStateStore:
    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RotationState:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("rotation").error(
                "rotation state corrupt; moved to %s -- reverting to all-enabled default",
                quarantined)
            return RotationState()
        return RotationState(strategies=payload or {})

    def save(self, state: RotationState) -> None:
        atomic_write_json(self.path, state.strategies)


# --- pending proposals -------------------------------------------------------

@dataclass
class RotationProposal:
    id: str
    action: str
    strategy: str
    weight: float | None = None
    rationale: str = ""
    status: str = "pending"           # pending | approved | denied | expired
    created_ts: str = ""
    expiry_ts: str = ""

    @classmethod
    def create(cls, action, strategy, weight=None, rationale="", expiry_minutes=10080):
        now = _utcnow()
        return cls(
            id=f"rot-{strategy}-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            action=action, strategy=strategy, weight=weight, rationale=rationale,
            status="pending", created_ts=now.isoformat(),
            expiry_ts=(now + timedelta(minutes=expiry_minutes)).isoformat(),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expiry_ts:
            return False
        try:
            return (now or _utcnow()) >= datetime.fromisoformat(self.expiry_ts)
        except ValueError:
            return False

    def summary(self) -> str:
        head = (f"reweight {self.strategy} -> {self.weight:g}"
                if self.action == "reweight" else f"{self.action} {self.strategy}")
        return f"{head}" + (f"  ({self.rationale})" if self.rationale else "")


class RotationProposalStore:
    def __init__(self, path: str | Path = DEFAULT_PROPOSALS_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict[str, dict]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("rotation").error(
                "rotation proposals corrupt; moved to %s and starting empty", quarantined)
            return {}
        return payload or {}

    def _save_raw(self, payload: dict[str, dict]) -> None:
        atomic_write_json(self.path, payload)

    def add(self, proposal: RotationProposal) -> None:
        payload = self._load_raw()
        payload[proposal.id] = asdict(proposal)
        self._save_raw(payload)

    def get(self, proposal_id: str) -> RotationProposal | None:
        raw = self._load_raw().get(proposal_id)
        return RotationProposal(**raw) if raw else None

    def list_pending(self) -> list[RotationProposal]:
        out = [RotationProposal(**d) for d in self._load_raw().values()]
        return [p for p in out if p.status == "pending" and not p.is_expired()]

    def mark(self, proposal_id: str, status: str) -> None:
        payload = self._load_raw()
        if proposal_id in payload:
            payload[proposal_id]["status"] = status
            self._save_raw(payload)


# --- service (validate -> propose -> approve/deny) ---------------------------

class RotationService:
    """Coordinates rotation proposals with guardrails. Enforced at propose AND
    approve time so a stale proposal can never apply an unsafe change."""

    def __init__(
        self,
        known_strategies,
        *,
        state_store: RotationStateStore | None = None,
        proposal_store: RotationProposalStore | None = None,
        max_weight: float = 1.0,
        expiry_minutes: int = 10080,
    ) -> None:
        self.known = tuple(known_strategies)
        self.state_store = state_store or RotationStateStore()
        self.proposal_store = proposal_store or RotationProposalStore()
        self.max_weight = float(max_weight)
        self.expiry_minutes = int(expiry_minutes)

    def propose(self, action, strategy, *, weight=None, rationale="") -> dict:
        action = str(action).lower().strip()
        strategy = str(strategy).strip()
        ok, reason = self._validate(action, strategy, weight, self.state_store.load())
        if not ok:
            return {"ok": False, "error": reason}
        prop = RotationProposal.create(action, strategy, _coerce_weight(weight),
                                       rationale, expiry_minutes=self.expiry_minutes)
        self.proposal_store.add(prop)
        return {"ok": True, "proposal_id": prop.id, "summary": prop.summary()}

    def approve(self, proposal_id: str) -> dict:
        p = self.proposal_store.get(proposal_id)
        if p is None:
            return {"ok": False, "error": "proposal not found"}
        if p.status != "pending":
            return {"ok": False, "error": f"already {p.status}"}
        if p.is_expired():
            self.proposal_store.mark(proposal_id, "expired")
            return {"ok": False, "error": "expired"}
        state = self.state_store.load()
        ok, reason = self._validate(p.action, p.strategy, p.weight, state)
        if not ok:
            return {"ok": False, "error": f"no longer valid: {reason}"}
        state.apply(p.action, p.strategy, p.weight)
        self.state_store.save(state)
        self.proposal_store.mark(proposal_id, "approved")
        return {"ok": True, "summary": p.summary()}

    def deny(self, proposal_id: str) -> dict:
        p = self.proposal_store.get(proposal_id)
        if p is None:
            return {"ok": False, "error": "proposal not found"}
        self.proposal_store.mark(proposal_id, "denied")
        return {"ok": True, "summary": f"denied: {p.summary()}"}

    def list_pending(self) -> list[RotationProposal]:
        return self.proposal_store.list_pending()

    # --- guardrails ---
    def _validate(self, action, strategy, weight, state: RotationState):
        if action not in ACTIONS:
            return False, f"unknown action {action!r} (expected {ACTIONS})"
        if strategy not in self.known:
            return False, f"unknown strategy {strategy!r}"
        if action == "reweight":
            w = _coerce_weight(weight)
            if w is None:
                return False, "reweight requires a numeric weight"
            if not (0.0 <= w <= self.max_weight):
                return False, f"weight {w} out of bounds [0, {self.max_weight}]"
        if not self._resulting_enabled(action, strategy, weight, state):
            return False, "cannot disable the last active strategy"
        return True, "ok"

    def _resulting_enabled(self, action, strategy, weight, state: RotationState) -> set[str]:
        enabled = {s for s in self.known if state.is_enabled(s)}
        if action == "disable" or (action == "reweight" and (_coerce_weight(weight) or 0.0) <= 0.0):
            enabled.discard(strategy)
        elif action == "enable":
            enabled.add(strategy)
        return enabled


def _coerce_weight(weight):
    if weight is None:
        return None
    try:
        return float(weight)
    except (TypeError, ValueError):
        return None
