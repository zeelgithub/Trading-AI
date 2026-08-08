"""Tests for the shared confirmation-candle rule (src/strategy/base.py) used
by all three strategies: a real-bodied candle in the right direction, not
just any tick that happened to close there."""

from __future__ import annotations

import pandas as pd

from src.strategy.base import bearish_confirmation, bullish_confirmation


def _bar(open_, high, low, close):
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


PREV = _bar(100, 101, 99, 100)


def test_strong_bullish_body_confirms():
    # body 4 / range 5 = 0.8 >= 0.5
    curr = _bar(open_=100, high=105, low=100, close=104)
    assert bullish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_weak_bullish_body_rejected():
    # body 0.5 / range 5 = 0.1 < 0.5 -- ticked green but no conviction
    curr = _bar(open_=100, high=105, low=100, close=100.5)
    assert not bullish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_bullish_needs_close_above_prior_close_even_with_strong_body():
    curr = _bar(open_=95, high=100, low=94, close=99)  # strong body, but 99 < prev.close(100)
    assert not bullish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_exact_threshold_boundary_passes():
    # body 2.5 / range 5 = exactly 0.5 -- boundary is inclusive (>=)
    curr = _bar(open_=100, high=105, low=100, close=102.5)
    assert bullish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_strong_bearish_body_confirms():
    curr = _bar(open_=100, high=100, low=95, close=96)  # body 4/range 5 = 0.8
    prev = _bar(100, 101, 99, 100)
    assert bearish_confirmation(curr, prev, min_body_ratio=0.5)


def test_weak_bearish_body_rejected():
    curr = _bar(open_=100, high=100.5, low=95, close=99.5)  # body 0.5/range 5.5
    prev = _bar(100, 101, 99, 100)
    assert not bearish_confirmation(curr, prev, min_body_ratio=0.5)


def test_zero_range_candle_never_confirms():
    # high == low: a real bar can't have zero range, but guard the division.
    curr = _bar(open_=100, high=100, low=100, close=100)
    assert not bullish_confirmation(curr, PREV, min_body_ratio=0.5)
    assert not bearish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_nan_range_never_confirms():
    curr = pd.Series({"open": 100, "high": float("nan"), "low": 99, "close": 101})
    assert not bullish_confirmation(curr, PREV, min_body_ratio=0.5)


def test_stricter_ratio_rejects_what_looser_ratio_allows():
    curr = _bar(open_=100, high=105, low=100, close=103)  # body 3/range 5 = 0.6
    assert bullish_confirmation(curr, PREV, min_body_ratio=0.5)
    assert not bullish_confirmation(curr, PREV, min_body_ratio=0.7)
