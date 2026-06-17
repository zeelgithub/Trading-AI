"""
Natural-language command parsing -- cognitive plane facade.

Turns a free-form Telegram message ("grab me 10 nvidia, tight 5% stop") into the
structured command dict the listener already understands, using the nl_router
agent. If the Claude API key is absent or the agent errors, it falls back to the
caller-supplied parser (the existing local regex) so the phone surface NEVER
goes dark -- it just degrades to the simpler parser.

Output never originates an order: it only names an intent. The listener still
routes every write through the confirm + TradeService risk gate.

Boundary: parsing only; places orders NO.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.agents.catalog import NL_ROUTER
from src.agents.dispatch import AgentRequest, Dispatcher
from src.agents.model import AnthropicModel, ModelClient
from src.agents.tools.base import ToolRegistry
from src.common.logging import get_logger

log = get_logger("agents.nl")

FallbackFn = Callable[[str], Optional[dict]]
ModelFactory = Callable[[str], ModelClient]

_KNOWN = {"status", "pending", "buy", "close", "flatten", "halt", "reset", "run", "help", "unknown"}


class NLCommandParser:
    """Parse Telegram text into a command dict via the nl_router agent, with a
    regex fallback. Construct once and reuse (the dispatcher is built lazily)."""

    def __init__(
        self,
        fallback: FallbackFn | None = None,
        model_factory: ModelFactory | None = None,
        available: bool | None = None,
    ) -> None:
        self.fallback = fallback
        self._model_factory = model_factory
        self._available = available
        self._dispatcher: Dispatcher | None = None

    def available(self) -> bool:
        """True if the smart parser can run (a Claude key is present, or a model
        factory was injected for tests)."""
        if self._available is not None:
            return self._available
        if self._model_factory is not None:
            return True
        try:
            from src.common.secrets import load_anthropic_api_key

            load_anthropic_api_key()
            return True
        except Exception:
            return False

    def parse(self, text: str) -> dict | None:
        if self.available():
            try:
                result = self._dispatch().dispatch(AgentRequest(kind="nl_command", payload=text))
                if result.ok and result.output:
                    return _normalize(result.output)
                log.warning("nl_router did not parse (%s); falling back", result.error)
            except Exception as exc:  # never let a model failure break the phone
                log.warning("nl_router error (%s); falling back", exc)
        return self.fallback(text) if self.fallback is not None else None

    def _dispatch(self) -> Dispatcher:
        if self._dispatcher is None:
            factory = self._model_factory or (lambda model_id: AnthropicModel(model_id))
            self._dispatcher = Dispatcher(
                profiles={"nl_router": NL_ROUTER},
                registry=ToolRegistry(),
                model_factory=factory,
                routes={"nl_command": "nl_router"},
            )
        return self._dispatcher


def _normalize(out: dict) -> dict:
    """Coerce the agent's JSON into the exact shape the listener consumes,
    defensively (a model can still hand back a stray type)."""
    cmd = str(out.get("cmd", "unknown")).lower().strip()
    if cmd == "positions":
        cmd = "status"
    if cmd not in _KNOWN:
        return {"cmd": "unknown", "reply": out.get("reply") or "I didn't understand that."}

    if cmd == "buy":
        result: dict = {"cmd": "buy", "sym": str(out.get("sym") or "").strip().upper()}
        try:
            result["qty"] = int(out.get("qty"))
        except (TypeError, ValueError):
            result["qty"] = 0
        try:
            result["stop"] = float(out["stop"]) if out.get("stop") is not None else 10.0
        except (TypeError, ValueError):
            result["stop"] = 10.0
        return result
    if cmd == "close":
        return {"cmd": "close", "sym": str(out.get("sym") or "").strip().upper()}
    if cmd == "unknown":
        return {"cmd": "unknown", "reply": out.get("reply") or "I didn't understand that."}
    return {"cmd": cmd}
