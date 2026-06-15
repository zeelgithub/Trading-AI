"""Tests for the order manager: protected entries, fills, raising stops, close."""

from __future__ import annotations

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


def test_protected_entry_no_tp_is_oto():
    broker = FakeBroker(auto_fill=False)
    pos = OrderManager(broker).open_position(make_decision(), make_ratchet(), tag="t1")
    assert pos.status == PositionStatus.PENDING_ENTRY
    assert pos.stop_order_id is not None
    assert pos.tp_order_id is None              # no take-profit leg
    assert pos.current_stop == pytest.approx(90.0)
    stops = [o for o in broker._orders.values() if o.type == "stop"]
    assert len(stops) == 1 and stops[0].stop_price == pytest.approx(90.0)


def test_protected_entry_with_tp_is_bracket():
    broker = FakeBroker(auto_fill=False)
    pos = OrderManager(broker).open_position(
        make_decision(take_profit=102.0, strategy="mean_reversion"), make_ratchet(), tag="t1"
    )
    assert pos.tp_order_id is not None
    tps = [o for o in broker._orders.values() if o.type == "limit"]
    assert len(tps) == 1 and tps[0].limit_price == pytest.approx(102.0)


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


def test_raise_stop_replaces_leg():
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    assert om.raise_stop(pos, 120.0) is True
    assert pos.current_stop == pytest.approx(110.0)
    assert broker.replaced and broker.replaced[-1][1] == pytest.approx(110.0)
    assert om.raise_stop(pos, 105.0) is False   # never lowers


def test_close_liquidates_and_cancels_legs():
    broker = FakeBroker()
    om = OrderManager(broker)
    pos = om.open_position(make_decision(), make_ratchet(), tag="t1")
    om.close(pos, "signal")
    assert pos.status == PositionStatus.CLOSED
    assert broker.closed == ["AAPL"]
    assert all(o.symbol != "AAPL" for o in broker._orders.values())  # legs cancelled


def test_cannot_open_on_veto():
    broker = FakeBroker()
    veto = RiskDecision(intent=make_decision().intent, decision=Decision.VETO)
    with pytest.raises(ValueError):
        OrderManager(broker).open_position(veto, make_ratchet(), tag="t1")
