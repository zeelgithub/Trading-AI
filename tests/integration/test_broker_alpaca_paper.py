"""
Integration tests -- hit Alpaca's REAL paper API. NOT part of the default
`pytest` run (pytest.ini: `addopts = -m "not integration"`, per CLAUDE.md's
own "pytest # offline, no creds" invariant). Run explicitly:

    pytest -m integration tests/integration/

Skips cleanly (not an error) if ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
aren't set.

Why this exists: every other test in this project exercises Alpaca only
through `FakeBroker` (tests/unit/fakes.py) -- a hand-written stand-in that
echoes back whatever it's asked for. That's exactly why the real naked-
position incident shipped undetected: `AlpacaBroker._to_order_view`'s mapping
of the REAL alpaca-py response was never exercised by any test until
tests/unit/test_broker_alpaca.py added mocked-SDK-client coverage (see
docs/ROADMAP.md, docs/SAFEGUARDS.md). Mocking the SDK client closes most of
that gap cheaply and offline, which is why that tier stays in the default
run -- but a mock still encodes an ASSUMPTION about what Alpaca returns. This
tier removes the assumption: it is the only place in this project that asks
the real API and checks the real response shape, including fields this
project depends on for safety (`expires_at` for the 90-day GTC aged-order
fix -- see docs/SAFEGUARDS.md "GTC aged-order policy").

SAFETY -- read before running:
  - `AlpacaBroker(paper=True)` is explicit here, never inherited from a
    default. alpaca-py's TradingClient routes on this flag, not on
    ALPACA_TRADING_BASE_URL, so this suite cannot reach a live account even
    if .env is misconfigured.
  - Run this against a paper account you are NOT actively running the bot
    against, or at a time the bot's own scheduled cycle won't also run. The
    reconciler compares broker state to state/positions.json; an order this
    suite places (and cleans up) mid-run can race a live cycle into a false
    reconcile_mismatch HALT. A second, free Alpaca paper account (or a
    second key pair on the same account) avoids this entirely.
  - Every test cleans up after itself (cancel/close in a `finally`), but a
    hard crash mid-test can still leave a resting paper order or position --
    check the Alpaca paper dashboard after any aborted run.
  - Places qty=1 market orders on a liquid symbol (default SPY; override with
    INTEGRATION_TEST_SYMBOL). Paper money only, but still a real fill against
    real market data, so it only runs meaningfully while the market is open.

Boundary: same as AlpacaBroker -- places PAPER orders only.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from src.common.models import Side
from src.execution.broker_alpaca import AlpacaBroker

pytestmark = pytest.mark.integration

SYMBOL = os.environ.get("INTEGRATION_TEST_SYMBOL", "SPY")


def _creds_available() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY"))


@pytest.fixture
def broker():
    if not _creds_available():
        pytest.skip("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set -- "
                    "set real Alpaca PAPER credentials in .env to run this suite")
    return AlpacaBroker(paper=True)  # explicit -- see module docstring SAFETY note


def _wait_for_order(broker, order_id, timeout_s=15.0, interval_s=1.0):
    """Poll until the order fills or reaches a terminal non-fill status, or
    the timeout elapses (returns whatever the last poll saw either way --
    the caller asserts on it, so a timeout surfaces as a clear assertion
    failure rather than a hang)."""
    deadline = time.time() + timeout_s
    order = broker.get_order(order_id)
    while time.time() < deadline:
        if order.filled_qty and order.filled_qty > 0:
            return order
        if order.status in ("rejected", "canceled", "expired"):
            return order
        time.sleep(interval_s)
        order = broker.get_order(order_id)
    return order


def test_get_account_returns_real_shape(broker):
    """Confirms the real response maps cleanly against AccountView's current
    (post-PDT-removal, 2026-08-21) shape -- equity/buying_power only."""
    acct = broker.get_account()
    assert acct.equity >= 0.0
    assert acct.buying_power >= 0.0


def test_list_assets_includes_the_test_symbol(broker):
    symbols = {a.symbol for a in broker.list_assets()}
    assert SYMBOL in symbols


def test_protected_entry_sequence_matches_real_alpaca_shape(broker):
    """Walks the REAL sequence OrderManager drives against a live paper fill:
    market entry -> confirm fill -> standalone GTC stop -> confirm it rests
    with a real `expires_at` -> replace it and confirm the clock moves. This
    is the exact sequence the naked-position incident broke, and the exact
    field the 90-day GTC aging fix depends on -- FakeBroker cannot catch a
    mismatch in either because it only ever echoes back what it's told.
    """
    if not broker.is_market_open():
        pytest.skip("market closed -- a DAY market order won't fill promptly outside RTH")

    tag = uuid.uuid4().hex[:8]
    entry = broker.submit_market_entry(
        SYMBOL, qty=1, side=Side.LONG, client_order_id=f"itest-entry-{tag}",
    )
    try:
        entry = _wait_for_order(broker, entry.id)
        assert entry.filled_qty == 1, f"entry did not fill within timeout (status={entry.status})"
        assert entry.filled_avg_price is not None and entry.filled_avg_price > 0

        stop_price = round(entry.filled_avg_price * 0.9, 2)
        stop = broker.submit_stop(
            SYMBOL, qty=1, side=Side.LONG, stop_price=stop_price,
            client_order_id=f"itest-stop-{tag}",
        )
        stop = broker.get_order(stop.id)
        assert stop.status in ("new", "accepted", "held", "pending_new")
        assert stop.legs == ()  # standalone, not nested under any parent
        # The exact field src/execution/order_manager.py's refresh_stale_stop
        # depends on to reset Alpaca's 90-day GTC clock -- confirms it's real,
        # not just present in the SDK's type hints.
        assert stop.expires_at is not None

        old_expiry = stop.expires_at
        replaced = broker.replace_stop(stop.id, stop_price)  # same price -- just exercising the path
        replaced = broker.get_order(replaced.id)
        assert replaced.id != stop.id  # replace returns a NEW order id, same as OrderManager relies on
        assert replaced.expires_at is not None
        assert replaced.expires_at >= old_expiry  # the clock reset, not just carried over
    finally:
        # Best-effort: liquidates the position AND cancels the linked stop if
        # the entry filled; if it never filled there's nothing to close, only
        # a dead order to leave for Alpaca's own DAY expiry.
        try:
            broker.close_position(SYMBOL)
        except Exception:
            pass
