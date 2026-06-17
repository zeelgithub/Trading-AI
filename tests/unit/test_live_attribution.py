"""Live attribution: a real close records realized PnL against its strategy."""

from __future__ import annotations

import pytest

from src.common.config import load_config
from src.common.logging import AuditLog
from src.common.models import Side
from src.core.orchestrator import Orchestrator
from src.core.state_store import HaltStore, StateStore
from src.execution.broker_alpaca import OrderView, PositionView
from src.execution.order_manager import ManagedPosition, PositionStatus, realized_pnl
from src.research.scoreboard import Scoreboard
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker
from tests.unit.test_orchestrator import exit_frame


def test_realized_pnl_long_and_short():
    assert realized_pnl(Side.LONG, 100.0, 50, 95.0) == pytest.approx(-250.0)
    assert realized_pnl(Side.LONG, 100.0, 50, 110.0) == pytest.approx(500.0)
    assert realized_pnl(Side.SHORT, 100.0, 50, 95.0) == pytest.approx(250.0)
    assert realized_pnl(Side.SHORT, 100.0, 50, 110.0) == pytest.approx(-500.0)


def test_signal_exit_records_live_attribution(tmp_path):
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    broker.seed_order(OrderView(id="s1", client_order_id="c", symbol="AAPL", qty=50,
                                side="sell", type="stop", status="held", stop_price=90.0))
    sb = Scoreboard(tmp_path / "sb.json")
    orch = Orchestrator(
        broker=broker, feature_provider=exit_frame, execute=True,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
        scoreboard=sb,
    )
    orch.positions = {
        "AAPL": ManagedPosition(
            symbol="AAPL", side=Side.LONG, qty=50, strategy="trend_following",
            entry_order_id="e1", stop_order_id="s1", current_stop=90.0,
            ratchet=PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0),
            status=PositionStatus.OPEN, filled_qty=50,
        )
    }

    report = orch.run_cycle()
    assert ("AAPL", "opposite_ema_break") in report.exited

    scores = sb.load()
    assert "trend_following" in scores
    assert scores["trend_following"].live_num_trades == 1
    # exit_frame close = 95, entry = 100, long, qty 50 -> (95-100)*50 = -250
    assert scores["trend_following"].live_total_pnl == pytest.approx(-250.0)


def test_shadow_mode_records_nothing(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    orch = Orchestrator(
        broker=FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False),
        feature_provider=exit_frame, execute=False,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
        scoreboard=sb,
    )
    orch.positions = {
        "AAPL": ManagedPosition(
            symbol="AAPL", side=Side.LONG, qty=50, strategy="trend_following",
            entry_order_id="e1", stop_order_id="s1", current_stop=90.0,
            ratchet=PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0),
            status=PositionStatus.OPEN, filled_qty=50,
        )
    }
    orch.run_cycle()
    assert sb.load() == {}    # shadow mode: a "would exit" is logged, nothing recorded
