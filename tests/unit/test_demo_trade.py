"""Unit tests for scripts/demo_trade.py's _account_state -- the account-
state builder that used to hardcode open_positions/gross_exposure_value to
zero regardless of what the account actually held."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.demo_trade import _account_state


def _acct():
    return SimpleNamespace(equity=100_000.0, last_equity=99_000.0, buying_power=200_000.0)


def test_no_positions_gives_zero_exposure():
    state = _account_state(_acct(), [], price=250.0)
    assert state.open_positions == 0
    assert state.gross_exposure_value == 0.0


def test_reflects_real_holdings_not_hardcoded_zero():
    positions = [
        SimpleNamespace(qty=10, avg_entry_price=100.0),
        SimpleNamespace(qty=5, avg_entry_price=50.0),
    ]
    state = _account_state(_acct(), positions, price=250.0)
    assert state.open_positions == 2
    assert state.gross_exposure_value == 10 * 100.0 + 5 * 50.0


def test_passes_through_equity_and_price():
    state = _account_state(_acct(), [], price=250.0)
    assert state.equity == 100_000.0
    assert state.start_of_day_equity == 99_000.0
    assert state.buying_power == 200_000.0
    assert state.last_price == 250.0
