"""Unit tests for src/risk/correlation.py -- the correlation-aware tightening
RiskManager.evaluate() step 7.6 applies on top of the flat aggregate open-risk
cap (step 7.5, src/risk/exposure.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.risk.correlation import correlated_open_risk

LOOKBACK = 10


def _closes(returns, start=100.0):
    # N returns -> N+1 prices (a leading price plus one per return), so a
    # `returns` list of length `lookback` yields the lookback+1 closes
    # correlated_open_risk requires.
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return pd.Series(prices, index=pd.date_range("2024-01-01", periods=len(prices)))


def _pos(symbol, qty=10, entry=100.0, stop=90.0):
    return SimpleNamespace(symbol=symbol, qty=qty, ratchet=SimpleNamespace(entry=entry, stop=stop))


CANDIDATE_RETURNS = [0.02, -0.01, 0.03, -0.02, 0.01, 0.02, -0.03, 0.01, 0.02, -0.01]


def test_no_open_positions_is_zero():
    closes = {"AAPL": _closes(CANDIDATE_RETURNS)}
    assert correlated_open_risk("AAPL", [], closes, lookback=LOOKBACK) == 0.0


def test_highly_correlated_position_counts():
    # Proportionally-scaled returns -> correlation is exactly 1.0 (a positive
    # linear transform of returns doesn't change their correlation).
    closes = {
        "AAPL": _closes(CANDIDATE_RETURNS),
        "MSFT": _closes([r * 2 for r in CANDIDATE_RETURNS]),
    }
    positions = [_pos("MSFT", qty=10, entry=100.0, stop=90.0)]  # risk = 10 * 10 = 100
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 100.0


def test_anticorrelated_position_excluded():
    # Negated returns -> correlation is -1.0. An inverse mover doesn't crash
    # alongside the candidate, so it must NOT count toward the correlated cap
    # even though |corr| is high.
    closes = {
        "AAPL": _closes(CANDIDATE_RETURNS),
        "SQQQ": _closes([-r for r in CANDIDATE_RETURNS]),
    }
    positions = [_pos("SQQQ", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_zero_variance_position_is_nan_correlation_and_excluded():
    closes = {
        "AAPL": _closes(CANDIDATE_RETURNS),
        "FLAT": _closes([0.0] * len(CANDIDATE_RETURNS)),
    }
    positions = [_pos("FLAT", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_position_missing_from_closes_by_symbol_is_excluded_not_assumed_correlated():
    closes = {"AAPL": _closes(CANDIDATE_RETURNS)}  # no entry for GOOG at all
    positions = [_pos("GOOG", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_position_with_too_little_history_is_excluded():
    closes = {
        "AAPL": _closes(CANDIDATE_RETURNS),
        "IPO": _closes(CANDIDATE_RETURNS[:3]),  # far fewer than lookback+1 prices
    }
    positions = [_pos("IPO", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_candidate_missing_from_closes_by_symbol_is_zero():
    closes = {"MSFT": _closes(CANDIDATE_RETURNS)}  # no AAPL entry at all
    positions = [_pos("MSFT", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_candidate_symbol_already_held_is_skipped_not_double_counted():
    closes = {"AAPL": _closes(CANDIDATE_RETURNS)}
    positions = [_pos("AAPL", qty=10, entry=100.0, stop=90.0)]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 0.0


def test_sums_across_multiple_correlated_positions_and_skips_uncorrelated():
    closes = {
        "AAPL": _closes(CANDIDATE_RETURNS),
        "MSFT": _closes([r * 2 for r in CANDIDATE_RETURNS]),   # corr 1.0 -> risk 100 counts
        "GOOG": _closes([r * 0.5 for r in CANDIDATE_RETURNS]),  # corr 1.0 -> risk 40 counts
        "SQQQ": _closes([-r for r in CANDIDATE_RETURNS]),       # corr -1.0 -> excluded
    }
    positions = [
        _pos("MSFT", qty=10, entry=100.0, stop=90.0),   # risk 100
        _pos("GOOG", qty=4, entry=100.0, stop=90.0),    # risk 40
        _pos("SQQQ", qty=10, entry=100.0, stop=90.0),   # excluded
    ]
    total = correlated_open_risk("AAPL", positions, closes, lookback=LOOKBACK, threshold=0.6)
    assert total == 140.0
