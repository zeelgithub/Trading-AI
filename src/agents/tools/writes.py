"""
Write tools -- cognitive plane (write=True).

State-changing tools, the tightly-controlled tier (decision #4). They are
reachable only by trusted agents and enforce their guardrails INSIDE the handler,
so safety holds no matter which agent calls them.

`propose_rotation` is propose-only by construction: it records a recommendation
for human approval and changes NOTHING. (Trade-placing write tools, when added,
will run TradeService / the risk gate the same way.)

Boundary: records proposals only; applies nothing; places orders NO.
"""

from __future__ import annotations

from src.agents.tools.base import Tool, ToolRegistry
from src.core.rotation import RotationService


def build_write_registry(rotation_service: RotationService) -> ToolRegistry:
    return ToolRegistry([
        Tool(
            "propose_rotation",
            "Propose enabling, disabling, or reweighting a trading strategy. This "
            "ONLY records a proposal for the human to approve from the phone -- it "
            "changes nothing and places no orders. Use after reviewing the scoreboard.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["enable", "disable", "reweight"]},
                    "strategy": {"type": "string", "description": "strategy name, e.g. breakout"},
                    "weight": {"type": "number", "description": "0..1 capital weight (reweight only)"},
                    "rationale": {"type": "string", "description": "one sentence why"},
                },
                "required": ["action", "strategy"],
            },
            handler=lambda action, strategy, weight=None, rationale="": rotation_service.propose(
                action, strategy, weight=weight, rationale=rationale
            ),
            write=True,
        ),
    ])
