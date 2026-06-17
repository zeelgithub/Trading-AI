"""Tests for per-strategy attribution."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.research import attribution


def trade(strategy, pnl, return_pct, exit_date):
    return SimpleNamespace(strategy=strategy, pnl=pnl, return_pct=return_pct, exit_date=exit_date)


def test_per_strategy_breakdown_segments_by_strategy():
    trades = [
        trade("trend_following", 100, 0.10, date(2024, 1, 1)),
        trade("trend_following", -40, -0.04, date(2024, 2, 1)),
        trade("mean_reversion", 30, 0.03, date(2024, 1, 15)),
    ]
    bd = attribution.per_strategy_breakdown(trades)
    assert set(bd) == {"trend_following", "mean_reversion"}

    tf = bd["trend_following"]
    assert tf["num_trades"] == 2
    assert tf["total_pnl"] == 60
    assert tf["win_rate"] == 0.5
    assert tf["profit_factor"] == pytest.approx(100 / 40)

    mr = bd["mean_reversion"]
    assert mr["num_trades"] == 1
    assert mr["profit_factor"] == float("inf")  # no losses


def test_temporal_consistency_flags_one_period_luck():
    # 6 losing trades early, then 2 big winners -> profit concentrated in one bucket.
    trades = [trade("x", -10, -0.01, date(2024, 1, 1 + i)) for i in range(6)]
    trades += [trade("x", 100, 0.10, date(2024, 6, 1 + i)) for i in range(2)]
    out = attribution.temporal_consistency(trades, n_buckets=4)["x"]
    assert out["buckets"] == 4
    assert out["positive"] == 1
    assert out["consistency"] == 0.25


def test_temporal_consistency_consistent_strategy_scores_high():
    trades = [trade("x", 10, 0.01, date(2024, 1, 1 + i)) for i in range(8)]
    out = attribution.temporal_consistency(trades, n_buckets=4)["x"]
    assert out["consistency"] == 1.0


def test_temporal_consistency_too_few_trades():
    out = attribution.temporal_consistency([trade("x", 10, 0.01, date(2024, 1, 1))], n_buckets=4)["x"]
    assert out["consistency"] is None
    assert "too few" in out["note"]
