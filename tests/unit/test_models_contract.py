"""Tests for the trade-signal JSON contract (Intent.from_dict / to_dict)."""

from __future__ import annotations

import pytest

from src.common.models import Action, Intent, Side


VALID = {
    "symbol": "AAPL",
    "signal": "BUY",
    "confidence": 0.72,
    "strategy": "trend_following",
    "entry_price": 190.2,
    "stop_loss": 185.0,
    "take_profit": 205.0,
}


def test_from_dict_parses_and_maps_side():
    intent = Intent.from_dict(VALID)
    assert intent.symbol == "AAPL"
    assert intent.action == Action.BUY
    assert intent.side == Side.LONG
    assert intent.entry_price == 190.2
    assert intent.stop_loss == 185.0
    assert intent.take_profit == 205.0


def test_round_trip_to_dict():
    intent = Intent.from_dict(VALID)
    assert intent.to_dict() == VALID


def test_short_signal_maps_to_short_side():
    d = {**VALID, "signal": "SHORT", "stop_loss": 195.0, "take_profit": 180.0}
    intent = Intent.from_dict(d)
    assert intent.side == Side.SHORT


def test_missing_required_key_raises():
    d = {k: v for k, v in VALID.items() if k != "symbol"}
    with pytest.raises(ValueError, match="missing required"):
        Intent.from_dict(d)


def test_long_stop_above_entry_is_invalid():
    d = {**VALID, "stop_loss": 195.0}  # stop above entry for a long
    with pytest.raises(ValueError, match="stop_loss must be below"):
        Intent.from_dict(d)


def test_non_opening_signal_rejected():
    d = {**VALID, "signal": "HOLD"}
    with pytest.raises(ValueError, match="does not open"):
        Intent.from_dict(d)
