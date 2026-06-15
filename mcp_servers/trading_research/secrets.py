"""Credential loading for the trading-research MCP server.

This server is OUTSIDE the bot's execution path -- it never holds trading
credentials. It needs:
  - ANTHROPIC_API_KEY, for internal Haiku compression calls.
  - SEC_USER_AGENT, a descriptive User-Agent SEC EDGAR requires on requests.

Read-only Alpaca market-data credentials come from
src.common.secrets.load_market_data_credentials -- safe to reuse here.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

_loaded = False


def _ensure_env_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
    except ImportError:
        pass
    _loaded = True


def load_anthropic_api_key() -> str:
    _ensure_env_loaded()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY in .env -- required for internal Haiku "
            "summarization calls in mcp_servers/trading_research."
        )
    return key


def load_sec_user_agent() -> str:
    _ensure_env_loaded()
    return os.environ.get(
        "SEC_USER_AGENT",
        "trading-research-mcp/1.0 (set SEC_USER_AGENT in .env with a contact email)",
    )
