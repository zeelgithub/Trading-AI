"""End-to-end plumbing test for the strategy-evaluation pipeline.

Drives the REAL backtester (regime + strategy + risk + ratchet) on a synthetic
winning-trend frame, then checks the evaluation assembles a scored verdict.
"""

from __future__ import annotations

import pandas as pd

from src.research.evaluation import EvaluationReport, evaluate_strategies
from tests.unit.synth import make_features

_KNOWN_STRATEGIES = {"trend_following", "mean_reversion", "breakout"}


def _winning_trend_frame() -> pd.DataFrame:
    rows = [
        {"open": 98, "high": 98.5, "low": 97.5, "close": 98},
        {"open": 99, "high": 100.5, "low": 99.0, "close": 100,
         "ema20": 99.5, "ema50": 95, "ema200": 90, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30, "atr": 1.0},
        {"open": 100, "high": 106, "low": 100, "close": 105},
        {"open": 105, "high": 116, "low": 104, "close": 115},
        {"open": 115, "high": 126, "low": 114, "close": 125},
        {"open": 124, "high": 124, "low": 109, "close": 112},
    ]
    df = make_features(rows)
    df.index = pd.bdate_range(start="2024-01-02", periods=len(df))
    return df


def test_evaluate_strategies_pipeline_runs():
    report = evaluate_strategies(
        {"TEST": _winning_trend_frame()}, benchmark_symbol=None, n_bootstrap=200,
    )
    assert isinstance(report, EvaluationReport)
    assert len(report.scores) == 1

    s = report.scores[0]
    assert s.strategy in _KNOWN_STRATEGIES
    assert s.num_trades == 1
    assert s.verdict == "noise"            # a single trade is never signal
    assert report.portfolio_metrics["num_trades"] == 1
    assert "STRATEGY EVALUATION" in report.text()


def test_evaluate_includes_benchmark_when_present():
    frame = _winning_trend_frame()
    report = evaluate_strategies(
        {"TEST": frame, "SPY": frame}, benchmark_symbol="SPY", n_bootstrap=200,
    )
    assert report.benchmark is not None
    assert report.benchmark_symbol == "SPY"
    assert "benchmark SPY" in report.text()
