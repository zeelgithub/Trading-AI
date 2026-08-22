"""Tests for scripts/run_self_heal.py's optional agent-diagnosis enrichment.

_try_agent_diagnosis is best-effort and additive: no ANTHROPIC_API_KEY, or any
failure at all, must fall back to exactly nothing (the caller then sends the
deterministic incident_brief alone, unchanged from before this existed) --
never let the agent path block or corrupt the one guaranteed alert.
"""

from __future__ import annotations

from scripts.run_self_heal import _try_agent_diagnosis


class _Result:
    def __init__(self, ok, output=None):
        self.ok = ok
        self.output = output


class _FakeTriage:
    def __init__(self, *a, **kw):
        pass

    def diagnose(self):
        return _Result(True, {
            "severity": "high", "diagnosis": "local state diverged from broker",
            "likely_cause": "a fill was missed", "recommended_action": "check positions then /reset",
        })


class _FailingTriage:
    def __init__(self, *a, **kw):
        pass

    def diagnose(self):
        raise RuntimeError("model API down")


class _EmptyTriage:
    def __init__(self, *a, **kw):
        pass

    def diagnose(self):
        return _Result(False, None)


def test_no_key_returns_none(monkeypatch):
    def _raise():
        raise RuntimeError("Missing required environment variable 'ANTHROPIC_API_KEY'")
    monkeypatch.setattr("src.common.secrets.load_anthropic_api_key", _raise)

    assert _try_agent_diagnosis() is None


def test_key_present_returns_formatted_diagnosis(monkeypatch):
    monkeypatch.setattr("src.common.secrets.load_anthropic_api_key", lambda: "fake-key")
    monkeypatch.setattr("src.agents.triage.AnomalyTriage", _FakeTriage)

    result = _try_agent_diagnosis()

    assert result is not None
    assert "high" in result and "local state diverged from broker" in result
    assert "check positions then /reset" in result


def test_agent_failure_falls_back_to_none(monkeypatch):
    """A model/network failure must never propagate -- the brief still ships
    without the enrichment."""
    monkeypatch.setattr("src.common.secrets.load_anthropic_api_key", lambda: "fake-key")
    monkeypatch.setattr("src.agents.triage.AnomalyTriage", _FailingTriage)

    assert _try_agent_diagnosis() is None


def test_agent_not_ok_falls_back_to_none(monkeypatch):
    monkeypatch.setattr("src.common.secrets.load_anthropic_api_key", lambda: "fake-key")
    monkeypatch.setattr("src.agents.triage.AnomalyTriage", _EmptyTriage)

    assert _try_agent_diagnosis() is None
