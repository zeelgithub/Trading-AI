"""Tests for the regime filter routing and the non-executing sentiment gate."""

from __future__ import annotations

from src.common.config import load_config
from src.common.models import Action, Intent, Regime, Side
from src.strategy.regime_filter import RegimeFilter
from src.strategy.sentiment_gate import SentimentGate
from tests.unit.synth import flat_frame


# --- Regime filter ---

def test_trending_routes_to_trend_following():
    rf = RegimeFilter(load_config())
    df = flat_frame(30, value=100.0, adx=35.0)  # >= trending_adx_min (32)
    assert rf.classify(df) == Regime.TRENDING
    assert rf.active_strategy(df) == "trend_following"


def test_ranging_routes_to_mean_reversion():
    rf = RegimeFilter(load_config())
    df = flat_frame(30, value=100.0, adx=15.0)
    assert rf.classify(df) == Regime.RANGING
    assert rf.active_strategy(df) == "mean_reversion"


def test_ranging_band_widened_for_mean_reversion_coverage():
    # Evidence-based widening (see config/strategies.yaml comment): ADX 26 used
    # to fall in the old dead zone (20-25) and get NO strategy at all, even
    # though mean-reversion's median signal ADX is ~27 -- it must now route.
    rf = RegimeFilter(load_config())
    df = flat_frame(30, value=100.0, adx=26.0)
    assert rf.classify(df) == Regime.RANGING
    assert rf.active_strategy(df) == "mean_reversion"


def test_expansion_routes_to_breakout():
    rf = RegimeFilter(load_config())
    # Rising ATR + close breaking the prior-range high -> expansion.
    df = flat_frame(30, value=100.0, close=110.0, atr=5.0, adx=22.0)
    assert rf.classify(df) == Regime.EXPANSION
    assert rf.active_strategy(df) == "breakout"


def test_dead_zone_stands_aside():
    rf = RegimeFilter(load_config())
    df = flat_frame(30, value=100.0, adx=30.0)  # 28 < adx < 32, no breakout
    assert rf.classify(df) == Regime.NONE
    assert rf.active_strategy(df) is None


# --- Sentiment gate ---

def _long_intent(conf=0.7) -> Intent:
    return Intent(symbol="AAPL", strategy="trend_following", side=Side.LONG,
                  action=Action.BUY, confidence=conf)


def test_neutral_sentiment_haircuts_confidence():
    gate = SentimentGate(load_config(), scorer=None)  # all neutral (0)
    out = gate.apply(_long_intent(0.7))
    assert out is not None
    assert out.confidence == 0.56  # 0.7 * 0.8


def test_confirming_sentiment_keeps_confidence():
    gate = SentimentGate(load_config(), scorer=lambda s: 1)
    out = gate.apply(_long_intent(0.7))
    assert out is not None and out.confidence == 0.7


def test_opposing_sentiment_blocks_long():
    gate = SentimentGate(load_config(), scorer=lambda s: -1)
    assert gate.apply(_long_intent()) is None


def test_short_blocked_by_bullish_sentiment():
    gate = SentimentGate(load_config(), scorer=lambda s: 1)
    short = Intent(symbol="AAPL", strategy="breakout", side=Side.SHORT,
                   action=Action.SHORT, confidence=0.6)
    assert gate.apply(short) is None


def test_scorer_failure_skips_gate_unchanged():
    """on_feed_unavailable: skip_gate -- a wired scorer that RAISES (network/API
    error) must pass the intent through completely unchanged, not apply the
    neutral haircut and not block. Distinct from `scorer=None` (deliberately no
    scorer, which IS a real neutral 0 and DOES take the haircut)."""
    def _boom(symbol):
        raise TimeoutError("sentiment API timed out")

    gate = SentimentGate(load_config(), scorer=_boom)
    out = gate.apply(_long_intent(0.7))
    assert out is not None
    assert out.confidence == 0.7  # unchanged -- no haircut


def test_scorer_failure_does_not_block_a_short_either():
    def _boom(symbol):
        raise ConnectionError("no route to sentiment host")

    gate = SentimentGate(load_config(), scorer=_boom)
    short = Intent(symbol="AAPL", strategy="breakout", side=Side.SHORT,
                   action=Action.SHORT, confidence=0.6)
    out = gate.apply(short)
    assert out is not None
    assert out.confidence == 0.6
