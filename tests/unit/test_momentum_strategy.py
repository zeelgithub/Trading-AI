"""Momentum strategy: bucket-transition entry, confirmation candle, bucket-exit."""

from __future__ import annotations

import pytest

from src.common.config import load_config
from src.common.models import Side
from src.strategy.momentum import Momentum
from tests.unit.synth import make_features


@pytest.fixture
def config():
    return load_config()


def _with_bucket(features, bucket_flags, percentiles=None):
    features = features.copy()
    features["momentum_top_bucket"] = bucket_flags
    features["momentum_percentile"] = percentiles or [0.9] * len(features)
    return features


def test_entering_top_bucket_with_confirmation_fires_long(config):
    rows = [
        {"close": 100, "open": 100},
        {"close": 105, "open": 104, "high": 105.5, "low": 103.5},  # bullish confirmation
    ]
    feats = _with_bucket(make_features(rows), bucket_flags=[False, True], percentiles=[0.7, 0.95])
    intent = Momentum(config).generate("AAA", feats)
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.entry_price == 105.0


def test_already_in_top_bucket_yesterday_does_not_refire(config):
    """Only the TRANSITION into the bucket signals -- a symbol that's been in
    the top bucket for weeks shouldn't re-signal every single day."""
    rows = [
        {"close": 100, "open": 100},
        {"close": 105, "open": 104, "high": 105.5, "low": 103.5},
    ]
    feats = _with_bucket(make_features(rows), bucket_flags=[True, True])
    assert Momentum(config).generate("AAA", feats) is None


def test_entering_top_bucket_without_confirmation_does_not_fire(config):
    rows = [
        {"close": 100, "open": 100},
        {"close": 100.1, "open": 100.05, "high": 100.15, "low": 99.9},  # doji, weak body
    ]
    feats = _with_bucket(make_features(rows), bucket_flags=[False, True])
    assert Momentum(config).generate("AAA", feats) is None


def test_entering_bottom_bucket_with_confirmation_fires_short_when_allowed(config):
    from dataclasses import replace

    rows = [
        {"close": 100, "open": 100},
        {"close": 95, "open": 96, "high": 96.5, "low": 94.5},  # bearish confirmation
    ]
    feats = _with_bucket(make_features(rows), bucket_flags=[False, False], percentiles=[0.3, 0.05])
    shorting_config = replace(config, symbols={
        **config.symbols, "defaults": {**config.symbols.get("defaults", {}), "allow_short": True},
    })
    intent = Momentum(shorting_config).generate("AAA", feats)
    assert intent is not None
    assert intent.side == Side.SHORT


def test_short_blocked_when_shorts_disabled(config):
    rows = [
        {"close": 100, "open": 100},
        {"close": 95, "open": 96, "high": 96.5, "low": 94.5},
    ]
    feats = _with_bucket(make_features(rows), bucket_flags=[False, False], percentiles=[0.3, 0.05])
    assert Momentum(config).generate("AAA", feats) is None  # watchlist defaults to longs-only


def test_should_exit_long_when_leaving_top_bucket(config):
    rows = [{"close": 100, "open": 100}]
    feats = _with_bucket(make_features(rows), bucket_flags=[False])
    assert Momentum(config).should_exit("AAA", feats, Side.LONG) == "left_momentum_top_bucket"


def test_should_not_exit_long_while_still_in_top_bucket(config):
    rows = [{"close": 100, "open": 100}]
    feats = _with_bucket(make_features(rows), bucket_flags=[True])
    assert Momentum(config).should_exit("AAA", feats, Side.LONG) is None


def test_confidence_scales_with_percentile_strength(config):
    rows = [
        {"close": 100, "open": 100},
        {"close": 105, "open": 104, "high": 105.5, "low": 103.5},
    ]
    weak = _with_bucket(make_features(rows), bucket_flags=[False, True], percentiles=[0.7, 0.81])
    strong = _with_bucket(make_features(rows), bucket_flags=[False, True], percentiles=[0.7, 1.0])
    weak_intent = Momentum(config).generate("AAA", weak)
    strong_intent = Momentum(config).generate("AAA", strong)
    assert strong_intent.confidence > weak_intent.confidence
    assert strong_intent.confidence <= 0.9  # capped, same convention as trend_following
