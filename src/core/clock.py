"""
Market clock -- core layer.

Lightweight US-equities session awareness. The authoritative source is the
broker clock (Alpaca), which handles holidays and half-days; pass a broker that
exposes is_open via `from_broker`. The local fallback only checks weekday +
regular-hours window and does NOT know holidays -- use it for tests/offline.

Boundary: read-only.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def is_market_open_local(now: datetime | None = None) -> bool:
    """Local approximation: weekdays 09:30-16:00 ET. Ignores holidays."""
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(ET)
    if et.weekday() >= 5:  # Sat/Sun
        return False
    return _OPEN <= et.time() < _CLOSE


class MarketClock:
    """Session helper. Prefers the broker clock when available."""

    def __init__(self, broker_clock=None) -> None:
        self._broker_clock = broker_clock  # optional callable -> bool

    def is_open(self, now: datetime | None = None) -> bool:
        if self._broker_clock is not None:
            return bool(self._broker_clock())
        return is_market_open_local(now)
