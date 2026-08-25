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


@dataclass(frozen=True)
class NotificationCredentials:
    """Telegram bot credentials for the notify layer. NOT trading creds: a
    holder of these can message you, never place an order. The token is
    repr-suppressed so it can't leak into logs."""

    bot_token: str = field(repr=False)
    allowed_chat_ids: tuple[int, ...] = ()
    api_base_url: str = "https://api.telegram.org"

    @property
    def configured(self) -> bool:
        return bool(self.bot_token)

    def is_allowed(self, chat_id: int | str) -> bool:
        """True if this chat/user id may command the bot. An empty allowlist
        denies everyone -- you must explicitly list your own id."""
        try:
            return int(chat_id) in self.allowed_chat_ids
        except (TypeError, ValueError):
            return False


def load_market_data_credentials() -> MarketDataCredentials:
    _ensure_env_loaded()
    return MarketDataCredentials(
        key_id=_require("ALPACA_API_KEY_ID"),
        secret_key=_require("ALPACA_API_SECRET_KEY"),
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
        feed=os.environ.get("ALPACA_DATA_FEED", "iex"),
    )


def _parse_chat_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue  # ignore malformed entries rather than crash the listener
    return tuple(ids)


def load_notification_credentials() -> NotificationCredentials:
    """Telegram bot token + allowed chat-id allowlist. Returns an unconfigured
    (empty-token) object if not set, so callers can no-op cleanly. Holds NO
    trading credentials."""
    _ensure_env_loaded()
    return NotificationCredentials(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        allowed_chat_ids=_parse_chat_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS")),
        api_base_url=os.environ.get("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
    )


@dataclass(frozen=True)
class RedditCredentials:
    """App-only (client_credentials) OAuth creds for read-only public Reddit
    listings -- no Reddit account password involved, no write scope
    requested. Used by the discovery layer's social-buzz source only; holds
    NO trading credentials."""

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    user_agent: str = "discovery-social-buzz/1.0"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def load_reddit_credentials() -> RedditCredentials:
    """Returns an unconfigured (empty) object if unset, so callers can no-op
    cleanly -- same posture as load_notification_credentials()."""
    _ensure_env_loaded()
    return RedditCredentials(
        client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
        client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
        user_agent=os.environ.get("REDDIT_USER_AGENT", "discovery-social-buzz/1.0"),
    )


def load_anthropic_api_key() -> str:
    """Claude API key for the cognitive-plane agents and research summarizers.
    This is an AI credential, NOT a trading credential -- a holder of it can
    reason and summarize, never place an order."""
    _ensure_env_loaded()
    return _require("ANTHROPIC_API_KEY")


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
