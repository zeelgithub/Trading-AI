"""Tests for the generic agent runtime (tool-use loop)."""

from __future__ import annotations

import json

from src.agents.profiles import AgentProfile
from src.agents.runtime import run_agent
from src.agents.tools.base import Tool, ToolRegistry
from tests.unit.agent_fakes import ScriptedModel, text_response, tool_response


def profile(tools=(), max_steps=6) -> AgentProfile:
    return AgentProfile(name="t", system_prompt="sys", tool_names=tools, max_steps=max_steps)


def test_single_shot_returns_json():
    model = ScriptedModel([text_response('Here you go: {"cmd": "status"}')])
    res = run_agent(profile(), "show status", model=model, registry=ToolRegistry())
    assert res.ok
    assert res.output == {"cmd": "status"}
    assert res.steps == 1
    assert res.tool_calls == []


def test_tool_use_loop_feeds_result_back():
    seen = {}

    def ping(**kwargs):
        seen["args"] = kwargs
        return {"pong": True}

    reg = ToolRegistry([Tool("ping", "p", {"type": "object"}, handler=ping)])
    model = ScriptedModel([
        tool_response("ping", {"x": 1}, call_id="c1"),
        text_response('{"done": true}'),
    ])
    res = run_agent(profile(tools=("ping",)), {"q": "go"}, model=model, registry=reg)

    assert res.ok and res.output == {"done": True}
    assert res.tool_calls == ["ping"]
    assert res.steps == 2
    assert seen["args"] == {"x": 1}

    # The second model call must have been handed the tool's result.
    second_msgs = model.calls[1]["messages"]
    last = second_msgs[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert json.loads(last["content"][0]["content"]) == {"pong": True}


def test_unknown_tool_is_reported_not_crash():
    reg = ToolRegistry([Tool("ping", "p", {"type": "object"}, handler=lambda **k: {"ok": True})])
    model = ScriptedModel([
        tool_response("ghost", {}, call_id="c1"),   # model hallucinates a tool name
        text_response('{"recovered": true}'),
    ])
    res = run_agent(profile(tools=("ping",)), "x", model=model, registry=reg)
    assert res.ok and res.output == {"recovered": True}
    assert res.tool_calls == ["ghost"]
    block = model.calls[1]["messages"][-1]["content"][0]
    assert "unknown tool" in json.loads(block["content"])["error"]


def test_tool_exception_is_isolated():
    def boom(**kwargs):
        raise RuntimeError("kaboom")

    reg = ToolRegistry([Tool("boom", "b", {"type": "object"}, handler=boom)])
    model = ScriptedModel([
        tool_response("boom", {}, call_id="c1"),
        text_response('{"ok": 1}'),
    ])
    res = run_agent(profile(tools=("boom",)), "x", model=model, registry=reg)
    assert res.ok
    err = json.loads(model.calls[1]["messages"][-1]["content"][0]["content"])["error"]
    assert "kaboom" in err


def test_non_json_final_is_error():
    model = ScriptedModel([text_response("I cannot help with that.")])
    res = run_agent(profile(), "x", model=model, registry=ToolRegistry())
    assert not res.ok
    assert "JSON" in res.error


def test_max_steps_exceeded():
    reg = ToolRegistry([Tool("ping", "p", {"type": "object"}, handler=lambda **k: {"ok": 1})])
    model = ScriptedModel([
        tool_response("ping", {}, call_id="c1"),
        tool_response("ping", {}, call_id="c2"),
    ])
    res = run_agent(profile(tools=("ping",), max_steps=2), "x", model=model, registry=reg)
    assert not res.ok
    assert "max steps" in res.error
    assert res.steps == 2
