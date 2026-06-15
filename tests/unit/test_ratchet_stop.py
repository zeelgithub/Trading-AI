"""Tests for the ratchet stop engine -- the system's signature exit logic."""

from __future__ import annotations

import pytest

from src.common.models import Side
from src.risk.ratchet_stop import AtrRatchet, PercentRatchet, build_ratchet


def test_breakeven_lock_ladder_long_matches_spec():
    # $100 entry, 10% initial, lock arms at +20% -> $110 floor, +20% per step.
    r = PercentRatchet(
        entry=100.0, side=Side.LONG,
        initial_stop_pct=10.0, lock_trigger_pct=20.0,
        profit_lock_pct=10.0, step_pct=20.0,
    )
    assert r.stop == pytest.approx(90.0)        # initial
    assert not r.armed

    assert r.update(110.0) == pytest.approx(90.0)   # +10% -> not yet armed
    assert not r.armed

    assert r.update(120.0) == pytest.approx(110.0)  # +20% -> locks $110
    assert r.armed

    assert r.update(115.0) == pytest.approx(110.0)  # pullback -> stop holds
    assert r.update(140.0) == pytest.approx(130.0)  # +40% -> ratchets to $130
    assert r.update(130.0) == pytest.approx(130.0)  # never moves down

    assert r.is_hit(129.0)
    assert not r.is_hit(131.0)


def test_breakeven_lock_ladder_short_mirror():
    r = PercentRatchet(
        entry=100.0, side=Side.SHORT,
        initial_stop_pct=10.0, lock_trigger_pct=20.0,
        profit_lock_pct=10.0, step_pct=20.0,
    )
    assert r.stop == pytest.approx(110.0)
    assert r.update(90.0) == pytest.approx(110.0)   # -10% not armed
    assert r.update(80.0) == pytest.approx(90.0)    # -20% locks $90
    assert r.update(60.0) == pytest.approx(70.0)    # -40% -> $70
    assert r.update(70.0) == pytest.approx(70.0)    # monotonic


def test_fixed_percent_stop_for_mean_reversion():
    # No lock params -> a plain tight stop that never ratchets.
    r = PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=2.0)
    assert r.stop == pytest.approx(98.0)
    assert r.update(105.0) == pytest.approx(98.0)
    assert not r.armed


def test_atr_trailing_stop():
    r = AtrRatchet(
        entry=100.0, side=Side.LONG,
        atr_multiple_initial=2.0, atr_multiple_trail=1.5, atr=2.0,
    )
    assert r.stop == pytest.approx(96.0)             # 100 - 2*2
    assert r.update(110.0, atr=2.0) == pytest.approx(107.0)  # 110 - 1.5*2
    assert r.update(108.0, atr=2.0) == pytest.approx(107.0)  # holds


def test_build_ratchet_from_config_blocks():
    trend = build_ratchet(
        "trend_following",
        {"initial_stop_pct": 10, "lock_trigger_pct": 20, "profit_lock_pct": 10, "step_pct": 20},
        entry=100.0, side=Side.LONG,
    )
    assert isinstance(trend, PercentRatchet)
    assert trend.stop == pytest.approx(90.0)

    brk = build_ratchet(
        "breakout",
        {"atr_multiple_initial": 2.0, "atr_multiple_trail": 1.5},
        entry=100.0, side=Side.LONG, atr=3.0,
    )
    assert isinstance(brk, AtrRatchet)
    assert brk.stop == pytest.approx(94.0)

    with pytest.raises(ValueError):
        build_ratchet("breakout", {"atr_multiple_initial": 2.0}, 100.0, Side.LONG, atr=None)
