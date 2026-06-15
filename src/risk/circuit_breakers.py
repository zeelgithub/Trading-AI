"""
Circuit breakers -- risk layer.

Pre-trade and runtime guards: daily-loss kill switch, order rate limits,
consecutive-error counter, and a fat-finger price band. Any trip returns a
not-ok status; the orchestrator flips the state machine to HALTED in response.

Deterministic and clock-injectable (`now`) for testing.

Boundary: places orders NO.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BreakerStatus:
    ok: bool
    reason: str = ""


OK = BreakerStatus(True)


class CircuitBreakers:
    def __init__(
        self,
        max_daily_loss_pct: float,
        max_orders_per_minute: int,
        max_orders_per_day: int,
        max_consecutive_errors: int,
        fat_finger_price_band_pct: float,
    ) -> None:
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_orders_per_minute = max_orders_per_minute
        self.max_orders_per_day = max_orders_per_day
        self.max_consecutive_errors = max_consecutive_errors
        self.fat_finger_price_band_pct = fat_finger_price_band_pct

        self._order_times: deque[float] = deque()
        self._orders_today = 0
        self._consecutive_errors = 0

    # --- daily loss kill switch ---
    def check_daily_loss(self, start_equity: float, current_equity: float) -> BreakerStatus:
        if start_equity <= 0:
            return OK
        loss_pct = (start_equity - current_equity) / start_equity * 100.0
        if loss_pct >= self.max_daily_loss_pct:
            return BreakerStatus(
                False, f"daily loss {loss_pct:.2f}% >= limit {self.max_daily_loss_pct}%"
            )
        return OK

    # --- fat finger ---
    def check_fat_finger(self, order_price: float, last_price: float) -> BreakerStatus:
        if last_price <= 0:
            return OK
        deviation = abs(order_price - last_price) / last_price * 100.0
        if deviation > self.fat_finger_price_band_pct:
            return BreakerStatus(
                False,
                f"order price {order_price} is {deviation:.1f}% off last {last_price} "
                f"(band {self.fat_finger_price_band_pct}%)",
            )
        return OK

    # --- rate limits ---
    def check_rate_limits(self, now: float | None = None) -> BreakerStatus:
        now = now if now is not None else time.time()
        self._expire(now)
        if len(self._order_times) >= self.max_orders_per_minute:
            return BreakerStatus(False, f"rate limit: {self.max_orders_per_minute} orders/min")
        if self._orders_today >= self.max_orders_per_day:
            return BreakerStatus(False, f"rate limit: {self.max_orders_per_day} orders/day")
        return OK

    def register_order(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._order_times.append(now)
        self._orders_today += 1

    def reset_day(self) -> None:
        self._order_times.clear()
        self._orders_today = 0

    def _expire(self, now: float) -> None:
        while self._order_times and now - self._order_times[0] > 60.0:
            self._order_times.popleft()

    # --- error counter ---
    def register_error(self) -> BreakerStatus:
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_consecutive_errors:
            return BreakerStatus(
                False, f"{self._consecutive_errors} consecutive errors >= limit"
            )
        return OK

    def reset_errors(self) -> None:
        self._consecutive_errors = 0
