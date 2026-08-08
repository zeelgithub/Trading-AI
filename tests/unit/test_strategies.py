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
    # 3 rows: pullback_lookback_bars defaults to 3, so at least that much
    # history is required even though the touch+reclaim itself is same-bar here.
    rows = [
        {"close": 104, "open": 104, "high": 105, "low": 103},
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


def test_trend_windowed_pullback_touch_two_bars_ago_still_fires(config):
    # Touch happened 2 bars ago (not today); today is purely the confirmation/
    # reclaim bar. Same-bar-only logic would reject this; windowed (default
    # lookback=3) must accept it.
    rows = [
        {"close": 104, "open": 104, "high": 105, "low": 104.4,  # the touch bar
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
        {"close": 106, "open": 105, "high": 106.5, "low": 105.0,  # drifting up, no touch
         "ema20": 104.6, "ema50": 100, "ema200": 95, "rsi": 58,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
        {"close": 108, "open": 106, "high": 108.5, "low": 106.0,  # confirmation candle
         "ema20": 104.7, "ema50": 100, "ema200": 95, "rsi": 60,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    intent = TrendFollowing(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG


def test_trend_touch_outside_lookback_window_does_not_fire(config):
    from dataclasses import replace

    # EMA20 held flat (~100) while price walks away from it. The touch (row 0,
    # low near 100) is 3 bars before today; with lookback_bars=2 the window is
    # only [row2, row3], neither of which is anywhere near the (flat) EMA20 --
    # so "pulled back recently" must be False and the signal must NOT fire.
    rows = [
        {"close": 101, "open": 100.5, "high": 101.5, "low": 100.5,  # touch (outside window)
         "ema20": 100, "ema50": 90, "ema200": 85, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
        {"close": 105, "open": 101, "high": 105.5, "low": 104,
         "ema20": 100, "ema50": 90, "ema200": 85, "rsi": 58,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
        {"close": 107, "open": 105, "high": 107.5, "low": 106,
         "ema20": 100, "ema50": 90, "ema200": 85, "rsi": 60,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
        {"close": 109.3, "open": 108.2, "high": 109.5, "low": 108.0,  # confirmation candle
         "ema20": 100, "ema50": 90, "ema200": 85, "rsi": 62,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    tf = TrendFollowing(replace(config, strategies={
        **config.strategies,
        "strategies": {
            **config.strategies["strategies"],
            "trend_following": {
                **config.strategies["strategies"]["trend_following"],
                "conditions": {
                    **config.strategies["strategies"]["trend_following"]["conditions"],
                    "pullback_lookback_bars": 2,
                },
            },
        },
    }))
    assert tf.generate("AAPL", make_features(rows)) is None


def test_trend_rejects_when_rsi_out_of_band(config):
    rows = [
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "low": 104.0, "ema20": 104.5,
         "ema50": 100, "ema200": 95, "rsi": 80, "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    assert TrendFollowing(config).generate("AAPL", make_features(rows)) is None


# --- Mean Reversion ---

def test_mean_reversion_long_entry(config):
    # Strong-bodied reversal candle: body (89.9-88.9=1.0) is 91% of the day's
    # range (90.1-88.9=1.2) -- comfortably clears the 0.5 min_body_ratio bar.
    # 3 rows: reversion_lookback_bars defaults to 3, so at least that much
    # history is required even though the touch+reversal itself is same-bar.
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28},
        {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25},
    ]
    intent = MeanReversion(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.stop_loss == pytest.approx(88.1)   # round(89.9 * 0.98, 2)
    assert intent.take_profit == pytest.approx(91.7)  # round(89.9 * 1.02, 2)


def test_mean_reversion_touch_two_bars_ago_still_fires(config):
    # The band-touch/RSI-extreme happened 2 bars ago; today is purely the
    # confirmation candle. Same-bar-only logic would reject this; windowed
    # (default lookback=3) must accept it.
    rows = [
        {"close": 89.0, "open": 89.5, "high": 89.6, "low": 88.9,   # the touch bar
         "bb_lower": 90, "bb_upper": 110, "bb_mid": 101, "rsi": 25},
        {"close": 89.5, "open": 89.2, "high": 89.7, "low": 89.0,   # drifting, no fresh touch
         "bb_lower": 90.5, "bb_upper": 110, "bb_mid": 101, "rsi": 32},
        {"close": 91.0, "open": 89.6, "high": 91.2, "low": 89.5,   # confirmation candle
         "bb_lower": 91, "bb_upper": 110, "bb_mid": 101, "rsi": 40},
    ]
    intent = MeanReversion(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG


def test_mean_reversion_touch_outside_lookback_window_does_not_fire(config):
    from dataclasses import replace

    # Touch is 3 bars before today; with reversion_lookback_bars=2 the window
    # is only [row2, row3], neither of which touches the (now-recovered) band.
    rows = [
        {"close": 89.0, "open": 89.5, "high": 89.6, "low": 88.9,   # touch (outside window)
         "bb_lower": 90, "bb_upper": 110, "bb_mid": 101, "rsi": 25},
        {"close": 92, "open": 89.2, "high": 92.3, "low": 89.0,
         "bb_lower": 90.2, "bb_upper": 110, "bb_mid": 101, "rsi": 42},
        {"close": 95, "open": 92.2, "high": 95.3, "low": 92.0,
         "bb_lower": 90.5, "bb_upper": 110, "bb_mid": 101, "rsi": 50},
        {"close": 98, "open": 95.2, "high": 98.3, "low": 95.0,     # confirmation-shaped candle
         "bb_lower": 91, "bb_upper": 110, "bb_mid": 101, "rsi": 55},
    ]
    mr = MeanReversion(replace(config, strategies={
        **config.strategies,
        "strategies": {
            **config.strategies["strategies"],
            "mean_reversion": {
                **config.strategies["strategies"]["mean_reversion"],
                "conditions": {
                    **config.strategies["strategies"]["mean_reversion"]["conditions"],
                    "reversion_lookback_bars": 2,
                },
            },
        },
    }))
    assert mr.generate("AAPL", make_features(rows)) is None


def test_mean_reversion_rejects_weak_bodied_candle(config):
    # Same oversold setup as above, but a doji-like candle: body (0.1) is only
    # 8% of the day's range (1.2) -- a tick in the right direction with no
    # real conviction behind it. Must NOT confirm.
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28},
        {"close": 89.55, "open": 89.45, "high": 90.05, "low": 88.95,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None


def test_mean_reversion_short_blocked_when_shorts_disabled(config):
    # Overbought short setup, but watchlist defaults to longs-only -> no intent.
    rows = [
        {"close": 106, "open": 105.5, "bb_lower": 90, "bb_upper": 108, "rsi": 55},
        {"close": 109, "open": 108, "bb_lower": 90, "bb_upper": 108, "rsi": 72},
        {"close": 110, "open": 110.5, "high": 111, "low": 109.5,
         "bb_lower": 90, "bb_upper": 108, "bb_mid": 99, "rsi": 75},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None


# --- Breakout ---

def test_breakout_long_entry(config):
    # Real-bodied breakout candle: body (105-100.5=4.5) is 82% of the day's
    # range (105.5-100.0=5.5) -- a genuine breakout close, not a weak tick
    # past the level.
    df = flat_frame(
        25, value=100.0,
        open=100.5, high=105.5, low=100.0, close=105.0,
        volume=2e6, vol_sma=1e6, atr=3.0,
    )
    intent = Breakout(config).generate("AAPL", df)
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.entry_price == 105.0
    assert intent.stop_loss == pytest.approx(99.0)  # 105 - 2.0 * ATR(3)


def test_breakout_needs_volume_spike(config):
    df = flat_frame(25, value=100.0, open=100.5, high=105.5, low=100.0,
                    close=105.0, volume=1.0e6, vol_sma=1e6, atr=3.0)
    assert Breakout(config).generate("AAPL", df) is None


def test_breakout_rejects_weak_bodied_close(config):
    # Close beats resistance (105 > 100), volume + ATR conditions pass, but
    # the candle itself is a weak tick (body 0.2 of a 5.3 range ~ 4%) -- the
    # false-breakout shape the strategy's own docs flag as its known risk.
    # Must NOT fire without a real-bodied confirmation candle.
    df = flat_frame(
        25, value=100.0,
        open=104.8, high=105.3, low=100.0, close=105.0,
        volume=2e6, vol_sma=1e6, atr=3.0,
    )
    assert Breakout(config).generate("AAPL", df) is None


# --- Opposite-EMA exit (signal-driven) ---

def test_trend_should_exit_on_opposite_ema_break(config):
    tf = TrendFollowing(config)
    broke = make_features([{"close": 95, "ema50": 100, "ema200": 90}])
    assert tf.should_exit("AAPL", broke, Side.LONG) == "opposite_ema_break"
    held = make_features([{"close": 105, "ema50": 100, "ema200": 90}])
    assert tf.should_exit("AAPL", held, Side.LONG) is None
