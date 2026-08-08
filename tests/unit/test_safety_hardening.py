"""Tests for the safety-hardening pass: crash-safe state files, the
market-hours rule, the stale-data guard, per-symbol stop-raise isolation,
side-aware reconciliation, and the ops snapshot."""

from __future__ import annotations

import json

import pandas as pd

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import AuditLog
from src.common.models import Side
from src.core.orchestrator import Orchestrator
from src.core.proposals import Proposal, ProposalStore
from src.core.state_store import HaltStore, StateStore
from src.core.trade_service import TradeService
from src.core import portfolio_view
from src.data.ingest import last_bar_age_days
from src.execution.broker_alpaca import OrderView, PositionView
from src.execution.order_manager import ManagedPosition, PositionStatus
from src.execution.reconciler import Reconciler
from src.research.scoreboard import Scoreboard
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features


# --- crash-safe JSON ---------------------------------------------------------

def test_atomic_write_and_load_roundtrip(tmp_path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1})
    payload, quarantined = load_json_or_quarantine(path)
    assert payload == {"a": 1} and quarantined is None
    assert not list(tmp_path.glob("*.tmp"))          # no temp litter


def test_corrupt_file_is_quarantined_not_trusted(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"truncated": ', encoding="utf-8")
    payload, quarantined = load_json_or_quarantine(path)
    assert payload is None and quarantined is not None
    assert quarantined.exists() and not path.exists()  # evidence kept, original moved


def test_state_store_survives_corrupt_positions_file(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text("NOT JSON", encoding="utf-8")
    assert StateStore(path).load() == {}               # safe default, no crash
    assert list(tmp_path.glob("positions.json.corrupt-*"))


def test_unreadable_halt_file_still_means_halted(tmp_path):
    path = tmp_path / "halt.json"
    path.write_text("{garbage", encoding="utf-8")
    store = HaltStore(path)
    assert store.is_halted()                           # default-to-halt
    assert store.halt_info()["class"] == "unknown"


# --- market-hours rule -------------------------------------------------------

def _service(broker, tmp_path):
    return TradeService(
        broker=broker,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        price_fn=lambda s: 100.0,
    )


def test_manual_buy_refused_while_market_closed(tmp_path):
    broker = FakeBroker(market_open=False)
    result = _service(broker, tmp_path).place_manual("NVDA", 10)
    assert not result.ok and result.status == "market_closed"
    assert broker._orders == {}                        # nothing reached the broker


def test_approval_refused_while_market_closed(tmp_path):
    broker = FakeBroker(market_open=False)
    intent = {"symbol": "NVDA", "signal": "BUY", "confidence": 0.7,
              "strategy": "trend_following", "entry_price": 100.0,
              "stop_loss": 90.0, "take_profit": None}
    proposal = Proposal.create(intent, approved_qty=10, strategy="trend_following")
    result = _service(broker, tmp_path).execute_approved(proposal)
    assert not result.ok and result.status == "market_closed"
    assert broker._orders == {}


# --- stale-data guard --------------------------------------------------------

def _dated(df: pd.DataFrame, end: str | pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.date_range(end=end, periods=len(df), freq="D")
    return df


def _approve_rows():
    return [
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30, "atr": 1.0},
    ]


def test_last_bar_age_days():
    fresh = _dated(make_features(_approve_rows()), pd.Timestamp.today().normalize())
    old = _dated(make_features(_approve_rows()), "2020-01-02")
    assert last_bar_age_days(fresh) == 0
    assert last_bar_age_days(old) > 1000
    assert last_bar_age_days(make_features(_approve_rows())) is None  # RangeIndex: unknown


def _orch(broker, tmp_path, provider, **kw):
    kw.setdefault("execute", False)
    return Orchestrator(
        broker=broker, feature_provider=provider,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        scoreboard=Scoreboard(tmp_path / "scoreboard.json"), **kw,
    )


def test_all_symbols_stale_halts_cycle(tmp_path):
    stale = _dated(make_features(_approve_rows()), "2020-01-02")
    report = _orch(FakeBroker(), tmp_path, lambda s: stale).run_cycle()
    assert report.halted and "stale data" in report.halt_reason
    assert HaltStore(tmp_path / "halt.json").halt_info()["class"] == "stale_data"


def test_single_stale_symbol_is_skipped_not_halting(tmp_path):
    stale = _dated(make_features(_approve_rows()), "2020-01-02")
    fresh = _dated(make_features(_approve_rows()), pd.Timestamp.today().normalize())

    def provider(sym):
        return stale if sym == "AAPL" else fresh

    report = _orch(FakeBroker(), tmp_path, provider).run_cycle()
    assert not report.halted
    assert [s for s, _age in report.stale] == ["AAPL"]
    assert "AAPL" not in report.opened and len(report.opened) == 2


# --- stop-raise isolation ----------------------------------------------------

class RejectingBroker(FakeBroker):
    def replace_stop(self, order_id, stop_price):
        raise RuntimeError("alpaca rejected the replace")


def test_one_failed_stop_replace_does_not_halt(tmp_path):
    broker = RejectingBroker(positions=[PositionView("TSLA", 10, Side.LONG, 100.0)])
    broker.seed_order(OrderView(id="stp-1", client_order_id="c-sl", symbol="TSLA", qty=10,
                                side="sell", type="stop", status="accepted", stop_price=90.0))
    ratchet = PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0,
                             lock_trigger_pct=5.0, profit_lock_pct=2.0, step_pct=5.0)
    pos = ManagedPosition(symbol="TSLA", side=Side.LONG, qty=10, strategy="trend_following",
                          entry_order_id="e-1", stop_order_id="stp-1", current_stop=90.0,
                          ratchet=ratchet, status=PositionStatus.OPEN, filled_qty=10)
    quiet = make_features([{"close": 110.0, "ema50": 110.0}])  # advances ratchet, no entry/exit
    orch = _orch(broker, tmp_path, lambda s: quiet, execute=True)
    orch.positions = {"TSLA": pos}

    report = orch.run_cycle()
    assert not report.halted                       # isolated, resting stop still protects
    assert report.stops_raised == []
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(50)]
    assert "stop_raise_error" in events


# --- side-aware reconciliation ----------------------------------------------

def test_reconciler_flags_side_mismatch():
    broker = FakeBroker(positions=[PositionView("TSLA", 10, Side.SHORT, 100.0)])
    broker.seed_order(OrderView(id="s1", client_order_id="c", symbol="TSLA", qty=10,
                                side="buy", type="stop", status="accepted", stop_price=110.0))
    pos = ManagedPosition(symbol="TSLA", side=Side.LONG, qty=10, strategy="t",
                          entry_order_id="e", stop_order_id="s1", current_stop=90.0,
                          ratchet=None, status=PositionStatus.OPEN, filled_qty=10)
    report = Reconciler(broker).reconcile({"TSLA": pos})
    assert not report.ok and "TSLA" in report.quantity_mismatches


# --- ops snapshot ------------------------------------------------------------

def test_ops_snapshot_surfaces_cycle_and_proposals(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.record("risk_decision", symbol="AAPL")
    audit.record("cycle_complete", mode="propose", summary="ok")
    proposals = ProposalStore(tmp_path / "proposals.json")
    intent = {"symbol": "NVDA", "signal": "BUY", "confidence": 0.7,
              "strategy": "trend_following", "entry_price": 100.0, "stop_loss": 90.0}
    proposals.add(Proposal.create(intent, approved_qty=5, strategy="trend_following"))

    snap = portfolio_view.ops_snapshot(
        halt_store=HaltStore(tmp_path / "halt.json"), audit=audit,
        proposal_store=proposals)
    assert snap["halt"] is None
    assert snap["last_cycle"]["mode"] == "propose"
    assert snap["pending_proposals"][0]["symbol"] == "NVDA"


def test_cycle_complete_event_is_recorded(tmp_path):
    fresh = _dated(make_features(_approve_rows()), pd.Timestamp.today().normalize())
    _orch(FakeBroker(), tmp_path, lambda s: fresh).run_cycle()
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(50)]
    assert "cycle_complete" in events
