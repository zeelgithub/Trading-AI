"""Tests for performance metrics."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from src.research.metrics import compute_metrics


def trade(pnl):
    return SimpleNamespace(pnl=pnl)


def test_total_return_and_drawdown():
    equity = pd.Series([100.0, 110.0, 105.0, 121.0])
    m = compute_metrics(equity, [])
    assert m["total_return"] == pytest.approx(0.21)
    assert m["max_drawdown"] == pytest.approx(105 / 110 - 1, abs=1e-4)  # -4.5%


def test_trade_stats():
    trades = [trade(100), trade(-40), trade(60), trade(-20)]
    equity = pd.Series([1000.0, 1100.0])
    m = compute_metrics(equity, trades)
    assert m["num_trades"] == 4
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(160 / 60, abs=1e-2)
    assert m["avg_win"] == pytest.approx(80.0)
    assert m["avg_loss"] == pytest.approx(-30.0)


def test_handles_empty():
    assert compute_metrics(pd.Series([100.0]), [])["num_trades"] == 0
