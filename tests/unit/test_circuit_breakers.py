"""Tests for the circuit breakers (kill switch, fat finger, rate limits, errors)."""

from __future__ import annotations

from src.risk.circuit_breakers import CircuitBreakers


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


def test_consecutive_errors():
    cb = make_breakers()
    assert cb.register_error().ok
    assert cb.register_error().ok
    assert not cb.register_error().ok                     # third -> trip
    cb.reset_errors()
    assert cb.register_error().ok
