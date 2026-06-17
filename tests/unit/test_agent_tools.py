"""Tests for the agent Tool abstraction and registry."""

from __future__ import annotations

import pytest

from src.agents.tools.base import Tool, ToolRegistry


def _echo(**kwargs):
    return {"echo": kwargs}


def make_tool(name: str = "echo", write: bool = False) -> Tool:
    return Tool(name=name, description="d", input_schema={"type": "object"},
                handler=_echo, write=write)


def test_tool_spec_and_run():
    t = make_tool()
    assert t.spec() == {"name": "echo", "description": "d", "input_schema": {"type": "object"}}
    assert t.run(a=1) == {"echo": {"a": 1}}


def test_tool_run_rejects_non_dict():
    t = Tool("bad", "d", {"type": "object"}, handler=lambda **k: "nope")
    with pytest.raises(TypeError):
        t.run()


def test_registry_register_get_specs():
    reg = ToolRegistry([make_tool("a"), make_tool("b")])
    assert "a" in reg
    assert reg.get("a").name == "a"
    assert reg.get("missing") is None
    assert [s["name"] for s in reg.specs(["b", "a"])] == ["b", "a"]


def test_registry_duplicate_raises():
    reg = ToolRegistry([make_tool("a")])
    with pytest.raises(ValueError):
        reg.register(make_tool("a"))


def test_registry_specs_unknown_raises():
    with pytest.raises(KeyError):
        ToolRegistry().specs(["nope"])


def test_write_flag():
    assert make_tool(write=True).write is True
    assert make_tool().write is False
