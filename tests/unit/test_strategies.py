"""Tests for the three strategies' signal logic (isolated via synthetic frames)."""

from __future__ import annotations

import pytest

from src.common.config import load_config
from src.common.models import Action, Side
from src.strategy.breakout import Breakout
from src.strategy.mean_reversion import MeanReversion
from src.strategy.trend_following import TrendFollowing
from tests.unit.synth import flat_frame, make_features


@pytest.fixture
def config():
    return load_config()


# --- Trend Following ---

def test_trend_long_entry(config):
    # Uptrend (close > ema50 > ema200), RSI in band, volume up, pullback to ema20
    # with a bullish confirmation candle that closes back above the 20 EMA.
    rows = [
        {"close": 103, "open": 103, "high": 104, "low": 102},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    intent = TrendFollowing(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG and intent.action == Action.BUY
    assert intent.entry_price == 105.0
    assert intent.stop_loss == pytest.approx(94.5)  # 105 * 0.90


def test_trend_rejects_when_rsi_out_of_band(config):
    rows = [
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "low": 104.0, "ema20": 104.5,
         "ema50": 100, "ema200": 95, "rsi": 80, "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    assert TrendFollowing(config).generate("AAPL", make_features(rows)) is None


# --- Mean Reversion ---

def test_mean_reversion_long_entry(config):
    rows = [
        {"close": 89, "open": 90},
        {"close": 90, "open": 89.5, "high": 90.5, "low": 89,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25},
    ]
    intent = MeanReversion(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.stop_loss == pytest.approx(88.2)   # 90 * 0.98
    assert intent.take_profit == pytest.approx(91.8)  # 90 * 1.02


def test_mean_reversion_short_blocked_when_shorts_disabled(config):
    # Overbought short setup, but watchlist defaults to longs-only -> no intent.
    rows = [
        {"close": 109, "open": 108},
        {"close": 110, "open": 110.5, "high": 111, "low": 109.5,
         "bb_lower": 90, "bb_upper": 108, "bb_mid": 99, "rsi": 75},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None


# --- Breakout ---

def test_breakout_long_entry(config):
    df = flat_frame(
        25, value=100.0,
        close=105.0, volume=2e6, vol_sma=1e6, atr=3.0,
    )
    intent = Breakout(config).generate("AAPL", df)
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.entry_price == 105.0
    assert intent.stop_loss == pytest.approx(99.0)  # 105 - 2.0 * ATR(3)


def test_breakout_needs_volume_spike(config):
    df = flat_frame(25, value=100.0, close=105.0, volume=1.0e6, vol_sma=1e6, atr=3.0)
    assert Breakout(config).generate("AAPL", df) is None


# --- Opposite-EMA exit (signal-driven) ---

def test_trend_should_exit_on_opposite_ema_break(config):
    tf = TrendFollowing(config)
    broke = make_features([{"close": 95, "ema50": 100, "ema200": 90}])
    assert tf.should_exit("AAPL", broke, Side.LONG) == "opposite_ema_break"
    held = make_features([{"close": 105, "ema50": 100, "ema200": 90}])
    assert tf.should_exit("AAPL", held, Side.LONG) is None
