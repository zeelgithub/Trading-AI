"""
Discovery source weight advisor -- discovery layer.

The one place in this project where "adaptive, not static" applies --
deliberately NOT the risk gate, sizing, or strategy entry/exit logic, which
regulatory guidance (SEC 15c3-5, FINRA 15-09) and every production trading
engine researched for this project's architecture plan keep strictly
deterministic. `discovery.weights` (config/settings.yaml) has been manual-only
since the discovery plane shipped, by deliberate decision ("no self-adjusting
black box" -- docs/ROADMAP.md "Discovery plane"). This module does not
overturn that decision: it computes a SUGGESTED reweighting from the ledger's
already-tracked, already-transparent per-source stats
(`DiscoveryLedger.summarize`), and the suggestion only ever becomes real
through the SAME propose -> phone Approve/Deny -> applied-state flow every
other change in this project uses -- this mirrors `src/core/rotation.py`
almost exactly, on purpose, rather than inventing a new pattern. Nothing here
is a black box: the formula is a few lines, and both the ledger inputs and
the resulting weights are printed in the proposal text before you approve it.

What "contribution" means here, honestly: it is NOT realized trading P&L per
source. P&L is tracked per STRATEGY on the scoreboard (congress ideas land on
the `congress_copy` row; technical ideas share a row with every OTHER
technical signal, not just discovery-surfaced ones), so there is no clean
per-source P&L signal to compute from today -- see `src/discovery/ledger.py`'s
own docstring, which draws the same line. What IS real and already tracked:
how often a source's vote survives into a candidate that clears the score
floor and gets proposed to the phone (`SourceStats.proposed / .surfaced`) and
the average blended score its votes contribute (`.avg_score`). A source that
votes often but rarely produces anything worth proposing is pulling less
weight than its configured share; this shrinks it (and grows the others,
proportionally). A source with too little history to judge yet
(< `MIN_SAMPLE_SIZE`) is left at its current weight rather than reacting to
noise, and any single suggestion is capped to a bounded relative move
(`MAX_SHIFT_PCT`) so one noisy week can't swing a weight wildly.

Boundary: reads the ledger only; proposes only; places orders NO.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger
from src.discovery.ledger import DiscoveryLedger, SourceStats

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = PROJECT_ROOT / "state" / "discovery_weights.json"
DEFAULT_PROPOSALS_PATH = PROJECT_ROOT / "state" / "discovery_weight_proposals.json"

MIN_SAMPLE_SIZE = 10    # ledger rows a source needs before its contribution counts as evidence
MAX_SHIFT_PCT = 0.30    # one suggestion moves a judged source's weight by at most this much, relative
MIN_DELTA = 0.02        # smallest absolute weight change worth proposing at all


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- the suggestion itself: pure, deterministic, no I/O -----------------------

def compute_contribution(stats: SourceStats, min_sample_size: int = MIN_SAMPLE_SIZE) -> float | None:
    """A 0-1 signal: how often this source's votes clear the score floor and
    get proposed, weighted by how strong those votes tend to be. None if
    there isn't yet enough history to judge (see module docstring)."""
    if stats.surfaced < min_sample_size:
        return None
    proposal_rate = stats.proposed / stats.surfaced
    return round(proposal_rate * (stats.avg_score / 100.0), 4)


def suggest_weights(
    stats: list[SourceStats],
    current_weights: dict[str, float],
    active_sources,
    *,
    min_sample_size: int = MIN_SAMPLE_SIZE,
    max_shift: float = MAX_SHIFT_PCT,
    min_delta: float = MIN_DELTA,
) -> tuple[dict[str, float], str] | None:
    """Suggest a reweighting, or None if there's nothing meaningful to
    propose (too little data, or the suggestion barely moves anything).

    Only reallocates weight AMONG sources with enough evidence to judge
    (`judged`); a source below `min_sample_size` keeps its current weight
    untouched -- it neither loses nor gains share on no evidence. The judged
    sources' combined weight mass is preserved (this reallocates their share
    of the pie, it doesn't grow or shrink it relative to the unjudged
    sources), and each judged source's move is capped to `max_shift` of its
    own current weight.
    """
    stats_by_source = {s.source: s for s in stats}
    judged: dict[str, float] = {}
    unjudged: set[str] = set()
    for src in active_sources:
        s = stats_by_source.get(src)
        signal = compute_contribution(s, min_sample_size) if s else None
        if signal is None:
            unjudged.add(src)
        else:
            judged[src] = signal

    if len(judged) < 2:
        return None  # nothing to reallocate between with only 0-1 judged sources

    judged_weight_mass = sum(current_weights.get(s, 0.0) for s in judged)
    signal_total = sum(judged.values())
    if signal_total <= 0 or judged_weight_mass <= 0:
        return None

    suggested = dict(current_weights)
    for src, signal in judged.items():
        raw = judged_weight_mass * (signal / signal_total)
        cur = current_weights.get(src, 0.0)
        if cur > 0:
            lo, hi = cur * (1 - max_shift), cur * (1 + max_shift)
            suggested[src] = max(lo, min(hi, raw))
        else:
            suggested[src] = raw

    # Clamping can drift the judged sources' combined mass away from
    # judged_weight_mass; rescale them back so unjudged sources' shares (and
    # the overall total) are undisturbed by the clamp.
    clamped_mass = sum(suggested[s] for s in judged)
    if clamped_mass > 0:
        scale = judged_weight_mass / clamped_mass
        for src in judged:
            suggested[src] = round(suggested[src] * scale, 3)

    moved = max(abs(suggested[s] - current_weights.get(s, 0.0)) for s in judged)
    if moved < min_delta:
        return None

    parts = [
        f"{s}: {stats_by_source[s].proposed}/{stats_by_source[s].surfaced} proposed, "
        f"avg score {stats_by_source[s].avg_score:.0f} -> "
        f"{current_weights.get(s, 0.0):.2f} => {suggested[s]:.2f}"
        for s in sorted(judged)
    ]
    if unjudged:
        parts.append(f"unchanged, < {min_sample_size} samples yet: {', '.join(sorted(unjudged))}")
    return suggested, "; ".join(parts)


# --- applied state -------------------------------------------------------------

@dataclass
class DiscoveryWeightState:
    """Applied override for `discovery.weights`. Empty = use config as-is --
    this is a no-op until a suggestion has been approved, same posture as
    RotationState defaulting to all-enabled."""
    weights: dict[str, float] = field(default_factory=dict)


class DiscoveryWeightStateStore:
    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> DiscoveryWeightState:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("weight_advisor").error(
                "discovery weight state corrupt; moved to %s -- reverting to config defaults",
                quarantined)
            return DiscoveryWeightState()
        return DiscoveryWeightState(weights=payload or {})

    def save(self, state: DiscoveryWeightState) -> None:
        atomic_write_json(self.path, state.weights)


# --- pending proposals -----------------------------------------------------------

@dataclass
class WeightProposal:
    id: str
    weights: dict[str, float]
    rationale: str = ""
    status: str = "pending"           # pending | approved | denied | expired
    created_ts: str = ""
    expiry_ts: str = ""

    @classmethod
    def create(cls, weights: dict[str, float], rationale: str = "", expiry_minutes: int = 10080):
        now = _utcnow()
        return cls(
            id=f"wgt-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            weights=dict(weights), rationale=rationale, status="pending",
            created_ts=now.isoformat(),
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
        head = ", ".join(f"{k}={v:.2f}" for k, v in sorted(self.weights.items()))
        return f"reweight sources -> {head}" + (f"  ({self.rationale})" if self.rationale else "")


class WeightProposalStore:
    def __init__(self, path: str | Path = DEFAULT_PROPOSALS_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict[str, dict]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("weight_advisor").error(
                "discovery weight proposals corrupt; moved to %s and starting empty", quarantined)
            return {}
        return payload or {}

    def _save_raw(self, payload: dict[str, dict]) -> None:
        atomic_write_json(self.path, payload)

    def add(self, proposal: WeightProposal) -> None:
        payload = self._load_raw()
        payload[proposal.id] = asdict(proposal)
        self._save_raw(payload)

    def get(self, proposal_id: str) -> WeightProposal | None:
        raw = self._load_raw().get(proposal_id)
        return WeightProposal(**raw) if raw else None

    def list_pending(self) -> list[WeightProposal]:
        out = [WeightProposal(**d) for d in self._load_raw().values()]
        return [p for p in out if p.status == "pending" and not p.is_expired()]

    def mark(self, proposal_id: str, status: str) -> None:
        payload = self._load_raw()
        if proposal_id in payload:
            payload[proposal_id]["status"] = status
            self._save_raw(payload)


# --- service (ledger -> suggest -> propose -> approve/deny) --------------------

class DiscoveryWeightService:
    """Coordinates weight-reweighting proposals. `suggest()` is the only entry
    point that computes anything; `approve`/`deny` just move a proposal
    already on record, exactly like RotationService."""

    def __init__(
        self,
        active_sources,
        default_weights: dict[str, float],
        *,
        ledger: DiscoveryLedger | None = None,
        state_store: DiscoveryWeightStateStore | None = None,
        proposal_store: WeightProposalStore | None = None,
        min_sample_size: int = MIN_SAMPLE_SIZE,
        expiry_minutes: int = 10080,
    ) -> None:
        self.active_sources = frozenset(active_sources)
        self.default_weights = dict(default_weights)
        self.ledger = ledger or DiscoveryLedger()
        self.state_store = state_store or DiscoveryWeightStateStore()
        self.proposal_store = proposal_store or WeightProposalStore()
        self.min_sample_size = int(min_sample_size)
        self.expiry_minutes = int(expiry_minutes)

    def current_weights(self) -> dict[str, float]:
        """Applied override merged over the configured defaults -- what the
        live Scorer actually uses right now (see build_discovery_pipeline)."""
        return {**self.default_weights, **self.state_store.load().weights}

    def suggest(self) -> dict:
        current = self.current_weights()
        result = suggest_weights(
            self.ledger.summarize(), current, self.active_sources,
            min_sample_size=self.min_sample_size,
        )
        if result is None:
            return {"ok": False, "error": "nothing meaningful to suggest yet"}
        weights, rationale = result
        prop = WeightProposal.create(weights, rationale, expiry_minutes=self.expiry_minutes)
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
        if not p.weights or any(w < 0 for w in p.weights.values()) or sum(p.weights.values()) <= 0:
            return {"ok": False, "error": "no longer valid: degenerate weights"}
        self.state_store.save(DiscoveryWeightState(weights=p.weights))
        self.proposal_store.mark(proposal_id, "approved")
        return {"ok": True, "summary": p.summary()}

    def deny(self, proposal_id: str) -> dict:
        p = self.proposal_store.get(proposal_id)
        if p is None:
            return {"ok": False, "error": "proposal not found"}
        self.proposal_store.mark(proposal_id, "denied")
        return {"ok": True, "summary": f"denied: {p.summary()}"}

    def list_pending(self) -> list[WeightProposal]:
        return self.proposal_store.list_pending()
