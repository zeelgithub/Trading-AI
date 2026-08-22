"""
Dispatcher -- cognitive plane.

Deterministic control flow (decision #3): a trigger's `kind` maps through a
plain table to exactly ONE agent profile. No LLM decides what runs -- the
dynamism lives INSIDE each agent's reasoning loop, not in the routing. This is
what keeps the system observable, cheap, and safe while still being adaptive.

A trigger can be a Telegram message, a scheduled job, or an event off the bus;
the dispatcher does not care where it came from.

Boundary: routes only; places orders NO.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.agents.model import ModelClient
from src.agents.profiles import AgentProfile
from src.agents.runtime import AgentResult, run_agent
from src.agents.tools.base import ToolRegistry

ModelFactory = Callable[[str], ModelClient]


@dataclass
class AgentRequest:
    kind: str
    payload: object  # str (raw text) or dict (structured)


class Dispatcher:
    def __init__(
        self,
        profiles: dict[str, AgentProfile],
        registry: ToolRegistry,
        model_factory: ModelFactory,
        routes: dict[str, str] | None = None,
        audit=None,
    ) -> None:
        self.profiles = dict(profiles)
        self.registry = registry
        self.model_factory = model_factory  # (model_id) -> ModelClient
        self.routes = dict(routes or {})
        self.audit = audit

    def route(self, kind: str) -> str | None:
        """The profile name a kind routes to, or None if unrouted."""
        return self.routes.get(kind)

    def dispatch(self, request: AgentRequest) -> AgentResult:
        profile_name = self.routes.get(request.kind)
        if profile_name is None:
            return AgentResult(False, error=f"no route for kind {request.kind!r}")
        profile = self.profiles.get(profile_name)
        if profile is None:
            return AgentResult(False, error=f"unknown profile {profile_name!r}")
        model = self.model_factory(profile.model)
        if self.audit is not None:
            self.audit.record("agent_dispatch", kind=request.kind, profile=profile_name)
        return run_agent(profile, request.payload, model=model,
                         registry=self.registry, audit=self.audit)
