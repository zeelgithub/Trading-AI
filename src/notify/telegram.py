"""
Telegram client + notifier -- notify layer.

A thin synchronous wrapper over the Telegram Bot HTTP API (long-polling, no
public URL required) plus a best-effort Notifier used by the orchestrator and
trade service. Uses `requests`; no async dependency.

Design rules:
  - Holds ONLY the bot token (NotificationCredentials), never trading creds.
  - Every send is best-effort: a Telegram outage must NEVER affect trading, so
    failures are logged and swallowed.
  - The token is never logged (it is repr-suppressed on the creds object and we
    never format it into messages).

Boundary: places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.common.config import Config
from src.common.logging import get_logger
from src.common.secrets import NotificationCredentials, load_notification_credentials

log = get_logger("notify")

# An inline keyboard is a list of rows; each row is a list of (label, data) tuples.
Button = tuple[str, str]
Keyboard = Sequence[Sequence[Button]]


def _keyboard(buttons: Keyboard | None) -> dict | None:
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for (label, data) in row]
            for row in buttons
        ]
    }


class TelegramClient:
    """Minimal Bot API surface: send / edit messages, answer callbacks, poll.

    `session` lets tests inject a fake transport (anything exposing
    `.post(url, json=, timeout=)` -> object with `.json()`).
    """

    def __init__(
        self,
        creds: NotificationCredentials | None = None,
        session: Any | None = None,
    ) -> None:
        self.creds = creds or load_notification_credentials()
        self._session = session

    def _get_session(self):
        if self._session is None:
            import requests  # lazy: keep module import light / offline-friendly

            self._session = requests.Session()
        return self._session

    def _api(self, method: str, payload: dict, timeout: float = 30.0) -> dict:
        url = f"{self.creds.api_base_url}/bot{self.creds.bot_token}/{method}"
        resp = self._get_session().post(url, json=payload, timeout=timeout)
        data = resp.json()
        if not data.get("ok", False):
            raise RuntimeError(f"telegram {method} failed: {data.get('description')}")
        return data.get("result", {})

    def send_message(
        self, chat_id: int | str, text: str, buttons: Keyboard | None = None
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        kb = _keyboard(buttons)
        if kb:
            payload["reply_markup"] = kb
        return self._api("sendMessage", payload)

    def edit_message(
        self, chat_id: int | str, message_id: int, text: str, buttons: Keyboard | None = None
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        kb = _keyboard(buttons)
        if kb:
            payload["reply_markup"] = kb
        return self._api("editMessageText", payload)

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> dict:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._api("answerCallbackQuery", payload)

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        # HTTP timeout must outlast the long-poll timeout, else we cut it short.
        return self._api("getUpdates", payload, timeout=timeout + 10)


class Notifier:
    """Best-effort push notifications to the allow-listed chats."""

    def __init__(
        self,
        client: TelegramClient,
        chat_ids: Sequence[int],
        enabled: bool = True,
        events: Sequence[str] | None = None,
    ) -> None:
        self.client = client
        self.chat_ids = list(chat_ids)
        self.enabled = enabled
        self.events = set(events) if events is not None else None  # None => all events

    def _broadcast(self, text: str, buttons: Keyboard | None = None) -> None:
        if not self.enabled or not self.chat_ids:
            return
        for chat_id in self.chat_ids:
            try:
                self.client.send_message(chat_id, text, buttons)
            except Exception as exc:  # never let a notify failure affect trading
                log.warning("telegram send failed (chat %s): %s", chat_id, exc)

    def alert(self, event: str, detail: str = "") -> None:
        if self.events is not None and event not in self.events:
            return
        emoji = _EVENT_EMOJI.get(event, "🔔")
        text = f"{emoji} {event.replace('_', ' ').upper()}"
        if detail:
            text += f"\n{detail}"
        self._broadcast(text)

    def proposal(self, proposal) -> None:
        """Push one trade proposal with Approve / Deny buttons."""
        text = f"🟡 PROPOSED TRADE\n{proposal.summary()}\n\nApprove to place (paper)."
        buttons = [[("✅ Approve", f"approve:{proposal.id}"), ("❌ Deny", f"deny:{proposal.id}")]]
        self._broadcast(text, buttons)

    def send(self, text: str, buttons: Keyboard | None = None) -> None:
        """Push a free-form message (used by the discovery digest). Best-effort,
        not event-filtered."""
        self._broadcast(text, buttons)


class NullNotifier:
    """No-op notifier used when Telegram is unconfigured or in tests."""

    enabled = False

    def alert(self, event: str, detail: str = "") -> None:  # noqa: D401
        return None

    def proposal(self, proposal) -> None:
        return None

    def send(self, text: str, buttons=None) -> None:
        return None


_EVENT_EMOJI = {
    "halt": "🛑",
    "kill_switch": "🛑",
    "reconciliation_mismatch": "⚠️",
    "reconcile_mismatch": "⚠️",
    "fill": "✅",
    "position_opened": "✅",
    "daily_summary": "📊",
}


def build_notifier(config: Config) -> "Notifier | NullNotifier":
    """Construct a Notifier from config + env, or a NullNotifier when Telegram
    is not configured or alerts are disabled. Never raises."""
    try:
        creds = load_notification_credentials()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("could not load notification creds: %s", exc)
        return NullNotifier()

    enabled = bool(config.get("settings.alerts.enabled", True))
    if not creds.configured or not creds.allowed_chat_ids or not enabled:
        return NullNotifier()

    events = config.get("settings.alerts.on", None)
    return Notifier(
        client=TelegramClient(creds),
        chat_ids=list(creds.allowed_chat_ids),
        enabled=enabled,
        events=events,
    )
