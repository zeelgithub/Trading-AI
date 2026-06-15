"""Tests for risk-based position sizing."""

from __future__ import annotations

from src.risk.position_sizer import size_position


def test_risk_cap_drives_size():
    # risk $ = 50000 * 1% = 500; risk/share = |100-90| = 10 -> 50 shares.
    # max position = 10% * 50000 / 100 = 50 -> tie. buying power not binding.
    res = size_position(50000, 100, 90, per_trade_risk_pct=1.0, max_position_pct=10.0)
    assert res.qty == 50


def test_max_position_cap_binds_when_stop_is_tight():
    # tight stop -> risk allows many shares, but max position caps it.
    res = size_position(50000, 100, 99, per_trade_risk_pct=1.0, max_position_pct=10.0)
    assert res.qty == 50
    assert res.capped_by == "max_position"


def test_buying_power_cap():
    res = size_position(
        50000, 100, 90, per_trade_risk_pct=5.0, max_position_pct=50.0, buying_power=1000
    )
    assert res.qty == 10
    assert res.capped_by == "buying_power"


def test_zero_risk_distance_refuses_to_size():
    res = size_position(50000, 100, 100, per_trade_risk_pct=1.0, max_position_pct=10.0)
    assert res.qty == 0
    assert res.capped_by == "risk"
