"""End-to-end: orchestrator proposes (no orders) -> trade service executes on approval."""

from __future__ import annotations

from src.common.logging import AuditLog
from src.core.orchestrator import Orchestrator
from src.core.state_store import HaltStore, StateStore
from src.core.trade_service import TradeService
from src.execution.order_manager import PositionStatus
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features


def trending_long(_symbol=None):
    """A trending long setup that passes regime + trend-following + risk."""
    rows = [
        {"close": 102, "open": 102},
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 35, "atr": 1.0},
    ]
    return make_features(rows)


def make_orch(broker, tmp_path, **kw):
    return Orchestrator(
        broker=broker, feature_provider=trending_long, propose=True,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"),
        audit=AuditLog(tmp_path / "a.jsonl"), **kw,
    )


def test_propose_mode_emits_proposals_and_places_nothing(tmp_path):
    broker = FakeBroker(auto_fill=True)
    report = make_orch(broker, tmp_path).run_cycle()
    assert not report.halted
    assert len(report.proposals) == 3          # one per enabled symbol
    assert report.opened == []                 # nothing opened
    assert broker._orders == {}                # NO orders placed in propose mode
    p = report.proposals[0]
    assert p.status == "pending" and p.intent["stop_loss"] < p.intent["entry_price"]


def test_approving_a_proposal_opens_a_protected_position(tmp_path):
    proposals = make_orch(FakeBroker(), tmp_path).run_cycle().proposals

    # A fresh broker/service approves the proposal (as the phone listener would).
    broker = FakeBroker(auto_fill=True)
    svc = TradeService(
        broker=broker,
        state_store=StateStore(tmp_path / "live.json"),
        halt_store=HaltStore(tmp_path / "h.json"),
        price_fn=lambda s: 105.0,
    )
    result = svc.execute_approved(proposals[0])
    assert result.ok and result.status == "placed"
    stops = [o for o in broker._orders.values() if o.type == "stop"]
    assert len(stops) == 1                      # protected entry, never naked
    live = StateStore(tmp_path / "live.json").load()
    assert next(iter(live.values())).status == PositionStatus.OPEN
