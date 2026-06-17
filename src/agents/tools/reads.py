"""
Read tools -- cognitive plane (write=False).

The safe lookup surface for agents: market data, indicators, positions, halt
state, and the strategy scoreboard. Each tool wraps a pure read-query function,
so these are the in-process twins of the market_data / portfolio_state MCP
servers -- identical logic, exposed two ways (decision #4).

Every tool here is read-only. Write tools (propose/halt/rotate) live elsewhere
and enforce the risk gate in their handler.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from src.agents.tools.base import Tool, ToolRegistry
from src.common.logging import AuditLog
from src.core import portfolio_view as pf
from src.data import queries as mkt


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required)}


def build_read_registry() -> ToolRegistry:
    """A registry of the standard read tools, ready to hand to an agent."""
    return ToolRegistry([
        Tool(
            "get_recent_bars",
            "Recent daily OHLCV bars for a symbol (oldest first).",
            _schema({"symbol": {"type": "string"},
                     "days": {"type": "integer", "description": "how many recent bars", "default": 20}},
                    ("symbol",)),
            handler=lambda symbol, days=20: mkt.recent_bars(symbol, days),
        ),
        Tool(
            "get_indicators",
            "Latest causal indicator snapshot + regime classification for a symbol.",
            _schema({"symbol": {"type": "string"}}, ("symbol",)),
            handler=lambda symbol: mkt.indicator_snapshot(symbol),
        ),
        Tool(
            "get_positions",
            "Current open managed positions (the bot's intent state).",
            _schema({}),
            handler=lambda: pf.positions_snapshot(),
        ),
        Tool(
            "get_halt_state",
            "Whether the bot is HALTED and the reason.",
            _schema({}),
            handler=lambda: pf.halt_snapshot(),
        ),
        Tool(
            "get_scoreboard",
            "Ranked strategy scoreboard: validation verdicts + live attribution.",
            _schema({}),
            handler=lambda: pf.scoreboard_snapshot(),
        ),
        Tool(
            "get_recent_events",
            "Recent audit-trail events (oldest first) -- for diagnosing what happened.",
            _schema({"limit": {"type": "integer", "description": "how many events", "default": 20}}),
            handler=lambda limit=20: {"events": AuditLog().tail(limit)},
        ),
    ])
