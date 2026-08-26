"""Tests for the discovery weight advisor: the deterministic suggestion
formula, and the propose -> approve/deny -> applied-state service."""

from __future__ import annotations

from types import SimpleNamespace

from src.discovery.ledger import DiscoveryLedger, SourceStats
from src.discovery.weight_advisor import (
    DiscoveryWeightService,
    DiscoveryWeightState,
    DiscoveryWeightStateStore,
    WeightProposalStore,
    compute_contribution,
    suggest_weights,
)

ACTIVE = frozenset({"congress", "technical", "news", "fundamentals"})


def _stats(source, surfaced, proposed, avg_score) -> SourceStats:
    s = SourceStats(source=source, surfaced=surfaced, proposed=proposed)
    s.score_sum = avg_score * surfaced  # avg_score is a derived property
    return s


# --- compute_contribution ---

def test_compute_contribution_none_below_min_sample():
    assert compute_contribution(_stats("congress", surfaced=5, proposed=5, avg_score=90)) is None


def test_compute_contribution_formula():
    # 10/20 proposed = 0.5 rate, avg score 80 -> 0.5 * 0.8 = 0.4
    assert compute_contribution(_stats("congress", 20, 10, 80)) == 0.4


# --- suggest_weights ---

def test_suggest_weights_none_when_all_below_sample_floor():
    stats = [_stats("congress", 3, 1, 90), _stats("technical", 4, 1, 80)]
    current = {"congress": 0.5, "technical": 0.5}
    assert suggest_weights(stats, current, {"congress", "technical"}) is None


def test_suggest_weights_none_with_only_one_judged_source():
    stats = [_stats("congress", 20, 15, 90), _stats("technical", 3, 1, 50)]
    current = {"congress": 0.5, "technical": 0.5}
    assert suggest_weights(stats, current, {"congress", "technical"}) is None


def test_suggest_weights_reallocates_toward_stronger_source():
    # congress: strong (high proposal rate + score); technical: weak.
    stats = [
        _stats("congress", 30, 24, 90),   # signal = 0.8 * 0.9 = 0.72
        _stats("technical", 30, 6, 40),   # signal = 0.2 * 0.4 = 0.08
        _stats("news", 5, 1, 50),         # below sample floor -- untouched
    ]
    current = {"congress": 0.35, "technical": 0.35, "news": 0.15, "fundamentals": 0.15}
    result = suggest_weights(stats, current, ACTIVE)
    assert result is not None
    weights, rationale = result
    assert weights["congress"] > current["congress"]
    assert weights["technical"] < current["technical"]
    # Unjudged sources keep their exact current weight.
    assert weights["news"] == current["news"]
    assert weights["fundamentals"] == current["fundamentals"]
    assert "congress" in rationale and "unchanged" in rationale


def test_suggest_weights_preserves_judged_weight_mass():
    stats = [_stats("congress", 30, 27, 95), _stats("technical", 30, 3, 30)]
    current = {"congress": 0.5, "technical": 0.5}
    weights, _ = suggest_weights(stats, current, {"congress", "technical"})
    assert weights["congress"] + weights["technical"] == 1.0  # reallocated, not grown/shrunk


def test_suggest_weights_respects_max_shift_cap():
    # Extreme signal gap would otherwise want to move congress far more than
    # 30% of its own current weight in one pass.
    stats = [_stats("congress", 50, 49, 99), _stats("technical", 50, 1, 5)]
    current = {"congress": 0.5, "technical": 0.5}
    weights, _ = suggest_weights(stats, current, {"congress", "technical"}, max_shift=0.30)
    assert weights["congress"] <= current["congress"] * 1.30 + 1e-6
    assert weights["technical"] >= current["technical"] * (1 - 0.30) - 1e-6


def test_suggest_weights_none_when_change_too_small():
    # Nearly identical signals -> negligible reallocation.
    stats = [_stats("congress", 30, 15, 60), _stats("technical", 30, 15, 61)]
    current = {"congress": 0.5, "technical": 0.5}
    assert suggest_weights(stats, current, {"congress", "technical"}, min_delta=0.02) is None


# --- DiscoveryWeightState / Store ---

def test_state_defaults_empty(tmp_path):
    assert DiscoveryWeightStateStore(tmp_path / "w.json").load().weights == {}


def test_state_roundtrip(tmp_path):
    store = DiscoveryWeightStateStore(tmp_path / "w.json")
    store.save(DiscoveryWeightState(weights={"congress": 0.6, "technical": 0.4}))
    assert store.load().weights == {"congress": 0.6, "technical": 0.4}


# --- DiscoveryWeightService ---

def _service(tmp_path, ledger: DiscoveryLedger | None = None, **kw) -> DiscoveryWeightService:
    return DiscoveryWeightService(
        active_sources={"congress", "technical", "news", "fundamentals"},
        default_weights={"congress": 0.35, "technical": 0.35, "news": 0.15, "fundamentals": 0.15},
        ledger=ledger or DiscoveryLedger(path=tmp_path / "ledger.jsonl"),
        state_store=DiscoveryWeightStateStore(tmp_path / "w.json"),
        proposal_store=WeightProposalStore(tmp_path / "wp.json"),
        **kw,
    )


def _cand(symbol, score, sources):
    return SimpleNamespace(symbol=symbol, score=score, sources=sources, strategy="discovery")


def test_current_weights_merges_override_over_defaults(tmp_path):
    svc = _service(tmp_path)
    assert svc.current_weights() == svc.default_weights
    svc.state_store.save(DiscoveryWeightState(weights={"congress": 0.6}))
    merged = svc.current_weights()
    assert merged["congress"] == 0.6 and merged["technical"] == 0.35  # untouched key survives


def test_suggest_not_ok_when_ledger_empty(tmp_path):
    res = _service(tmp_path).suggest()
    assert res["ok"] is False


def test_suggest_creates_pending_proposal_when_meaningful(tmp_path):
    ledger = DiscoveryLedger(path=tmp_path / "ledger.jsonl")
    for i in range(30):
        proposed = i < 27  # congress: strong hit rate
        ledger.record_surface(
            [_cand(f"C{i}", 90, ["congress"])],
            [SimpleNamespace(symbol=f"C{i}", id=f"p{i}")] if proposed else [],
        )
    for i in range(30):
        proposed = i < 3  # technical: weak hit rate
        ledger.record_surface(
            [_cand(f"T{i}", 30, ["technical"])],
            [SimpleNamespace(symbol=f"T{i}", id=f"q{i}")] if proposed else [],
        )
    svc = _service(tmp_path, ledger=ledger)

    res = svc.suggest()

    assert res["ok"] is True and res["proposal_id"]
    pending = svc.list_pending()
    assert len(pending) == 1
    assert pending[0].weights["congress"] > svc.default_weights["congress"]


def test_approve_applies_suggestion_to_state(tmp_path):
    ledger = DiscoveryLedger(path=tmp_path / "ledger.jsonl")
    for i in range(30):
        ledger.record_surface([_cand(f"C{i}", 90, ["congress"])],
                              [SimpleNamespace(symbol=f"C{i}", id=f"p{i}")] if i < 28 else [])
    for i in range(30):
        ledger.record_surface([_cand(f"T{i}", 30, ["technical"])],
                              [SimpleNamespace(symbol=f"T{i}", id=f"q{i}")] if i < 2 else [])
    svc = _service(tmp_path, ledger=ledger)
    pid = svc.suggest()["proposal_id"]

    result = svc.approve(pid)

    assert result["ok"] is True
    assert svc.state_store.load().weights == svc.proposal_store.get(pid).weights
    assert svc.list_pending() == []


def test_deny_leaves_state_unchanged(tmp_path):
    ledger = DiscoveryLedger(path=tmp_path / "ledger.jsonl")
    for i in range(30):
        ledger.record_surface([_cand(f"C{i}", 90, ["congress"])],
                              [SimpleNamespace(symbol=f"C{i}", id=f"p{i}")] if i < 28 else [])
    for i in range(30):
        ledger.record_surface([_cand(f"T{i}", 30, ["technical"])],
                              [SimpleNamespace(symbol=f"T{i}", id=f"q{i}")] if i < 2 else [])
    svc = _service(tmp_path, ledger=ledger)
    pid = svc.suggest()["proposal_id"]

    assert svc.deny(pid)["ok"] is True
    assert svc.state_store.load().weights == {}
    assert svc.list_pending() == []


def test_deny_rejects_an_already_approved_proposal(tmp_path):
    """Regression guard: deny() used to have no status guard at all (unlike
    approve(), and unlike the trade-proposal deny path in run_telegram.py's
    _on_deny) -- a stale Telegram button tap could flip an already-approved
    (and applied) proposal's audit record to "denied" after the fact,
    corrupting the record without actually reverting the applied weights."""
    ledger = DiscoveryLedger(path=tmp_path / "ledger.jsonl")
    for i in range(30):
        ledger.record_surface([_cand(f"C{i}", 90, ["congress"])],
                              [SimpleNamespace(symbol=f"C{i}", id=f"p{i}")] if i < 28 else [])
    for i in range(30):
        ledger.record_surface([_cand(f"T{i}", 30, ["technical"])],
                              [SimpleNamespace(symbol=f"T{i}", id=f"q{i}")] if i < 2 else [])
    svc = _service(tmp_path, ledger=ledger)
    pid = svc.suggest()["proposal_id"]
    assert svc.approve(pid)["ok"]
    applied_weights = svc.state_store.load().weights

    result = svc.deny(pid)

    assert result["ok"] is False
    assert svc.proposal_store.get(pid).status == "approved"
    assert svc.state_store.load().weights == applied_weights  # untouched


def test_approve_rejects_degenerate_weights(tmp_path):
    from src.discovery.weight_advisor import WeightProposal

    svc = _service(tmp_path)
    bad = WeightProposal.create({"congress": -0.1, "technical": 0.4})
    svc.proposal_store.add(bad)

    res = svc.approve(bad.id)

    assert res["ok"] is False
    assert svc.state_store.load().weights == {}


def test_approve_unknown_proposal_id(tmp_path):
    assert _service(tmp_path).approve("does-not-exist")["ok"] is False


def test_custom_expiry_minutes_is_actually_used(tmp_path):
    """expiry_minutes (config-driven in production, see scripts/run_telegram.py's
    settings.approval.recommendation_expiry_minutes) must reach the proposal
    created by suggest(), not just sit unused on the service."""
    from datetime import datetime, timedelta, timezone

    ledger = DiscoveryLedger(path=tmp_path / "ledger.jsonl")
    for i in range(30):
        proposed = i < 27  # congress: strong hit rate
        ledger.record_surface(
            [_cand(f"C{i}", 90, ["congress"])],
            [SimpleNamespace(symbol=f"C{i}", id=f"p{i}")] if proposed else [],
        )
    for i in range(30):
        proposed = i < 3  # technical: weak hit rate
        ledger.record_surface(
            [_cand(f"T{i}", 30, ["technical"])],
            [SimpleNamespace(symbol=f"T{i}", id=f"q{i}")] if proposed else [],
        )
    svc = _service(tmp_path, ledger=ledger, expiry_minutes=5)

    result = svc.suggest()

    assert result["ok"] is True
    prop = svc.proposal_store.get(result["proposal_id"])
    now = datetime.now(timezone.utc)
    assert not prop.is_expired(now + timedelta(minutes=4))
    assert prop.is_expired(now + timedelta(minutes=6))
