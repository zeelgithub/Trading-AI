"""Tests for significance testing (bootstrap, PSR, Sidak, benchmark)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research import significance


def test_bootstrap_detects_real_edge():
    rng = np.random.default_rng(1)
    rets = list(rng.normal(0.05, 0.02, 200))  # clearly positive mean
    out = significance.bootstrap_pvalue(rets, n_resamples=2000, seed=0)
    assert out["p_value"] < 0.05
    assert out["ci_low"] > 0
    assert out["n"] == 200


def test_bootstrap_no_edge_gives_high_pvalue():
    rng = np.random.default_rng(2)
    rets = list(rng.normal(0.0, 0.05, 200))  # zero-mean noise
    out = significance.bootstrap_pvalue(rets, n_resamples=2000, seed=0)
    assert out["p_value"] > 0.2


def test_bootstrap_too_few_returns():
    assert significance.bootstrap_pvalue([0.1])["p_value"] == 1.0


def test_psr_orders_strong_above_weak():
    rng = np.random.default_rng(3)
    good = list(rng.normal(0.05, 0.02, 100))
    weak = list(rng.normal(0.005, 0.05, 100))
    assert significance.probabilistic_sharpe_ratio(good) > significance.probabilistic_sharpe_ratio(weak)
    assert 0.0 <= significance.probabilistic_sharpe_ratio(good) <= 1.0


def test_psr_zero_variance_is_zero():
    assert significance.probabilistic_sharpe_ratio([0.03] * 60) == 0.0


def test_sidak_adjust():
    assert significance.sidak_adjust(0.05, 1) == 0.05
    assert significance.sidak_adjust(0.05, 3) == pytest.approx(1 - 0.95 ** 3, abs=1e-4)
    assert significance.sidak_adjust(0.0, 5) == 0.0


def test_trade_sharpe():
    assert significance.trade_sharpe([0.05] * 10) == 0.0  # zero std
    assert significance.trade_sharpe([0.1]) == 0.0        # too few
    assert significance.trade_sharpe([0.02, 0.04, 0.06]) > 0


def test_buy_and_hold():
    out = significance.buy_and_hold(pd.DataFrame({"close": [100, 110, 121]}))
    assert out["total_return"] == pytest.approx(0.21)
    assert significance.buy_and_hold(pd.DataFrame({"close": [100]}))["total_return"] == 0.0
