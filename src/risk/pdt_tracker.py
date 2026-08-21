"""
PDT tracker -- risk layer.

Tracks day-trade dates against the rolling 5-business-day window. When account
equity is at/above the threshold (default $25k) the rule does not bind; below it,
the tracker vetoes a day trade that would exceed `max_day_trades_rolling_5d`.

Reconcile `register_day_trade` / `broker_count` with Alpaca's daytrade_count on
each cycle so the local count never drifts from broker truth.

Boundary: places orders NO.
"""

from __future__ import annotations

from collections import deque
from datetime import date

import numpy as np


class PdtTracker:
    def __init__(
        self,
        min_equity_for_daytrading: float = 25000.0,
        max_day_trades_rolling_5d: int = 3,
        window_business_days: int = 5,
    ) -> None:
        self.min_equity = min_equity_for_daytrading
        self.max_day_trades = max_day_trades_rolling_5d
        self.window = window_business_days
        self._dates: deque[date] = deque()

    def register_day_trade(self, trade_date: date) -> None:
        self._dates.append(trade_date)

    def day_trades_in_window(self, asof: date) -> int:
        """Count recorded day trades within the trailing N business days of asof."""
        count = 0
        for d in self._dates:
            if d > asof:
                continue
            # busday_count(d, asof) excludes asof itself, so a trade made ON
            # asof already elapses 0 -- exactly what we want (day 0 of the
            # window), no separate same-day term needed.
            elapsed = int(np.busday_count(d, asof))
            if elapsed < self.window:
                count += 1
        return count

    def can_day_trade(self, equity: float, asof: date) -> bool:
        """True if a new day trade is permitted under the PDT rule."""
        if equity >= self.min_equity:
            return True
        return self.day_trades_in_window(asof) < self.max_day_trades

    def sync_broker_count(self, broker_count: int, asof: date) -> None:
        """Reconcile to broker truth: if Alpaca reports more day trades than we
        have locally in the window, top up so we never under-count."""
        local = self.day_trades_in_window(asof)
        for _ in range(max(0, broker_count - local)):
            self._dates.append(asof)
