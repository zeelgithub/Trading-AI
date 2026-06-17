"""Tests for the deterministic agent dispatcher."""

from __future__ import annotations

from src.agents.dispatch import AgentRequest, Dispatcher
from src.agents.profiles import AgentProfile
from src.agents.tools.base import ToolRegistry
from tests.unit.agent_fakes import ScriptedModel, text_response


def _profiles():
    return {"router": AgentProfile(name="router", system_prompt="route", model="m-fast")}


def test_route_lookup():
    d = Dispatcher(_profiles(), ToolRegistry(), model_factory=lambda m: None,
                   routes={"nl": "router"})
    assert d.route("nl") == "router"
    assert d.route("unknown") is None


def test_dispatch_happy_path_uses_routed_model_tier():
    seen = {}

    def factory(model_id):
        seen["model_id"] = model_id
        return ScriptedModel([text_response('{"cmd": "status"}')])

    d = Dispatcher(_profiles(), ToolRegistry(), model_factory=factory, routes={"nl": "router"})
    res = d.dispatch(AgentRequest(kind="nl", payload="show status"))
    assert res.ok and res.output == {"cmd": "status"}
    assert seen["model_id"] == "m-fast"   # the routed profile's tier, not a default


def test_dispatch_unrouted_kind():
    d = Dispatcher(_profiles(), ToolRegistry(), model_factory=lambda m: None, routes={})
    res = d.dispatch(AgentRequest(kind="weird", payload="x"))
    assert not res.ok
    assert "no route" in res.error


def test_dispatch_missing_profile():
    d = Dispatcher({}, ToolRegistry(), model_factory=lambda m: None, routes={"nl": "ghost"})
    res = d.dispatch(AgentRequest(kind="nl", payload="x"))
    assert not res.ok
    assert "unknown profile" in res.error
