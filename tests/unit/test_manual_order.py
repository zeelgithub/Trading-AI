"""Unit tests for scripts/manual_order.py's _stop_kwargs -- the CLI used to
hardcode --stop's default to 10.0, duplicating trade_service.place_manual's
own default independently."""

from __future__ import annotations

import inspect

from scripts.manual_order import _stop_kwargs
from src.core.trade_service import TradeService


def test_no_stop_flag_omits_stop_pct_so_place_manuals_own_default_applies():
    assert _stop_kwargs(None) == {}


def test_stop_flag_overrides_with_the_given_value():
    assert _stop_kwargs(8.0) == {"stop_pct": 8.0}


def test_place_manuals_default_stop_pct_is_still_10():
    """Guards the claim in _stop_kwargs's docstring and manual_order.py's
    own usage text ("10% stop (default)") -- if place_manual's default ever
    changes, this should fail as a reminder to update that text too."""
    default = inspect.signature(TradeService.place_manual).parameters["stop_pct"].default
    assert default == 10.0
