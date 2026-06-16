"""
Notify layer -- the phone control surface (Telegram).

Sends alerts and trade proposals to the user's phone and receives commands /
approvals back. This layer holds ONLY the Telegram bot token (a notification
credential), never trading credentials, and never calls the broker directly:
commands are routed to src/core/trade_service, which runs them through the risk
gate -> execution. See docs/SAFEGUARDS.md.

Boundary: places orders NO, holds trading credentials NO.
"""

from src.notify.telegram import (
    NullNotifier,
    Notifier,
    TelegramClient,
    build_notifier,
)

__all__ = ["TelegramClient", "Notifier", "NullNotifier", "build_notifier"]
