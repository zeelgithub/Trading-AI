"""Tests for the order manager: protected entries, fills, raising stops, close."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.common.models import Action, Decision, Intent, RiskDecision, Side
from src.execution.order_manager import OrderManager, PositionStatus
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker


def make_decision(qty=50.0, take_profit=None, strategy="trend_following") -> RiskDecision:
    intent = Intent(
        symbol="AAPL", strategy=strategy, side=Side.LONG, action=Action.BUY,
        confidence=0.7, entry_price=100.0, stop_loss=90.0, take_profit=take_profit,
    )
    return RiskDecision(intent=intent, decision=Decision.APPROVE, approved_qty=qty)


def make_ratchet() -> PercentRatchet:
    return PercentRatchet(
        entry=100.0, side=Side.LONG, initial_stop_pct=10.0,
        lock_trigger_pct=20.0, profit_lock_pct=10.0, step_pct=20.0,
    )


def test_open_position_submits_entry_alone_no_stop_yet():
    """The entry is submitted without any attached legs -- the protective
    stop isn't placed until settle() confirms a fill (see module docstring:
    Alpaca doesn't honor GTC on OTO/bracket child legs, so it can't be
    attached atomically anymore)."""
    broker = FakeBroker(auto_fill=False)
    pos = OrderManager(broker).open_position(make_decision(), make_ratchet(), tag="t1")
    assert pos.status == PositionStatus.PENDING_ENTRY
    assert pos.stop_order_id is None
    assert pos.tp_order_id is None
    assert pos.current_stop == pytest.approx(90.0)
    assert all(o.type != "stop" for o in broker._orders.values())


def test_settle_attaches_standalone_stop_no_tp():
    broker = FakeBroker(auto_fill=True)
    pos = OrderManager(broker).open_position(make_decision(), make_ratchet(), tag="t1")
    assert OrderManager(broker).settle(pos) == PositionStatus.OPEN
    assert pos.stop_order_id is not None
    assert pos.tp_order_id is None              # no take-profit leg
    stops = [o for o in broker._orders.values() if o.type == "stop"]
    assert len(stops) == 1 and stops[0].stop_price == pytest.approx(90.0)


def test_settle_attaches_gtc_oco_when_take_profit_present():
    broker = FakeBroker(auto_fill=True)
    om = OrderManager(broker)
    pos = om.open_position(
        make_decision(take_profit=102.0, strategy="mean_reversion"), make_ratchet(), tag="t1"
    )
    assert om.settle(pos) == PositionStatus.OPEN
    assert pos.tp_order_id is not None
    tp_leg = broker._orders[pos.tp_order_id]
    assert tp_leg.type == "limit" and tp_leg.limit_price == pytest.approx(102.0)


def test_settle_propagates_failure_to_attach_protection_instead_of_marking_open():
    """No naked positions (rule 4): if the standalone stop can't be placed
    after a confirmed fill, settle() must NOT mark the position OPEN -- the
    exception has to propagate so the cycle halts (rule 3), not get
    swallowed into a silently-unprotected 'open' position."""
    broker = FakeBroker(auto_fill=True)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")

    def _boom(*a, **kw):
        raise RuntimeError("broker unreachable")
    broker.submit_stop = _boom

    with pytest.raises(RuntimeError, match="broker unreachable"):
        om.settle(pos)
    assert pos.status == PositionStatus.PENDING_ENTRY
    assert pos.stop_order_id is None


def test_settle_reuses_open_tag_for_protection_idempotency():
    """Re-attempting settle() after protection already attached must not
    submit a second stop -- and if it somehow did retry (stop_order_id still
    None), it would reuse the SAME client_order_id every time because it's
    derived from the position's own open_tag, not from "today", so a retry
    spanning a halt is idempotent at the broker rather than risking a second
    real stop order stacked on the first."""
    broker = FakeBroker(auto_fill=True)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="2026-06-15")
    om.settle(pos)
    first_stop_id = pos.stop_order_id
    om.settle(pos)  # re-settle an already-open, already-protected position
    assert pos.stop_order_id == first_stop_id
    assert len([o for o in broker._orders.values() if o.type == "stop"]) == 1


def test_settle_full_fill():
    broker = FakeBroker(auto_fill=True)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(qty=50), make_ratchet(), tag="t1")
    assert om.settle(pos) == PositionStatus.OPEN
    assert pos.filled_qty == 50


def test_settle_partial_fill():
    broker = FakeBroker(auto_fill=False)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(qty=50), make_ratchet(), tag="t1")
    broker.set_fill(pos.entry_order_id, 20, status="partially_filled")
    assert om.settle(pos) == PositionStatus.OPEN
    assert pos.filled_qty == 20            # act on filled, not ordered


def test_recheck_partial_fill_raises_when_more_has_filled():
    """A resting stop sized for the first partial fill (20/50) doesn't
    magically grow if the order keeps filling -- recheck_partial_fill must
    catch that and halt (rule 4/3), not silently accept it."""
    broker = FakeBroker(auto_fill=False)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(qty=50), make_ratchet(), tag="t1")
    broker.set_fill(pos.entry_order_id, 20, status="partially_filled")
    om.settle(pos)
    assert pos.status == PositionStatus.OPEN and pos.filled_qty == 20

    broker.set_fill(pos.entry_order_id, 35, status="partially_filled")
    with pytest.raises(RuntimeError, match="entry filled further"):
        om.recheck_partial_fill(pos)


def test_recheck_partial_fill_is_a_noop_when_nothing_changed():
    broker = FakeBroker(auto_fill=False)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(qty=50), make_ratchet(), tag="t1")
    broker.set_fill(pos.entry_order_id, 20, status="partially_filled")
    om.settle(pos)

    om.recheck_partial_fill(pos)  # same 20 filled -- must not raise

    assert pos.filled_qty == 20
    assert pos.status == PositionStatus.OPEN


def test_raise_stop_replaces_leg():
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.settle(pos)                              # confirm the fill -> attaches the stop
    assert om.raise_stop(pos, 120.0) is True
    assert pos.current_stop == pytest.approx(110.0)
    assert broker.replaced and broker.replaced[-1][1] == pytest.approx(110.0)
    assert om.raise_stop(pos, 105.0) is False   # never lowers


def test_refresh_stale_stop_false_when_not_yet_protected():
    """Before settle() attaches a stop, there's nothing to refresh."""
    broker = FakeBroker(auto_fill=False)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")

    assert om.refresh_stale_stop(pos, now=datetime.now(timezone.utc)) is False
    assert broker.replaced == []


def test_refresh_stale_stop_false_when_broker_reports_no_expiry():
    """FakeBroker's stop orders default to expires_at=None (mirrors a broker
    response that doesn't carry the field) -- nothing to compare, so no-op
    rather than guessing."""
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.settle(pos)

    assert om.refresh_stale_stop(pos, now=datetime.now(timezone.utc)) is False
    assert broker.replaced == []


def _set_stop_expiry(broker: FakeBroker, pos, expires_at) -> None:
    order = broker._orders[pos.stop_order_id]
    broker.seed_order(replace(order, expires_at=expires_at))


def test_refresh_stale_stop_false_when_far_from_expiry():
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.settle(pos)
    now = datetime.now(timezone.utc)
    _set_stop_expiry(broker, pos, now + timedelta(days=40))  # well past the 15d default margin

    assert om.refresh_stale_stop(pos, now=now, min_days_remaining=15) is False
    assert broker.replaced == []


def test_refresh_stale_stop_replaces_at_same_price_when_near_expiry():
    """Within the safety margin: replace at the SAME price (identical
    protection level) purely to reset Alpaca's 90-day clock, and track the
    new order id the same way raise_stop does."""
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.settle(pos)
    old_stop_id = pos.stop_order_id
    now = datetime.now(timezone.utc)
    _set_stop_expiry(broker, pos, now + timedelta(days=10))  # inside the 15d default margin

    assert om.refresh_stale_stop(pos, now=now, min_days_remaining=15) is True

    assert broker.replaced[-1] == (old_stop_id, pytest.approx(pos.current_stop))
    assert pos.stop_order_id != old_stop_id       # replace returns a new order id
    assert pos.current_stop == pytest.approx(90.0)  # protection level unchanged


def test_close_liquidates_and_cancels_legs():
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.settle(pos)                              # confirm the fill -> attaches the stop
    om.close(pos, "signal")
    assert pos.status == PositionStatus.CLOSED
    assert broker.closed == ["AAPL"]
    assert all(o.symbol != "AAPL" for o in broker._orders.values())  # legs cancelled


def test_cannot_open_on_veto():
    broker = FakeBroker()
    veto = RiskDecision(intent=make_decision().intent, decision=Decision.VETO)
    with pytest.raises(ValueError):
        OrderManager(broker).open_position(veto, make_ratchet(), tag="t1")
