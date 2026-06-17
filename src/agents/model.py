"""
Model client -- cognitive plane.

A thin, neutral interface over the chat-completion model so the agent runtime
never imports the Anthropic SDK directly. That keeps the runtime fully testable
offline: tests inject a scripted fake implementing the same `respond` contract,
and no API key is ever required to run the suite.

Boundary: holds an AI credential only (never trading creds); places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A model's request to invoke one tool."""
    id: str
    name: str
    input: dict


@dataclass
class ModelResponse:
    """Normalized model turn: any free text, plus any tool calls it requested."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


@runtime_checkable
class ModelClient(Protocol):
    def respond(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> ModelResponse:
        ...


class AnthropicModel:
    """Real ModelClient backed by the Anthropic Messages API. The client is
    lazy-built so importing this module never needs a key."""

    def __init__(self, model: str, client: Any | None = None, api_key: str | None = None) -> None:
        self.model = model
        self._client = client
        self._api_key = api_key

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            from src.common.secrets import load_anthropic_api_key

            self._client = Anthropic(api_key=self._api_key or load_anthropic_api_key())
        return self._client

    def respond(self, *, system, messages, tools, max_tokens) -> ModelResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=max_tokens, system=system, messages=messages
        )
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in resp.content
            if b.type == "tool_use"
        ]
        return ModelResponse(text=text, tool_calls=calls, stop_reason=resp.stop_reason or "end_turn")
