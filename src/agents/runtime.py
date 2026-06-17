"""
Agent runtime -- cognitive plane.

The generic, short-lived tool-use loop every agent runs through. One call =
one task: build a fresh message list from the profile's system prompt + the task
payload, let the model think and (optionally) call read/write tools, feed results
back, and stop when it returns a final JSON object -- or when the step cap trips.

Context is bounded by construction: a fresh `messages` list per call, a hard
`max_steps`, and tool results that are compact JSON (the MCP read servers
pre-compress raw data). Nothing persists between calls.

Boundary: drives tools only; holds no trading creds; places orders NO.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.agents.model import ModelClient, ModelResponse
from src.agents.profiles import AgentProfile
from src.agents.tools.base import ToolRegistry
from src.common.logging import get_logger

log = get_logger("agents.runtime")


@dataclass
class AgentResult:
    ok: bool
    output: dict | None = None
    error: str | None = None
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)  # tool names invoked, for audit
    text: str = ""


def run_agent(
    profile: AgentProfile,
    payload,
    *,
    model: ModelClient,
    registry: ToolRegistry,
    audit=None,
) -> AgentResult:
    """Run one agent task to completion. `payload` is a str (raw user text) or a
    dict (structured input, JSON-encoded for the model)."""
    tool_specs = registry.specs(profile.tool_names) if profile.tool_names else []
    user_content = payload if isinstance(payload, str) else json.dumps(payload)
    messages: list[dict] = [{"role": "user", "content": user_content}]
    invoked: list[str] = []

    for step in range(1, profile.max_steps + 1):
        resp = model.respond(
            system=profile.system_prompt,
            messages=messages,
            tools=tool_specs,
            max_tokens=profile.max_tokens,
        )

        if not resp.tool_calls:
            data = _extract_json(resp.text)
            if data is None:
                return AgentResult(False, error="agent did not return parseable JSON",
                                   steps=step, tool_calls=invoked, text=resp.text)
            return AgentResult(True, output=data, steps=step, tool_calls=invoked, text=resp.text)

        # Record the assistant turn, then run each requested tool and feed results back.
        messages.append({"role": "assistant", "content": _assistant_blocks(resp)})
        result_blocks = []
        for call in resp.tool_calls:
            invoked.append(call.name)
            tool = registry.get(call.name)
            if tool is None:
                out = {"error": f"unknown tool {call.name!r}"}
            else:
                try:
                    out = tool.run(**call.input)
                except Exception as exc:  # isolate a bad tool call; let the agent react
                    log.warning("tool %s failed: %s", call.name, exc)
                    out = {"error": f"tool failed: {exc}"}
            if audit is not None:
                audit.record("agent_tool", agent=profile.name, tool=call.name,
                             ok=("error" not in out))
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(out),
            })
        messages.append({"role": "user", "content": result_blocks})

    return AgentResult(False, error=f"max steps ({profile.max_steps}) exceeded",
                       steps=profile.max_steps, tool_calls=invoked)


def _assistant_blocks(resp: ModelResponse) -> list[dict]:
    blocks: list[dict] = []
    if resp.text:
        blocks.append({"type": "text", "text": resp.text})
    for call in resp.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
    return blocks


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
