"""Tests for the PDT tracker."""

from __future__ import annotations

from datetime import date

from src.risk.pdt_tracker import PdtTracker


def test_above_threshold_is_unrestricted():
    t = PdtTracker(min_equity_for_daytrading=25000, max_day_trades_rolling_5d=3)
    for _ in range(10):
        t.register_day_trade(date(2026, 6, 12))
    assert t.can_day_trade(equity=30000, asof=date(2026, 6, 12)) is True


def test_below_threshold_enforces_cap():
    t = PdtTracker(min_equity_for_daytrading=25000, max_day_trades_rolling_5d=3)
    asof = date(2026, 6, 12)
    for _ in range(3):
        t.register_day_trade(asof)
    assert t.day_trades_in_window(asof) == 3
    assert t.can_day_trade(equity=20000, asof=asof) is False


def test_trades_outside_window_drop_off():
    t = PdtTracker(min_equity_for_daytrading=25000, max_day_trades_rolling_5d=3)
    asof = date(2026, 6, 12)
    t.register_day_trade(date(2026, 6, 1))    # ~9 business days back -> excluded
    t.register_day_trade(date(2026, 6, 11))   # 1 business day back -> included
    assert t.day_trades_in_window(asof) == 1
    assert t.can_day_trade(equity=20000, asof=asof) is True


def test_sync_broker_count_tops_up():
    t = PdtTracker(min_equity_for_daytrading=25000, max_day_trades_rolling_5d=3)
    asof = date(2026, 6, 12)
    t.sync_broker_count(broker_count=3, asof=asof)
    assert t.day_trades_in_window(asof) == 3
    assert t.can_day_trade(equity=20000, asof=asof) is False
