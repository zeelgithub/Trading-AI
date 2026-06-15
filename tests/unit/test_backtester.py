"""Integration test for the backtester: a full winning trend trade end-to-end.

Verifies next-open fills, the ratchet stop ratcheting up, the stop-out exit, and
the resulting PnL / equity -- using the real regime + strategy + risk + ratchet
components.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.research.backtester import Backtester
from tests.unit.synth import make_features


def _winning_trend_frame() -> pd.DataFrame:
    rows = [
        {"open": 98, "high": 98.5, "low": 97.5, "close": 98},          # warmup
        {"open": 99, "high": 100.5, "low": 99.0, "close": 100,         # SIGNAL day
         "ema20": 99.5, "ema50": 95, "ema200": 90, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30, "atr": 1.0},
        {"open": 100, "high": 106, "low": 100, "close": 105},          # FILL @100, rally
        {"open": 105, "high": 116, "low": 104, "close": 115},
        {"open": 115, "high": 126, "low": 114, "close": 125},          # +25% -> stop to 110
        {"open": 124, "high": 124, "low": 109, "close": 112},          # low 109 <= 110 -> exit
    ]
    df = make_features(rows)
    df.index = pd.bdate_range(start="2024-01-02", periods=len(df))
    return df


def test_winning_trend_trade():
    bt = Backtester(initial_equity=100_000.0, slippage_bps=0.0, commission_per_share=0.0)
    result = bt.run({"TEST": _winning_trend_frame()})

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.side == "long"
    assert t.reason == "stop"
    assert t.qty == 100                      # 10% max-position cap: 10k/100
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(110.0)
    assert t.pnl == pytest.approx(1000.0)    # (110-100) * 100

    assert result.equity_curve.iloc[-1] == pytest.approx(101_000.0)
    assert result.metrics["num_trades"] == 1
    assert result.metrics["win_rate"] == pytest.approx(1.0)
