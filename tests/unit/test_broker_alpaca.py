"""Unit tests for src/execution/broker_alpaca.py's AlpacaBroker.

The real alpaca-py TradingClient is never hit -- `_get_client()` is
monkeypatched to a fake object, mirroring tests/unit/test_alpaca_data.py's
pattern for the market-data client. Credentials are stubbed so these tests
need no network and no .env.

This file exists because AlpacaBroker -- the ONLY module permitted to place
orders -- had zero direct tests of its alpaca-py response mapping; every
other test exercises it only through FakeBroker, which never runs the real
translation code in _to_order_view / get_account. That gap is exactly how
`daytrade_count=int(a.daytrade_count)` shipped without a None-guard even
though alpaca-py's own SDK types the field Optional -- a real account
response without it crashed every cycle it hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from dataclasses import replace as dc_replace

from src.common.config import load_config
from src.common.models import Side
from src.common.secrets import TradingCredentials
from src.execution.broker_alpaca import AlpacaBroker, build_broker, is_stop_order


def _creds() -> TradingCredentials:
    return TradingCredentials(key_id="k", secret_key="s", base_url="https://paper-api.alpaca.markets")


def _install_fake_client(broker: AlpacaBroker, client) -> None:
    broker._get_client = lambda: client


def _fake_order_response(req, legs=()) -> SimpleNamespace:
    """Echoes back enough of a real Order response for _to_order_view to
    parse, plus keeps req itself reachable for the test to inspect exactly
    what was requested."""
    return SimpleNamespace(
        id="ord-1", client_order_id=req.client_order_id, symbol=req.symbol,
        qty=req.qty, side=req.side, type=req.type, status="held",
        stop_price=getattr(req, "stop_price", None),
        limit_price=getattr(req, "limit_price", None),
        filled_qty=0, filled_avg_price=None, legs=legs,
    )


class _CapturingClient:
    """Captures the exact OrderRequest submitted so tests can assert on
    time_in_force / order_class / side -- the fields that actually matter
    here (Alpaca silently overriding a requested GTC on an OTO/bracket child
    leg is what caused the real naked-position incident; these tests confirm
    the CODE now asks for the right thing on each of the three order paths)."""

    def __init__(self):
        self.submitted = None

    def submit_order(self, req):
        self.submitted = req
        legs = ()
        if getattr(req, "stop_loss", None) is not None and getattr(req, "take_profit", None) is not None:
            stop_leg = SimpleNamespace(
                id="leg-stop", client_order_id=req.client_order_id + "-sl", symbol=req.symbol,
                qty=req.qty, side=req.side, type="stop", status="held",
                stop_price=req.stop_loss.stop_price, limit_price=None,
                filled_qty=0, filled_avg_price=None, legs=(),
            )
            tp_leg = SimpleNamespace(
                id="leg-tp", client_order_id=req.client_order_id + "-tp", symbol=req.symbol,
                qty=req.qty, side=req.side, type="limit", status="held",
                stop_price=None, limit_price=req.take_profit.limit_price,
                filled_qty=0, filled_avg_price=None, legs=(),
            )
            legs = (stop_leg, tp_leg)
        return _fake_order_response(req, legs=legs)


def _fake_account(**overrides) -> SimpleNamespace:
    base = {"equity": "10000.0", "last_equity": "10050.0", "buying_power": "20000.0"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_get_account_maps_fields() -> None:
    """daytrade_count/pattern_day_trader are no longer read at all: FINRA
    retired the PDT rule (Regulatory Notice 26-10, 2026-06-04) and Alpaca
    removed these API fields on 2026-07-06 -- see broker_alpaca.py's
    AccountView docstring and docs/ROADMAP.md."""
    broker = AlpacaBroker(creds=_creds())
    _install_fake_client(broker, SimpleNamespace(get_account=lambda: _fake_account()))

    acct = broker.get_account()

    assert acct.equity == 10000.0
    assert acct.last_equity == 10050.0
    assert acct.buying_power == 20000.0


def test_submit_market_entry_is_day_no_legs_no_order_class() -> None:
    """The entry alone -- DAY is fine (a market order fills immediately or
    not at all), and critically NO order_class/stop_loss: this is what
    decouples the entry from Alpaca's OTO/bracket per-leg TIF limitation."""
    from alpaca.trading.enums import TimeInForce

    broker = AlpacaBroker(creds=_creds())
    client = _CapturingClient()
    _install_fake_client(broker, client)

    broker.submit_market_entry("AAPL", 10, Side.LONG, client_order_id="c-entry")

    req = client.submitted
    assert req.time_in_force == TimeInForce.DAY
    assert getattr(req, "order_class", None) in (None, "")
    assert getattr(req, "stop_loss", None) is None


def test_submit_stop_is_standalone_gtc_with_correct_exit_side() -> None:
    """The protective stop for a LONG position must be a SELL order (exits
    the position), standalone GTC -- not nested under any parent."""
    from alpaca.trading.enums import OrderSide, TimeInForce

    broker = AlpacaBroker(creds=_creds())
    client = _CapturingClient()
    _install_fake_client(broker, client)

    order = broker.submit_stop("AAPL", 10, Side.LONG, stop_price=90.0, client_order_id="c-stop")

    req = client.submitted
    assert req.time_in_force == TimeInForce.GTC
    assert req.side == OrderSide.SELL
    assert req.stop_price == 90.0
    assert order.legs == ()          # no legs: standalone, not an OTO/bracket child


def test_submit_stop_for_short_position_exits_via_buy() -> None:
    from alpaca.trading.enums import OrderSide

    broker = AlpacaBroker(creds=_creds())
    client = _CapturingClient()
    _install_fake_client(broker, client)

    broker.submit_stop("TSLA", 5, Side.SHORT, stop_price=250.0, client_order_id="c-stop")

    assert client.submitted.side == OrderSide.BUY


def test_get_order_maps_expires_at() -> None:
    """OrderView.expires_at feeds OrderManager.refresh_stale_stop, which
    proactively refreshes a resting GTC stop before Alpaca's 90-day
    aged-order policy auto-cancels it (docs.alpaca.markets/us/docs/
    orders-at-alpaca; confirmed against alpaca-py's Order.expires_at field
    on the installed SDK). Must map through unchanged when present, and
    stay None for order types that don't carry one."""
    when = datetime(2026, 11, 1, 16, 15, tzinfo=timezone.utc)
    broker = AlpacaBroker(creds=_creds())
    order = SimpleNamespace(
        id="o1", client_order_id="c1", symbol="AAPL", qty=10, side="sell",
        type="stop", status="held", stop_price=90.0, limit_price=None,
        filled_qty=0, filled_avg_price=None, legs=(), expires_at=when,
    )
    _install_fake_client(broker, SimpleNamespace(get_order_by_id=lambda oid: order))

    assert broker.get_order("o1").expires_at == when


def test_get_order_expires_at_defaults_none_when_absent() -> None:
    broker = AlpacaBroker(creds=_creds())
    order = SimpleNamespace(
        id="o1", client_order_id="c1", symbol="AAPL", qty=10, side="buy",
        type="market", status="filled", stop_price=None, limit_price=None,
        filled_qty=10, filled_avg_price=100.0, legs=(),
    )  # no expires_at attribute at all -- mirrors a DAY order's real response
    _install_fake_client(broker, SimpleNamespace(get_order_by_id=lambda oid: order))

    assert broker.get_order("o1").expires_at is None


def test_submit_oco_exit_is_gtc_oco_with_both_legs() -> None:
    from alpaca.trading.enums import OrderClass, TimeInForce

    broker = AlpacaBroker(creds=_creds())
    client = _CapturingClient()
    _install_fake_client(broker, client)

    order = broker.submit_oco_exit(
        "AAPL", 10, Side.LONG, stop_price=90.0, take_profit_price=110.0,
        client_order_id="c-oco",
    )

    req = client.submitted
    assert req.time_in_force == TimeInForce.GTC
    assert req.order_class == OrderClass.OCO
    assert req.stop_loss.stop_price == 90.0
    assert req.take_profit.limit_price == 110.0
    assert len(order.legs) == 2


# --- is_stop_order: the one definition reconciler.py's naked-position
# detector and OrderManager's OCO-leg classification both rely on ---

def test_is_stop_order_true_for_stop_family_types():
    for t in ("stop", "stop_limit", "trailing_stop"):
        assert is_stop_order(t) is True


def test_is_stop_order_false_for_non_stop_types():
    for t in ("limit", "market", "take_profit"):
        assert is_stop_order(t) is False


# --- build_broker: the one place that decides paper vs. live ---

def _config(*, mode: str):
    base = load_config()
    return dc_replace(base, settings={**base.settings, "mode": mode})


def test_build_broker_defaults_to_paper(monkeypatch) -> None:
    monkeypatch.setattr("src.execution.broker_alpaca.load_trading_credentials", _creds)
    broker = build_broker(_config(mode="paper"))
    assert broker._paper is True


def test_build_broker_live_mode_alone_is_not_enough(monkeypatch) -> None:
    # mode: live without allow_live=True must still be paper -- the whole
    # point of requiring both signals.
    monkeypatch.setattr("src.execution.broker_alpaca.load_trading_credentials", _creds)
    broker = build_broker(_config(mode="live"))
    assert broker._paper is True


def test_build_broker_allow_live_alone_is_not_enough(monkeypatch) -> None:
    # allow_live=True with mode: paper must still be paper.
    monkeypatch.setattr("src.execution.broker_alpaca.load_trading_credentials", _creds)
    broker = build_broker(_config(mode="paper"), allow_live=True)
    assert broker._paper is True


def test_build_broker_goes_live_only_with_both_signals(monkeypatch) -> None:
    monkeypatch.setattr("src.execution.broker_alpaca.load_trading_credentials", _creds)
    broker = build_broker(_config(mode="live"), allow_live=True)
    assert broker._paper is False
