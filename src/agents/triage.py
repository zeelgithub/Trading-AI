"""
Anomaly triage -- cognitive plane facade.

Runs the anomaly_triage agent: it reads the halt state + recent audit trail and
returns a structured diagnosis + recommendation. It is diagnose-and-escalate
ONLY -- it never clears a halt or places a trade (the deterministic SelfHealer
owns any verified auto-resume).

Boundary: read-only reasoning; places orders NO.
"""

from __future__ import annotations

from collections.abc import Callable

from src.agents.catalog import ANOMALY_TRIAGE
from src.agents.dispatch import AgentRequest, Dispatcher
from src.agents.model import AnthropicModel, ModelClient
from src.agents.runtime import AgentResult
from src.agents.tools.reads import build_read_registry

ModelFactory = Callable[[str], ModelClient]

_DEFAULT_TASK = "The bot has HALTED. Diagnose what happened and recommend the safest next step."


class AnomalyTriage:
    def __init__(self, model_factory: ModelFactory | None = None, audit=None) -> None:
        self._model_factory = model_factory
        self.audit = audit
        self._dispatcher: Dispatcher | None = None

    def diagnose(self, task: str | None = None) -> AgentResult:
        return self._dispatch().dispatch(
            AgentRequest(kind="anomaly", payload=task or _DEFAULT_TASK)
        )

    def _dispatch(self) -> Dispatcher:
        if self._dispatcher is None:
            factory = self._model_factory or (lambda model_id: AnthropicModel(model_id))
            self._dispatcher = Dispatcher(
                profiles={"anomaly_triage": ANOMALY_TRIAGE},
                registry=build_read_registry(),
                model_factory=factory,
                routes={"anomaly": "anomaly_triage"},
                audit=self.audit,
            )
        return self._dispatcher
