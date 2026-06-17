"""Tests for the anomaly_triage agent + the audit-tail read it relies on."""

from __future__ import annotations

from src.agents.triage import AnomalyTriage
from src.common.logging import AuditLog
from tests.unit.agent_fakes import ScriptedModel, text_response, tool_response


def test_audit_tail_returns_recent_oldest_first(tmp_path):
    a = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        a.record("event", i=i)
    assert [e["i"] for e in a.tail(3)] == [2, 3, 4]


def test_audit_tail_empty(tmp_path):
    assert AuditLog(tmp_path / "none.jsonl").tail() == []


def test_triage_reads_then_diagnoses():
    model = ScriptedModel([
        tool_response("get_halt_state", {}, call_id="c1"),
        tool_response("get_recent_events", {"limit": 10}, call_id="c2"),
        text_response(
            '{"halt_class":"reconcile_mismatch","diagnosis":"local state diverged from broker",'
            '"likely_cause":"a fill was missed","recommended_action":"check positions then /reset",'
            '"severity":"high","auto_resumable":false}'
        ),
    ])
    res = AnomalyTriage(model_factory=lambda model_id: model).diagnose()
    assert res.ok
    assert res.tool_calls == ["get_halt_state", "get_recent_events"]
    assert res.output["severity"] == "high"
    assert res.output["auto_resumable"] is False
