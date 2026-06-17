"""Tests for strategy rotation: state, proposals, and guardrails."""

from __future__ import annotations

from src.core.rotation import (
    RotationProposalStore,
    RotationService,
    RotationState,
    RotationStateStore,
)

KNOWN = ("trend_following", "mean_reversion", "breakout")


def service(tmp_path, **kw) -> RotationService:
    return RotationService(
        KNOWN,
        state_store=RotationStateStore(tmp_path / "rot.json"),
        proposal_store=RotationProposalStore(tmp_path / "rot_prop.json"),
        **kw,
    )


# --- state ---

def test_state_defaults_to_enabled(tmp_path):
    assert RotationStateStore(tmp_path / "r.json").load().is_enabled("anything") is True


def test_state_roundtrip(tmp_path):
    store = RotationStateStore(tmp_path / "r.json")
    s = RotationState()
    s.apply("disable", "breakout")
    store.save(s)
    reloaded = store.load()
    assert reloaded.is_enabled("breakout") is False
    assert reloaded.is_enabled("trend_following") is True


def test_reweight_zero_disables(tmp_path):
    s = RotationState()
    s.apply("reweight", "breakout", 0.0)
    assert s.is_enabled("breakout") is False


# --- propose guardrails ---

def test_propose_valid_records_pending(tmp_path):
    svc = service(tmp_path)
    res = svc.propose("disable", "breakout", rationale="noise")
    assert res["ok"] and res["proposal_id"]
    pending = svc.list_pending()
    assert len(pending) == 1 and pending[0].strategy == "breakout"


def test_propose_rejects_unknown_action_or_strategy(tmp_path):
    svc = service(tmp_path)
    assert not svc.propose("nuke", "breakout")["ok"]
    assert not svc.propose("disable", "ghost")["ok"]


def test_propose_reweight_requires_valid_weight(tmp_path):
    svc = service(tmp_path)
    assert not svc.propose("reweight", "breakout")["ok"]              # missing
    assert not svc.propose("reweight", "breakout", weight=2.0)["ok"]  # out of bounds
    assert svc.propose("reweight", "breakout", weight=0.5)["ok"]


def test_cannot_disable_last_active_strategy(tmp_path):
    svc = service(tmp_path)
    svc.approve(svc.propose("disable", "breakout")["proposal_id"])
    svc.approve(svc.propose("disable", "mean_reversion")["proposal_id"])
    res = svc.propose("disable", "trend_following")    # only one left
    assert not res["ok"] and "last active" in res["error"]


# --- approve / deny ---

def test_approve_applies_to_state(tmp_path):
    svc = service(tmp_path)
    pid = svc.propose("disable", "breakout")["proposal_id"]
    assert svc.approve(pid)["ok"]
    assert svc.state_store.load().is_enabled("breakout") is False
    assert svc.list_pending() == []                     # no longer pending


def test_approve_revalidates_against_current_state(tmp_path):
    svc = service(tmp_path)
    pid = svc.propose("disable", "breakout")["proposal_id"]    # valid when proposed
    # State changes out from under it: the other two get disabled directly.
    st = svc.state_store.load()
    st.apply("disable", "trend_following")
    st.apply("disable", "mean_reversion")
    svc.state_store.save(st)
    res = svc.approve(pid)
    assert not res["ok"] and "no longer valid" in res["error"]


def test_deny_leaves_state_unchanged(tmp_path):
    svc = service(tmp_path)
    pid = svc.propose("disable", "breakout")["proposal_id"]
    assert svc.deny(pid)["ok"]
    assert svc.list_pending() == []
    assert svc.state_store.load().is_enabled("breakout") is True
