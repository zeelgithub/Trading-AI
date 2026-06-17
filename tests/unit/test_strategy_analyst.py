"""End-to-end test of the strategy_analyst vertical slice:
read tool -> reasoning -> gated write tool -> recorded proposal (places nothing).
"""

from __future__ import annotations

from src.agents.analyst import StrategyAnalyst
from src.core.rotation import RotationProposalStore, RotationService, RotationStateStore
from tests.unit.agent_fakes import ScriptedModel, text_response, tool_response

KNOWN = ("trend_following", "mean_reversion", "breakout")


def _service(tmp_path) -> RotationService:
    return RotationService(
        KNOWN,
        state_store=RotationStateStore(tmp_path / "r.json"),
        proposal_store=RotationProposalStore(tmp_path / "p.json"),
    )


def test_analyst_reads_scoreboard_then_proposes_rotation(tmp_path):
    rot = _service(tmp_path)
    model = ScriptedModel([
        tool_response("get_scoreboard", {}, call_id="c1"),
        tool_response("propose_rotation",
                      {"action": "disable", "strategy": "breakout", "rationale": "noise verdict"},
                      call_id="c2"),
        text_response('{"summary": "disabled breakout (noise)",'
                      ' "recommendations": [{"strategy":"breakout","action":"disable","rationale":"noise"}],'
                      ' "proposal_ids": ["x"]}'),
    ])
    analyst = StrategyAnalyst(rot, model_factory=lambda model_id: model)

    res = analyst.review()
    assert res.ok
    assert res.tool_calls == ["get_scoreboard", "propose_rotation"]
    assert res.output["summary"].startswith("disabled breakout")

    # The gated write actually recorded a pending proposal -- and nothing applied.
    pending = rot.list_pending()
    assert len(pending) == 1
    assert pending[0].strategy == "breakout" and pending[0].action == "disable"
    assert rot.state_store.load().is_enabled("breakout") is True  # unchanged until approved


def test_analyst_with_no_change_records_nothing(tmp_path):
    rot = _service(tmp_path)
    model = ScriptedModel([
        tool_response("get_scoreboard", {}, call_id="c1"),
        text_response('{"summary": "all strategies healthy", "recommendations": [], "proposal_ids": []}'),
    ])
    analyst = StrategyAnalyst(rot, model_factory=lambda model_id: model)

    res = analyst.review()
    assert res.ok
    assert res.tool_calls == ["get_scoreboard"]
    assert rot.list_pending() == []
