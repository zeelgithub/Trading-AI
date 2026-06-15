"""Internal Haiku call used by every tool to compress raw research data into
a small structured JSON summary before it is returned to the calling agent.
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic

from .secrets import load_anthropic_api_key

MODEL = "claude-haiku-4-5-20251001"

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=load_anthropic_api_key())
    return _client


def summarize_to_json(system_prompt: str, raw_data: str, max_tokens: int = 200) -> dict:
    """Send raw_data to Haiku with system_prompt and parse its JSON reply."""
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": raw_data}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(text)


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Haiku did not return JSON: {text!r}")
    return json.loads(match.group(0))
