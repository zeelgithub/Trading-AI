"""
Tool abstraction + registry -- cognitive plane.

A Tool wraps a plain Python handler with the JSON schema the model needs to call
it. Tools are split into two tiers (decision #4):

  * read tools  (write=False) -- safe lookups (market data, portfolio state,
    scoreboard). These are typically thin clients over MCP read servers.
  * write tools (write=True)  -- anything state-changing (propose a trade, halt,
    request a rotation). Their handler MUST run the risk gate / TradeService
    internally, so safety holds no matter which agent invokes them.

The registry is the single place a runtime looks up tools by name.

Boundary: a write tool enforces the risk gate in its handler; places orders only
via TradeService, never the broker directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., dict]
    write: bool = False  # True => touches the trade path; gate enforced in handler

    def spec(self) -> dict:
        """The schema block handed to the model."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def run(self, **kwargs) -> dict:
        result = self.handler(**kwargs)
        if not isinstance(result, dict):
            raise TypeError(
                f"tool {self.name!r} handler must return a dict, got {type(result).__name__}"
            )
        return result


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        """All registered tools (e.g. to merge registries)."""
        return list(self._tools.values())

    def specs(self, names: Iterable[str]) -> list[dict]:
        """Tool specs for the named tools, in order. Raises if a name is unknown
        (a profile listing a tool that doesn't exist is a config error)."""
        out = []
        for n in names:
            tool = self._tools.get(n)
            if tool is None:
                raise KeyError(f"unknown tool {n!r}")
            out.append(tool.spec())
        return out

    def __contains__(self, name: str) -> bool:
        return name in self._tools
