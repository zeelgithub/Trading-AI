"""Tests for walk-forward / out-of-sample fold evaluation.

Drives the REAL backtester (regime + strategy + risk + ratchet) via
evaluate_walk_forward, checking that folds are chronologically isolated --
a signal in fold 2 must not be visible to (or entered during) fold 1, and a
position must not leak across a fold boundary.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.research.walkforward import WalkForwardReport, evaluate_walk_forward
from tests.unit.synth import make_features

_TREND_PATTERN = [
    {"open": 97, "high": 97.5, "low": 96.5, "close": 97},
    {"open": 98, "high": 98.5, "low": 97.5, "close": 98},
    {"open": 99, "high": 100.5, "low": 99.0, "close": 100,
     "ema20": 99.5, "ema50": 95, "ema200": 90, "rsi": 55,
     "volume": 2e6, "vol_sma": 1e6, "adx": 35, "atr": 1.0},
    {"open": 100, "high": 106, "low": 100, "close": 105},
    {"open": 105, "high": 116, "low": 104, "close": 115},
    {"open": 115, "high": 126, "low": 114, "close": 125},
    {"open": 124, "high": 124, "low": 109, "close": 112},
]


def _two_fold_frame() -> pd.DataFrame:
    """The same winning-trend pattern twice back-to-back (14 bars): one
    self-contained signal->fill->stop-out cycle per half, so a 2-fold split
    should find exactly one trade in each fold if -- and only if -- folds
    are properly isolated from each other."""
    df = make_features(_TREND_PATTERN + _TREND_PATTERN)
    df.index = pd.bdate_range(start="2024-01-02", periods=len(df))
    return df


def test_two_independent_signals_land_in_their_own_folds():
    report = evaluate_walk_forward(
        {"TEST": _two_fold_frame()}, n_folds=2, n_bootstrap=200,
    )
    assert isinstance(report, WalkForwardReport)
    assert report.n_folds == 2
    trend_folds = [f for f in report.folds if f.strategy == "trend_following"]
    assert len(trend_folds) == 2
    assert {f.fold for f in trend_folds} == {1, 2}
    assert all(f.num_trades == 1 for f in trend_folds)
    # Fold windows must be chronologically disjoint and non-overlapping.
    fold1, fold2 = sorted(trend_folds, key=lambda f: f.fold)
    assert fold1.end < fold2.start


def test_too_little_history_returns_empty_report_not_a_crash():
    tiny = make_features(_TREND_PATTERN[:3])
    tiny.index = pd.bdate_range(start="2024-01-02", periods=len(tiny))
    report = evaluate_walk_forward({"TEST": tiny}, n_folds=4, n_bootstrap=200)
    assert report.folds == []
    assert "no folds produced" in report.text()


def test_n_folds_below_two_rejected():
    with pytest.raises(ValueError):
        evaluate_walk_forward({"TEST": _two_fold_frame()}, n_folds=1)


def test_report_text_contains_holdout_framing():
    report = evaluate_walk_forward(
        {"TEST": _two_fold_frame()}, n_folds=2, n_bootstrap=200,
    )
    text = report.text()
    assert "WALK-FORWARD" in text
    assert "holdout" in text
