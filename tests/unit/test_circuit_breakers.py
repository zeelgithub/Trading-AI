"""Tests for the circuit breakers (kill switch, fat finger, rate limits, errors)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.risk.circuit_breakers import CircuitBreakers

_ET = ZoneInfo("America/New_York")


def _noon_et(y, m, d) -> float:
    return datetime(y, m, d, 12, 0, tzinfo=_ET).timestamp()


def make_breakers(**over):
    defaults = dict(
        max_daily_loss_pct=4.0,
        max_orders_per_minute=2,
        max_orders_per_day=3,
        max_consecutive_errors=3,
        fat_finger_price_band_pct=20.0,
    )
    defaults.update(over)
    return CircuitBreakers(**defaults)


def test_daily_loss_kill_switch():
    cb = make_breakers()
    assert cb.check_daily_loss(50000, 49000).ok          # 2% -> ok
    assert not cb.check_daily_loss(50000, 47000).ok       # 6% -> trip


def test_fat_finger_band():
    cb = make_breakers()
    assert cb.check_fat_finger(110, 100).ok               # 10% -> ok
    assert not cb.check_fat_finger(130, 100).ok           # 30% -> trip


def test_rate_limit_per_minute_and_expiry():
    cb = make_breakers()
    cb.register_order(now=1000.0)
    cb.register_order(now=1000.0)
    assert not cb.check_rate_limits(now=1000.0).ok        # 2 within the minute
    assert cb.check_rate_limits(now=1061.0).ok            # both aged out


def test_rate_limit_per_day():
    cb = make_breakers(max_orders_per_minute=100)
    for t in range(3):
        cb.register_order(now=1000.0 + t * 30)
    assert not cb.check_rate_limits(now=2000.0).ok        # 3/day cap hit


def test_rate_limit_per_day_resets_on_new_calendar_day_for_a_long_lived_process():
    """Reproduces the always-on Telegram listener: ONE CircuitBreakers for the
    life of the process, never explicitly told a new day started. The per-day
    cap must still reset on its own, or it becomes a lifetime cap that
    eventually vetoes every order forever, including on a fresh day."""
    cb = make_breakers(max_orders_per_minute=100, max_orders_per_day=3)
    day1 = _noon_et(2026, 1, 5)
    for t in range(3):
        cb.register_order(now=day1 + t)
    assert not cb.check_rate_limits(now=day1 + 100).ok   # day 1: cap hit

    day2 = _noon_et(2026, 1, 6)
    assert cb.check_rate_limits(now=day2).ok              # day 2: reset on its own
    cb.register_order(now=day2)
    assert cb.check_rate_limits(now=day2 + 1).ok           # only 1/3 used today


def test_register_order_also_rolls_the_day_before_check_rate_limits_is_called():
    """The rollover must not depend on check_rate_limits running first --
    register_order (called right after a successful submit) has to see it too."""
    cb = make_breakers(max_orders_per_minute=100, max_orders_per_day=1)
    day1 = _noon_et(2026, 1, 5)
    cb.register_order(now=day1)
    assert not cb.check_rate_limits(now=day1).ok

    day2 = _noon_et(2026, 1, 6)
    cb.register_order(now=day2)  # should count as day 2's 1st order, not day 1's 2nd
    assert cb.check_rate_limits(now=day2).ok is False  # day 2's own cap (1/day) now hit
    # but crucially it's a FRESH trip on day 2's count of 1, not an accumulated 2
    assert cb._orders_today == 1


def test_consecutive_errors():
    cb = make_breakers()
    assert cb.register_error().ok
    assert cb.register_error().ok
    assert not cb.register_error().ok                     # third -> trip
    cb.reset_errors()
    assert cb.register_error().ok
