"""Scripted ModelClient fake for agent-runtime tests -- no API key, fully offline.

Replays a fixed list of ModelResponses and records every call so tests can assert
on what the runtime sent back (tool results, message history, model tier).
"""

from __future__ import annotations

from src.agents.model import ModelResponse, ToolCall


class ScriptedModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def respond(self, *, system, messages, tools, max_tokens) -> ModelResponse:
        self.calls.append({
            "system": system,
            "messages": [dict(m) for m in messages],
            "tools": tools,
            "max_tokens": max_tokens,
        })
        if not self._responses:
            raise AssertionError("ScriptedModel exhausted -- runtime called more times than scripted")
        return self._responses.pop(0)


def text_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, stop_reason="end_turn")


def tool_response(name: str, tool_input: dict, call_id: str = "t1", text: str = "") -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
        stop_reason="tool_use",
    )
