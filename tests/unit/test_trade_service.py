"""Tests for the gated trade service shared by the CLI and the phone."""

from __future__ import annotations

from src.core.proposals import Proposal
from src.core.state_store import HaltStore, StateStore
from src.core.trade_service import TradeService
from src.execution.broker_alpaca import AccountView
from src.execution.order_manager import PositionStatus
from tests.unit.fakes import FakeBroker


def make_service(broker, tmp_path, **kw):
    return TradeService(
        broker=broker,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        price_fn=lambda s: 100.0,
        **kw,
    )


def _proposal(symbol="NVDA", qty=10):
    intent = {"symbol": symbol, "signal": "BUY", "confidence": 0.7,
              "strategy": "trend_following", "entry_price": 100.0,
              "stop_loss": 90.0, "take_profit": None}
    return Proposal.create(intent, approved_qty=qty, strategy="trend_following")


def test_place_manual_opens_protected_position(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    result = svc.place_manual("NVDA", 20, stop_pct=10)
    assert result.ok and result.status == "placed"
    stops = [o for o in broker._orders.values() if o.type == "stop"]
    assert len(stops) == 1                       # never naked
    reloaded = StateStore(tmp_path / "positions.json").load()
    assert reloaded["NVDA"].status == PositionStatus.OPEN


def test_place_manual_refused_when_halted(tmp_path):
    svc = make_service(FakeBroker(), tmp_path)
    svc.halt_store.set("manual halt")
    result = svc.place_manual("NVDA", 20)
    assert not result.ok and result.status == "halted"


def test_place_manual_refused_when_already_tracking(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    assert svc.place_manual("NVDA", 10).ok
    again = svc.place_manual("NVDA", 10)
    assert not again.ok and again.status == "exists"


def test_risk_trims_oversized_order(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    result = svc.place_manual("NVDA", 100000, stop_pct=10)
    assert result.ok and result.qty < 100000     # resized by risk caps


def test_kill_switch_vetoes(tmp_path):
    acct = AccountView(equity=45000, last_equity=50000, buying_power=200000,
                       daytrade_count=0, pattern_day_trader=False)
    svc = make_service(FakeBroker(account=acct), tmp_path)
    result = svc.place_manual("NVDA", 10)
    assert not result.ok and result.status == "vetoed"


def test_execute_approved_places_order(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    result = svc.execute_approved(_proposal(qty=10))
    assert result.ok and result.status == "placed"
    assert result.qty == 10
    assert StateStore(tmp_path / "positions.json").load()["NVDA"].status == PositionStatus.OPEN


def test_execute_approved_refused_when_halted(tmp_path):
    svc = make_service(FakeBroker(), tmp_path)
    svc.halt_store.set("halted")
    result = svc.execute_approved(_proposal())
    assert not result.ok and result.status == "halted"


def test_status_reports_positions_and_stops(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    svc.place_manual("NVDA", 10, stop_pct=10)
    status = svc.status()
    assert status.positions and status.positions[0]["symbol"] == "NVDA"
    assert status.positions[0]["stop"] == 90.0    # 10% below 100
    assert "NVDA" in status.text()


def test_close_and_flatten(tmp_path):
    broker = FakeBroker(auto_fill=True)
    svc = make_service(broker, tmp_path)
    svc.place_manual("NVDA", 10)
    assert svc.close("NVDA").ok and "NVDA" in broker.closed
    assert svc.close("NVDA").status == "none"     # already gone

    svc.place_manual("AAPL", 5)
    flat = svc.flatten()
    assert flat.ok and "AAPL" in broker.closed
