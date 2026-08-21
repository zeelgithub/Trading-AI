"""Tests for the pending-aware reconciler (broker = source of truth)."""

from __future__ import annotations

from types import SimpleNamespace

from src.common.models import Side
from src.execution.broker_alpaca import OrderView, PositionView
from src.execution.order_manager import PositionStatus
from src.execution.reconciler import Reconciler
from tests.unit.fakes import FakeBroker


def pos(symbol, status, qty=50, filled=50):
    return SimpleNamespace(symbol=symbol, status=status, side=Side.LONG, qty=qty, filled_qty=filled)


def stop_order(symbol):
    return OrderView(id="s1", client_order_id="c1", symbol=symbol, qty=50, side="sell",
                     type="stop", status="held", stop_price=90.0)


def entry_order(symbol):
    return OrderView(id="e1", client_order_id="c2", symbol=symbol, qty=50, side="buy",
                     type="market", status="accepted")


def test_clean_open_reconcile():
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    broker.seed_order(stop_order("AAPL"))
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.OPEN)})
    assert report.ok


def test_pending_entry_with_working_order_is_ok():
    # Entry not filled yet: no broker position, but the order is working.
    broker = FakeBroker(auto_fill=False)
    broker.seed_order(entry_order("AAPL"))
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.PENDING_ENTRY)})
    assert report.ok                       # the old code falsely halted here


def test_pending_entry_vanished_is_flagged():
    broker = FakeBroker(auto_fill=False)   # no order, no position
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.PENDING_ENTRY)})
    assert not report.ok
    assert "AAPL" in report.pending_lost


def test_pending_entry_actually_filled_at_broker_without_stop_is_unprotected():
    """Regression: a real incident left 7 of 8 positions genuinely filled and
    naked at the broker, but locally still marked PENDING_ENTRY (a crash
    between fill and settle() never updated it) -- the OLD reconciler only
    checked broker-position EXISTENCE for a pending entry, so these sailed
    through reconcile clean for weeks. A broker position that already exists
    is exposed to real risk regardless of what local status says."""
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.PENDING_ENTRY)})
    assert not report.ok
    assert report.unprotected_positions == ["AAPL"]


def test_pending_entry_actually_filled_at_broker_with_stop_is_ok():
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    broker.seed_order(stop_order("AAPL"))
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.PENDING_ENTRY)})
    assert report.ok


def test_open_without_stop_is_unprotected():
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.OPEN)})
    assert not report.ok
    assert report.unprotected_positions == ["AAPL"]


def test_qty_mismatch_flagged():
    broker = FakeBroker(positions=[PositionView("AAPL", 40, Side.LONG, 100.0)], auto_fill=False)
    broker.seed_order(stop_order("AAPL"))
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.OPEN, filled=50)})
    assert not report.ok
    assert "AAPL" in report.quantity_mismatches


def test_unknown_broker_position_flagged():
    broker = FakeBroker(positions=[PositionView("TSLA", 10, Side.LONG, 200.0)], auto_fill=False)
    broker.seed_order(stop_order("TSLA"))
    report = Reconciler(broker).reconcile({})
    assert not report.ok
    assert report.unknown_positions == ["TSLA"]


def test_stop_fired_is_auto_closed_not_halted():
    """Stop fires at broker: position + stop order both gone. Should be auto_closed,
    not a halt-triggering quantity_mismatch."""
    broker = FakeBroker(auto_fill=False)  # no position, no orders
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.OPEN)})
    assert report.ok                          # must NOT halt
    assert "AAPL" in report.auto_closed
    assert "AAPL" not in report.quantity_mismatches


def test_open_with_order_but_no_position_is_mismatch():
    """Position gone but an order still exists — genuinely unexpected broker state."""
    broker = FakeBroker(auto_fill=False)
    broker.seed_order(stop_order("AAPL"))    # order exists, but no position
    report = Reconciler(broker).reconcile({"AAPL": pos("AAPL", PositionStatus.OPEN)})
    assert not report.ok
    assert "AAPL" in report.quantity_mismatches
    assert "AAPL" not in report.auto_closed
