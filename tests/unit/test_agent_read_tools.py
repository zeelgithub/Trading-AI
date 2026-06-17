"""Tests for the in-process read-tool registry (the MCP servers' twin surface)."""

from __future__ import annotations

from src.agents.tools.reads import build_read_registry

_EXPECTED = ["get_recent_bars", "get_indicators", "get_positions", "get_halt_state", "get_scoreboard"]


def test_registry_exposes_expected_read_only_tools():
    reg = build_read_registry()
    for name in _EXPECTED:
        tool = reg.get(name)
        assert tool is not None, name
        assert tool.write is False          # reads must never be write-tier
        assert tool.spec()["name"] == name


def test_read_tool_handlers_return_dicts():
    reg = build_read_registry()
    assert "halted" in reg.get("get_halt_state").run()
    assert "strategies" in reg.get("get_scoreboard").run()
