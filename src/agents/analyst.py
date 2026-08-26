"""
Strategy analyst -- cognitive plane facade.

Runs the strategy_analyst agent: it reads the scoreboard + positions through the
read tools and may record rotation recommendations through propose_rotation (a
write tool that changes nothing -- you approve from the phone). This is the
vertical slice that proves the architecture end-to-end: read tools -> reasoning
agent -> gated write -> human approval.

Boundary: proposes only; applies nothing; places orders NO.
"""

from __future__ import annotations

from collections.abc import Callable

from src.agents.catalog import STRATEGY_ANALYST
from src.agents.dispatch import AgentRequest, Dispatcher, build_dispatcher
from src.agents.model import ModelClient
from src.agents.runtime import AgentResult
from src.agents.tools.base import ToolRegistry
from src.agents.tools.reads import build_read_registry
from src.agents.tools.writes import build_write_registry
from src.core.rotation import RotationService

ModelFactory = Callable[[str], ModelClient]

_DEFAULT_TASK = "Review the strategy scoreboard and propose rotations only if warranted."


def build_analyst_registry(rotation_service: RotationService) -> ToolRegistry:
    """Read tools + the propose_rotation write tool, merged into one registry."""
    reads = build_read_registry()
    writes = build_write_registry(rotation_service)
    return ToolRegistry([*reads.tools(), *writes.tools()])


class StrategyAnalyst:
    def __init__(
        self,
        rotation_service: RotationService,
        model_factory: ModelFactory | None = None,
        audit=None,
    ) -> None:
        self.registry = build_analyst_registry(rotation_service)
        self._model_factory = model_factory
        self.audit = audit
        self._dispatcher: Dispatcher | None = None

    def review(self, task: str | None = None) -> AgentResult:
        return self._dispatch().dispatch(
            AgentRequest(kind="strategy_review", payload=task or _DEFAULT_TASK)
        )

    def _dispatch(self) -> Dispatcher:
        if self._dispatcher is None:
            self._dispatcher = build_dispatcher(
                profiles={"strategy_analyst": STRATEGY_ANALYST},
                registry=self.registry,
                routes={"strategy_review": "strategy_analyst"},
                model_factory=self._model_factory,
                audit=self.audit,
            )
        return self._dispatcher
