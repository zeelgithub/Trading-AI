"""Tests for the real-world-readiness batch: config validation, cross-process
locking (mutual exclusion + busy/skip behavior), configurable backtest costs,
order-rejection surfacing, and self-heal's compare-and-clear under lock."""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from src.common.config_schema import ConfigError, validate_config
from src.common.logging import AuditLog
from src.common.models import Side
from src.common.proclock import BotBusy, bot_lock
from src.core.orchestrator import Orchestrator
from src.core.self_heal import SelfHealer
from src.core.state_store import HaltClass, HaltStore, StateStore
from src.core.trade_service import TradeService
from src.execution.broker_alpaca import OrderView
from src.execution.order_manager import ManagedPosition, OrderManager, PositionStatus, is_dead_entry
from src.research.backtester import Backtester
from src.research.scoreboard import Scoreboard
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features
from tests.unit.test_order_manager import make_decision, make_ratchet


# --- config validation --------------------------------------------------

GOOD_SETTINGS = {"mode": "paper", "data": {"max_bar_age_days": 4},
                 "scheduling": {"evaluate_at": "15:45"}}
GOOD_RISK = {"account": {"max_daily_loss_pct": 4.0}, "ratchet_stop": {
    "trend_following": {"initial_stop_pct": 10.0},
    "breakout": {"atr_multiple_initial": 2.0},
}}


def test_valid_config_passes():
    validate_config(GOOD_SETTINGS, GOOD_RISK)  # must not raise


def test_wrong_type_value_is_rejected():
    bad = {"account": {"max_daily_loss_pct": "four percent"}}
    with pytest.raises(ConfigError, match="max_daily_loss_pct"):
        validate_config(GOOD_SETTINGS, bad)


def test_negative_risk_pct_is_rejected():
    bad = {"position": {"per_trade_risk_pct": -1.0}}
    with pytest.raises(ConfigError):
        validate_config(GOOD_SETTINGS, bad)


def test_bad_evaluate_at_format_is_rejected():
    bad_settings = {**GOOD_SETTINGS, "scheduling": {"evaluate_at": "not-a-time"}}
    with pytest.raises(ConfigError, match="evaluate_at"):
        validate_config(bad_settings, GOOD_RISK)


def test_bad_mode_literal_is_rejected():
    bad_settings = {**GOOD_SETTINGS, "mode": "yolo"}
    with pytest.raises(ConfigError):
        validate_config(bad_settings, GOOD_RISK)


def test_ratchet_needs_one_param_family():
    bad = {"ratchet_stop": {"mystery_strategy": {"unrelated_key": 1.0}}}
    with pytest.raises(ConfigError, match="mystery_strategy"):
        validate_config(GOOD_SETTINGS, bad)


def test_unknown_keys_are_allowed_forward_compatible():
    settings = {**GOOD_SETTINGS, "some_future_section": {"whatever": True}}
    validate_config(settings, GOOD_RISK)  # must not raise


def test_real_config_files_are_valid():
    from src.common.config import load_config

    load_config.cache_clear()
    load_config()  # must not raise -- the shipped YAML must pass its own schema


# --- cross-process locking ------------------------------------------------

def test_bot_lock_serializes_and_times_out(tmp_path):
    lockfile = tmp_path / "test.lock"
    order: list[str] = []

    def holder():
        with bot_lock(timeout=5, path=lockfile):
            order.append("enter")
            time.sleep(0.4)
            order.append("exit")

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.1)
    with pytest.raises(BotBusy):
        with bot_lock(timeout=0.1, path=lockfile):
            order.append("should not happen")
    t.join()
    assert order == ["enter", "exit"]


def _service(broker, tmp_path, **kw):
    return TradeService(
        broker=broker,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        price_fn=lambda s: 100.0,
        lock_path=tmp_path / ".bot.lock",
        **kw,
    )


def test_trade_service_busy_when_lock_held(tmp_path):
    svc = _service(FakeBroker(), tmp_path)
    svc.action_lock_timeout = 0.2
    with bot_lock(timeout=5, path=tmp_path / ".bot.lock"):
        result = svc.halt("test")
    assert not result.ok and result.status == "busy"


def test_trade_service_succeeds_once_lock_released(tmp_path):
    svc = _service(FakeBroker(), tmp_path)
    result = svc.halt("test")
    assert result.ok


def _orch(broker, tmp_path, provider=None, **kw):
    kw.setdefault("execute", False)
    return Orchestrator(
        broker=broker, feature_provider=provider or (lambda s: make_features([])),
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        scoreboard=Scoreboard(tmp_path / "scoreboard.json"),
        lock_path=tmp_path / ".bot.lock",
        **kw,
    )


def test_orchestrator_skips_quietly_when_lock_held(tmp_path):
    orch = _orch(FakeBroker(), tmp_path)
    orch.cycle_lock_timeout = 0.2
    with bot_lock(timeout=5, path=tmp_path / ".bot.lock"):
        report = orch.run_cycle()
    assert not report.halted  # busy is not a fault -- next cycle picks it up
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(20)]
    assert "cycle_skipped_busy" in events


def test_trade_service_and_orchestrator_share_one_lock_file(tmp_path):
    """The whole point: both sides must contend on the SAME lock file."""
    lock_path = tmp_path / ".bot.lock"
    svc = _service(FakeBroker(), tmp_path)
    assert svc.lock_path == lock_path
    orch = _orch(FakeBroker(), tmp_path)
    assert orch.lock_path == lock_path


# --- self-heal compare-and-clear under lock -------------------------------

def _healer(tmp_path, verifiers=None, **kw):
    return SelfHealer(
        HaltStore(tmp_path / "halt.json"),
        verifiers=verifiers or {HaltClass.STALE_DATA: lambda: True},
        counter_path=tmp_path / "self_heal.json",
        cooldown_seconds=0,
        lock_path=tmp_path / ".bot.lock",
        **kw,
    )


def test_self_heal_resumes_when_uncontended(tmp_path):
    halts = HaltStore(tmp_path / "halt.json")
    halts.set("stale", HaltClass.STALE_DATA)
    result = _healer(tmp_path).attempt_resume()
    assert result.resumed
    assert halts.is_halted() is None


def test_self_heal_aborts_if_halt_changed_during_verification(tmp_path):
    """A fresh /halt racing in during a slow verifier must NOT be clobbered."""
    halts = HaltStore(tmp_path / "halt.json")
    halts.set("stale", HaltClass.STALE_DATA)

    def slow_verifier():
        # Simulate a /halt arriving mid-verification (different reason/class).
        halts.set("operator says stop", HaltClass.MANUAL)
        return True

    healer = _healer(tmp_path, verifiers={HaltClass.STALE_DATA: slow_verifier})
    result = healer.attempt_resume()
    assert not result.resumed and result.escalate
    assert halts.is_halted() == "operator says stop"  # untouched


def test_self_heal_reports_busy_state_when_lock_contended(tmp_path):
    halts = HaltStore(tmp_path / "halt.json")
    halts.set("stale", HaltClass.STALE_DATA)
    healer = _healer(tmp_path, lock_timeout=0.2)
    with bot_lock(timeout=5, path=tmp_path / ".bot.lock"):
        result = healer.attempt_resume()
    assert not result.resumed
    assert halts.is_halted() == "stale"  # never touched


# --- backtest cost config --------------------------------------------------

def test_backtester_reads_costs_from_config():
    from dataclasses import replace

    from src.common.config import load_config

    base = load_config()
    cfg = replace(base, settings={
        **base.settings,
        "backtest": {"commission_per_share": 0.01, "slippage_bps": 12.0},
    })
    bt = Backtester(config=cfg)
    assert bt.commission == pytest.approx(0.01)
    assert bt.slip == pytest.approx(12.0 / 10_000.0)


def test_backtester_explicit_args_override_config():
    bt = Backtester(commission_per_share=0.02, slippage_bps=3.0)
    assert bt.commission == pytest.approx(0.02)
    assert bt.slip == pytest.approx(3.0 / 10_000.0)


# --- order rejection surfacing ---------------------------------------------

def test_settle_rejected_order_is_dead_entry():
    broker = FakeBroker(auto_fill=False)
    om = OrderManager(broker)
    pos = om.open_position(make_decision(qty=50), make_ratchet(), tag="t1")
    broker.set_fill(pos.entry_order_id, 0, status="rejected")
    assert om.settle(pos) == PositionStatus.CLOSED
    assert is_dead_entry(pos)
    assert pos.last_order_status == "rejected"


def test_orchestrator_settle_pending_drops_and_audits_rejection(tmp_path):
    broker = FakeBroker(auto_fill=False)
    ratchet = PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0)
    pos = ManagedPosition(symbol="AAPL", side=ratchet.side, qty=10, strategy="manual",
                          entry_order_id="e1", stop_order_id="s1", current_stop=90.0,
                          ratchet=ratchet, status=PositionStatus.PENDING_ENTRY)
    broker._orders["e1"] = OrderView(
        id="e1", client_order_id="e1", symbol="AAPL", qty=10, side="buy",
        type="market", status="rejected", filled_qty=0.0)

    orch = _orch(broker, tmp_path, execute=True)
    orch.positions = {"AAPL": pos}
    orch._settle_pending()

    assert "AAPL" not in orch.positions
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(20)]
    assert "order_rejected" in events


def test_place_manual_surfaces_broker_rejection(tmp_path):
    broker = FakeBroker(reject_entries=True)
    svc = _service(broker, tmp_path)
    result = svc.place_manual("NVDA", 10)
    assert not result.ok and result.status == "rejected"
    assert "NVDA" not in svc.store.load()


# --- FakeBroker fill_price fix ---------------------------------------------

def test_fake_broker_fill_price_is_settable_and_distinct_from_stop():
    broker = FakeBroker(fill_price=150.0)
    broker.submit_market_entry("AAPL", 10, Side.LONG, client_order_id="c1")
    stop = broker.submit_stop("AAPL", 10, Side.LONG, stop_price=135.0, client_order_id="c1-sl")
    pos = broker.list_positions()[0]
    assert pos.avg_entry_price == pytest.approx(150.0)
    assert pos.avg_entry_price != pytest.approx(stop.stop_price)
