"""Tests for the write-tier agent tools (propose_rotation)."""

from __future__ import annotations

from src.agents.tools.writes import build_write_registry
from src.core.rotation import RotationProposalStore, RotationService, RotationStateStore

KNOWN = ("trend_following", "mean_reversion", "breakout")


def _service(tmp_path) -> RotationService:
    return RotationService(
        KNOWN,
        state_store=RotationStateStore(tmp_path / "r.json"),
        proposal_store=RotationProposalStore(tmp_path / "p.json"),
    )


def test_propose_rotation_is_write_tier(tmp_path):
    tool = build_write_registry(_service(tmp_path)).get("propose_rotation")
    assert tool is not None
    assert tool.write is True


def test_propose_rotation_handler_records_proposal(tmp_path):
    svc = _service(tmp_path)
    out = build_write_registry(svc).get("propose_rotation").run(
        action="disable", strategy="breakout", rationale="noise verdict",
    )
    assert out["ok"] and out["proposal_id"]
    assert len(svc.list_pending()) == 1


def test_propose_rotation_handler_enforces_guardrails(tmp_path):
    out = build_write_registry(_service(tmp_path)).get("propose_rotation").run(
        action="disable", strategy="ghost",
    )
    assert not out["ok"]
