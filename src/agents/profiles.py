"""
Agent profiles -- cognitive plane.

A profile is the entire, static definition of one focused agent: its role
(system prompt), the minimal set of tools it may use, which model tier runs it,
and its step/token budget. Keeping each profile narrow is what keeps per-call
context tiny -- an agent only ever sees its own prompt + the task payload.

Concrete profiles (nl_router, strategy_analyst, anomaly_triage, planner, ...)
are added in their respective phases; this module defines the shape they share.

Boundary: declarative only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default tier: the cheap, fast model. Heavier reasoning profiles override this.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    system_prompt: str
    tool_names: tuple[str, ...] = ()
    model: str = DEFAULT_MODEL
    max_steps: int = 6          # hard cap on the tool-use loop -> bounded context/cost
    max_tokens: int = 1024
