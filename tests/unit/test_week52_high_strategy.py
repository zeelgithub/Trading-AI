"""Week52High strategy: anchor-transition entry, confirmation candle, no
signal-based exit (rides the ATR ratchet, same philosophy as breakout)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.common.config import load_config
from src.common.models import Side
from src.strategy.week52_high import Week52High
from tests.unit.synth import flat_frame, make_features

_LOOKBACK = 5  # small on purpose -- keeps fixtures short; production default is 252


def _config(proximity_pct: float = 5.0, allow_short: bool = False):
    base = load_config()
    strategies = {
        **base.strategies,
        "strategies": {
            **base.strategies["strategies"],
            "week52_high": {
                "indicators": {"anchor_lookback_bars": _LOOKBACK},
                "conditions": {"proximity_pct": proximity_pct},
            },
        },
    }
    symbols = {
        **base.symbols,
        "defaults": {**base.symbols.get("defaults", {}), "allow_short": allow_short},
    }
    return replace(base, strategies=strategies, symbols=symbols)


def _rows_rallying_into_high_zone():
    """`_LOOKBACK` flat bars at 100 (anchor_high settles at 100), a dip well
    below the 5% proximity zone (yesterday), then a real-bodied rally back
    into the zone (today) -- the actual transition this strategy fires on."""
    rows = [{"close": 100, "open": 100, "high": 100, "low": 100} for _ in range(_LOOKBACK)]
    rows.append({"close": 80, "open": 80, "high": 80, "low": 80})   # yesterday: well below zone
    rows.append({"close": 105, "open": 104, "high": 105.5, "low": 103.5})  # today: rallies back in
    return rows


def _rows_dropping_into_low_zone():
    rows = [{"close": 100, "open": 100, "high": 100, "low": 100} for _ in range(_LOOKBACK)]
    rows.append({"close": 120, "open": 120, "high": 120, "low": 120})  # yesterday: well above zone
    rows.append({"close": 95, "open": 96, "high": 96.5, "low": 94.5})  # today: drops back in
    return rows


def test_entering_near_high_with_confirmation_fires_long():
    feats = make_features(_rows_rallying_into_high_zone())
    intent = Week52High(_config()).generate("AAA", feats)
    assert intent is not None
    assert intent.side == Side.LONG
    assert intent.entry_price == 105.0


def test_flat_series_never_refires_after_the_first_day():
    """A symbol sitting at a constant price is trivially 'near' its own flat
    anchor every single day -- there is no transition to fire on, so it
    should never signal (mirrors momentum's 'already in bucket' guard)."""
    feats = flat_frame(_LOOKBACK + 2, value=100.0)
    assert Week52High(_config()).generate("AAA", feats) is None


def test_entering_near_high_without_confirmation_does_not_fire():
    rows = [{"close": 100, "open": 100, "high": 100, "low": 100} for _ in range(_LOOKBACK)]
    rows.append({"close": 80, "open": 80, "high": 80, "low": 80})
    rows.append({"close": 95.1, "open": 95.05, "high": 95.15, "low": 94.9})  # doji, weak body
    feats = make_features(rows)
    assert Week52High(_config()).generate("AAA", feats) is None


def test_entering_near_low_with_confirmation_fires_short_when_allowed():
    feats = make_features(_rows_dropping_into_low_zone())
    intent = Week52High(_config(allow_short=True)).generate("AAA", feats)
    assert intent is not None
    assert intent.side == Side.SHORT
    assert intent.entry_price == 95.0


def test_short_blocked_when_shorts_disabled():
    feats = make_features(_rows_dropping_into_low_zone())
    assert Week52High(_config(allow_short=False)).generate("AAA", feats) is None


def test_insufficient_history_returns_none():
    feats = flat_frame(_LOOKBACK, value=100.0)  # needs LOOKBACK + 2
    assert Week52High(_config()).generate("AAA", feats) is None


def test_should_exit_never_signals_regardless_of_price():
    """No signal-based exit by design -- the entry is a punctual breakthrough
    event, not an ongoing state, so this rides the ATR ratchet alone (base
    class default). Any price/side combination should return None."""
    feats = flat_frame(_LOOKBACK + 2, value=50.0)
    strat = Week52High(_config())
    assert strat.should_exit("AAA", feats, Side.LONG) is None
    assert strat.should_exit("AAA", feats, Side.SHORT) is None
