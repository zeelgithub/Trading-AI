"""
Secrets gate -- common layer.

The ONLY module that reads .env. Exposes market-data credentials and trading
credentials as SEPARATE objects so research/strategy/data code cannot obtain
order credentials. Secret values are never logged or shown in repr.

Boundary: trading creds handed only to src/execution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

_loaded = False


def _ensure_env_loaded() -> None:
    """Load .env into os.environ once. Falls back to existing env if absent."""
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
    except ImportError:
        pass  # python-dotenv optional; rely on the ambient environment
    _loaded = True


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class MarketDataCredentials:
    """Read-only data-feed credentials. Safe for the data/research layers."""

    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str
    feed: str


@dataclass(frozen=True)
class TradingCredentials:
    """Order-placing credentials. MUST stay inside src/execution only."""

    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str


def load_market_data_credentials() -> MarketDataCredentials:
    _ensure_env_loaded()
    return MarketDataCredentials(
        key_id=_require("ALPACA_API_KEY_ID"),
        secret_key=_require("ALPACA_API_SECRET_KEY"),
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
        feed=os.environ.get("ALPACA_DATA_FEED", "iex"),
    )


def load_trading_credentials() -> TradingCredentials:
    """Intended to be called ONLY from src/execution. Importing this elsewhere
    is a layer-boundary violation."""
    _ensure_env_loaded()
    return TradingCredentials(
        key_id=_require("ALPACA_API_KEY_ID"),
        secret_key=_require("ALPACA_API_SECRET_KEY"),
        base_url=os.environ.get(
            "ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets"
        ),
    )
