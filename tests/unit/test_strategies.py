"""Tests for the three strategies' signal logic (isolated via synthetic frames)."""

from __future__ import annotations

from dataclasses import replace

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


def test_trend_rsi_band_is_actually_config_driven(config):
    """Regression guard: rsi_long_band is declared in config/strategies.yaml
    but was hardcoded as a literal in the strategy -- editing the config did
    nothing (found 2026-08-21, see module docstring). Same setup as
    test_trend_long_entry (RSI=80 instead of 55, otherwise identical) --
    rejected by the default 40-70 band; with the band explicitly widened to
    40-80 via config, the identical setup must now fire."""
    from dataclasses import replace

    rows = [
        {"close": 104, "open": 104, "high": 105, "low": 103},
        {"close": 103, "open": 103, "high": 104, "low": 102},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 80,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    widened = replace(config, strategies={
        **config.strategies,
        "strategies": {
            **config.strategies["strategies"],
            "trend_following": {
                **config.strategies["strategies"]["trend_following"],
                "conditions": {
                    **config.strategies["strategies"]["trend_following"]["conditions"],
                    "rsi_long_band": [40, 80],
                },
            },
        },
    })
    intent = TrendFollowing(widened).generate("AAPL", make_features(rows))
    assert intent is not None and intent.side == Side.LONG


# --- Mean Reversion ---

def test_mean_reversion_long_entry(config):
    # Strong-bodied reversal candle: body (89.9-88.9=1.0) is 91% of the day's
    # range (90.1-88.9=1.2) -- comfortably clears the 0.5 min_body_ratio bar.
    # 3 rows: reversion_lookback_bars defaults to 3, so at least that much
    # history is required even though the touch+reversal itself is same-bar.
    # ema200=80: an intact uptrend (close well above it), so the trend-alignment
    # filter (require_trend_alignment, default True) allows this dip-buy --
    # synth.py's make_features defaults ema200 to the row's own close, which
    # would otherwise fail the strict close > ema200 check on equality.
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 80},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28, "ema200": 80},
        {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 80},
    ]
    intent = MeanReversion(config).generate("AAPL", make_features(rows))
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.stop_loss == pytest.approx(88.1)   # round(89.9 * 0.98, 2)
    assert intent.take_profit == pytest.approx(91.7)  # round(89.9 * 1.02, 2)


def test_mean_reversion_touch_two_bars_ago_still_fires(config):
    # The band-touch/RSI-extreme happened 2 bars ago; today is purely the
    # confirmation candle. Same-bar-only logic would reject this; windowed
    # (default lookback=3) must accept it. ema200=80: intact uptrend, same
    # reasoning as test_mean_reversion_long_entry above.
    rows = [
        {"close": 89.0, "open": 89.5, "high": 89.6, "low": 88.9,   # the touch bar
         "bb_lower": 90, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 80},
        {"close": 89.5, "open": 89.2, "high": 89.7, "low": 89.0,   # drifting, no fresh touch
         "bb_lower": 90.5, "bb_upper": 110, "bb_mid": 101, "rsi": 32, "ema200": 80},
        {"close": 91.0, "open": 89.6, "high": 91.2, "low": 89.5,   # confirmation candle
         "bb_lower": 91, "bb_upper": 110, "bb_mid": 101, "rsi": 40, "ema200": 80},
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
    # Same oversold setup as above (ema200=80, intact uptrend -- trend
    # alignment allows this one through so the body-ratio check is what's
    # actually under test), but a doji-like candle: body (0.1) is only 8% of
    # the day's range (1.2) -- a tick in the right direction with no real
    # conviction behind it. Must NOT confirm.
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 80},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28, "ema200": 80},
        {"close": 89.55, "open": 89.45, "high": 90.05, "low": 88.95,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 80},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None


def test_mean_reversion_short_blocked_when_shorts_disabled(config):
    # Overbought short setup, ema200=120 (intact downtrend -- trend alignment
    # allows this one through so shorts_allowed() is what's actually under
    # test), but watchlist defaults to longs-only -> no intent.
    rows = [
        {"close": 106, "open": 105.5, "bb_lower": 90, "bb_upper": 108, "rsi": 55, "ema200": 120},
        {"close": 109, "open": 108, "bb_lower": 90, "bb_upper": 108, "rsi": 72, "ema200": 120},
        {"close": 110, "open": 110.5, "high": 111, "low": 109.5,
         "bb_lower": 90, "bb_upper": 108, "bb_mid": 99, "rsi": 75, "ema200": 120},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None


def _with_mean_reversion_condition(config, **overrides):
    from dataclasses import replace

    return replace(config, strategies={
        **config.strategies,
        "strategies": {
            **config.strategies["strategies"],
            "mean_reversion": {
                **config.strategies["strategies"]["mean_reversion"],
                "conditions": {
                    **config.strategies["strategies"]["mean_reversion"]["conditions"],
                    **overrides,
                },
            },
        },
    })


def test_mean_reversion_rsi_thresholds_are_actually_config_driven(config):
    """Regression guard: rsi_oversold/rsi_overbought are declared in
    config/strategies.yaml but were hardcoded as literals in the strategy --
    editing the config did nothing (found 2026-08-22, see module docstring).
    Same setup as test_mean_reversion_long_entry (RSI=32 instead of 25,
    otherwise identical) -- rejected under the default oversold threshold of
    30; with the threshold explicitly widened to 35 via config, the identical
    setup must now fire."""
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 80},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 40, "ema200": 80},
        {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 32, "ema200": 80},
    ]
    assert MeanReversion(config).generate("AAPL", make_features(rows)) is None

    widened = _with_mean_reversion_condition(config, rsi_oversold=35)
    intent = MeanReversion(widened).generate("AAPL", make_features(rows))
    assert intent is not None and intent.side == Side.LONG


_FALLING_KNIFE_ROWS = [
    {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 130},
    {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28, "ema200": 130},
    {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
     "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 130},
]


def test_mean_reversion_trend_filter_off_by_default_still_fires_in_downtrend(config):
    """require_trend_alignment defaults to False (see mean_reversion.py module
    docstring: tested against this project's own cached backtest data and
    found to hurt, not help) -- an otherwise-perfect oversold setup fires
    even though the primary trend (ema200) is down, same as before the
    filter existed."""
    intent = MeanReversion(config).generate("AAPL", make_features(_FALLING_KNIFE_ROWS))
    assert intent is not None and intent.side == Side.LONG


def test_mean_reversion_trend_filter_enabled_blocks_dip_buy_in_downtrend(config):
    """With the filter explicitly turned on, the same falling-knife setup
    must NOT fire -- this is the filter's own intended behavior, kept as
    tested, toggleable infrastructure even though it's off by default."""
    mr = MeanReversion(_with_mean_reversion_condition(config, require_trend_alignment=True))
    assert mr.generate("AAPL", make_features(_FALLING_KNIFE_ROWS)) is None


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


# --- Config-driven constants (2026-08-22: were hardcoded module constants) ---

def _with_trend_following_override(config, **overrides):
    tf_block = config.strategies["strategies"]["trend_following"]
    return replace(config, strategies={
        **config.strategies,
        "strategies": {
            **config.strategies["strategies"],
            "trend_following": {**tf_block, **overrides},
        },
    })


def test_trend_pullback_proximity_pct_is_read_from_config(config):
    # Row 0's low sits 1.5% above a flat ema20=100 -- a "touch" under the
    # default 2% proximity, but not under a tightened 0.5%.
    rows = [
        {"close": 101.5, "open": 101.5, "low": 101.5, "high": 102, "ema20": 100},
        {"close": 103, "open": 102, "low": 102, "high": 103.5, "ema20": 100},
        {"close": 106, "open": 104, "low": 103.8, "high": 106.7,
         "ema20": 100, "ema50": 95, "ema200": 90, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    feats = make_features(rows)
    assert TrendFollowing(config).generate("AAPL", feats) is not None  # default 2%: fires

    tightened = _with_trend_following_override(
        config,
        conditions={**config.strategies["strategies"]["trend_following"]["conditions"],
                    "pullback_proximity_pct": 0.5},
    )
    assert TrendFollowing(tightened).generate("AAPL", feats) is None  # 0.5%: no touch


def test_trend_confidence_formula_is_read_from_config(config):
    rows = [
        {"close": 104, "open": 104, "high": 105, "low": 103},
        {"close": 103, "open": 103, "high": 104, "low": 102},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30},
    ]
    feats = make_features(rows)
    # Default: min(0.9, 0.55 + max(0, 30-25)/100) = 0.60
    assert TrendFollowing(config).generate("AAPL", feats).confidence == pytest.approx(0.60)

    custom = _with_trend_following_override(
        config, confidence={"base": 0.5, "adx_baseline": 20.0, "adx_divisor": 50.0, "max": 0.8})
    # min(0.8, 0.5 + max(0, 30-20)/50) = 0.70
    assert TrendFollowing(custom).generate("AAPL", feats).confidence == pytest.approx(0.70)


def test_mean_reversion_confidence_is_read_from_config(config):
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 80},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28, "ema200": 80},
        {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 80},
    ]
    feats = make_features(rows)
    assert MeanReversion(config).generate("AAPL", feats).confidence == pytest.approx(0.6)

    mr_block = config.strategies["strategies"]["mean_reversion"]
    custom = replace(config, strategies={
        **config.strategies,
        "strategies": {**config.strategies["strategies"],
                       "mean_reversion": {**mr_block, "confidence": 0.42}},
    })
    assert MeanReversion(custom).generate("AAPL", feats).confidence == pytest.approx(0.42)


def test_breakout_confidence_is_read_from_config(config):
    df = flat_frame(
        25, value=100.0,
        open=100.5, high=105.5, low=100.0, close=105.0,
        volume=2e6, vol_sma=1e6, atr=3.0,
    )
    assert Breakout(config).generate("AAPL", df).confidence == pytest.approx(0.65)

    bo_block = config.strategies["strategies"]["breakout"]
    custom = replace(config, strategies={
        **config.strategies,
        "strategies": {**config.strategies["strategies"],
                       "breakout": {**bo_block, "confidence": 0.33}},
    })
    assert Breakout(custom).generate("AAPL", df).confidence == pytest.approx(0.33)
