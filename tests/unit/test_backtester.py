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
        {"open": 97, "high": 97.5, "low": 96.5, "close": 97},          # warmup
        {"open": 98, "high": 98.5, "low": 97.5, "close": 98},          # warmup
        {"open": 99, "high": 100.5, "low": 99.0, "close": 100,         # SIGNAL day
         "ema20": 99.5, "ema50": 95, "ema200": 90, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 35, "atr": 1.0},
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
    # adx=35 -> strategy confidence 0.65; no sentiment scorer wired -> neutral
    # haircut *0.8 -> 0.52. Risk-based sizing (per_strategy_risk_pct 1.33% *
    # 0.52 = 0.6916% of 100k = $691.6 / $10 risk-per-share) -> 69 shares,
    # tighter than the 100-share max-position cap this used to hit before
    # confidence-scaled sizing was wired in (see src/risk/risk_manager.py).
    assert t.qty == 69
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(110.0)
    assert t.pnl == pytest.approx(690.0)     # (110-100) * 69

    assert result.equity_curve.iloc[-1] == pytest.approx(100_690.0)
    assert result.metrics["num_trades"] == 1
    assert result.metrics["win_rate"] == pytest.approx(1.0)


def _open_at_data_end_frame() -> pd.DataFrame:
    # Same setup as the winning-trend frame, but stops one bar short of the
    # stop-out row -- the position is still open when this symbol's data
    # simply runs out (delisting, acquisition, or the backtest window ending).
    rows = [
        {"open": 97, "high": 97.5, "low": 96.5, "close": 97},
        {"open": 98, "high": 98.5, "low": 97.5, "close": 98},
        {"open": 99, "high": 100.5, "low": 99.0, "close": 100,
         "ema20": 99.5, "ema50": 95, "ema200": 90, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 35, "atr": 1.0},
        {"open": 100, "high": 106, "low": 100, "close": 105},          # FILL @100, rally
        {"open": 105, "high": 116, "low": 104, "close": 115},
        {"open": 115, "high": 126, "low": 114, "close": 125},          # data ends here, still open
    ]
    df = make_features(rows)
    df.index = pd.bdate_range(start="2024-01-02", periods=len(df))
    return df


def test_entries_start_suppresses_entries_before_the_cutoff():
    """`entries_start` is the walk-forward-fold gate: a signal generated on a
    day strictly before the cutoff must not open a position, even though the
    full history up to and including that bar is still visible for indicators
    (no warmup is lost, only new entries are suppressed)."""
    bt = Backtester(initial_equity=100_000.0, slippage_bps=0.0, commission_per_share=0.0)
    frame = _winning_trend_frame()
    signal_day = frame.index[2]
    result = bt.run({"TEST": frame}, entries_start=signal_day + pd.Timedelta(days=1))

    assert result.trades == []
    assert result.equity_curve.iloc[-1] == pytest.approx(100_000.0)


def test_entries_start_on_the_signal_day_itself_still_allows_entry():
    """The cutoff is inclusive: a signal generated ON `entries_start` still
    trades, matching the un-gated backtest exactly."""
    bt = Backtester(initial_equity=100_000.0, slippage_bps=0.0, commission_per_share=0.0)
    frame = _winning_trend_frame()
    signal_day = frame.index[2]
    result = bt.run({"TEST": frame}, entries_start=signal_day)

    assert len(result.trades) == 1
    assert result.trades[0].reason == "stop"


def test_position_still_open_when_data_ends_force_closes_at_last_price():
    """A symbol that stops trading mid-backtest (delisting, acquisition, or
    just the window ending) must not let an open position silently vanish --
    that would be the most optimistic possible outcome for exactly the kind
    of loss a survivorship-bias-aware universe exists to capture."""
    bt = Backtester(initial_equity=100_000.0, slippage_bps=0.0, commission_per_share=0.0)
    result = bt.run({"TEST": _open_at_data_end_frame()})

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.reason == "data_end"
    assert t.exit_price == pytest.approx(125.0)     # last available close
    assert t.exit_date == pd.bdate_range(start="2024-01-02", periods=6)[-1]
