"""Tests for the orchestrator: decision cycle, safety gates, exits, exceptions."""

from __future__ import annotations

from dataclasses import replace

from src.common.config import load_config
from src.common.logging import AuditLog
from src.common.models import Side
from src.core.orchestrator import Orchestrator
from src.core.state_store import HaltStore, StateStore
from src.execution.broker_alpaca import AccountView, OrderView, PositionView
from src.execution.order_manager import ManagedPosition, PositionStatus
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features


def approve_frame(_symbol=None):
    """Trending long setup that passes regime + trend-following + risk."""
    rows = [
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 30, "atr": 1.0},
    ]
    return make_features(rows)


def exit_frame(_symbol=None):
    """Price has lost the 50 EMA -> trend opposite-EMA exit; no new MR setup."""
    return make_features([{"close": 95, "open": 96, "ema50": 100, "ema200": 90,
                           "bb_lower": 93, "bb_upper": 110, "adx": 20, "atr": 1.0}])


def make_orch(broker, tmp_path, execute=False, halt_store=None, **kw):
    return Orchestrator(
        broker=broker, feature_provider=approve_frame, execute=execute,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=halt_store or HaltStore(tmp_path / "halt.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"), **kw,
    )


def test_shadow_cycle_decides_but_places_no_orders(tmp_path):
    broker = FakeBroker()
    report = make_orch(broker, tmp_path, execute=False).run_cycle()
    assert not report.halted and report.state == "idle"
    assert len(report.opened) == 3
    assert broker._orders == {}


def test_execute_cycle_opens_protected_positions_and_persists(tmp_path):
    broker = FakeBroker(auto_fill=True)
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert not report.halted
    assert len(report.opened) == 3
    markets = [o for o in broker._orders.values() if o.type == "market"]
    stops = [o for o in broker._orders.values() if o.type == "stop"]
    assert len(markets) == 3 and len(stops) == 3       # every entry has a stop
    reloaded = StateStore(tmp_path / "positions.json").load()
    assert len(reloaded) == 3
    assert all(p.status == PositionStatus.OPEN for p in reloaded.values())


def test_halted_bot_is_a_noop(tmp_path):
    broker = FakeBroker()
    orch = make_orch(broker, tmp_path, execute=True)
    orch.state.halt("manual")
    report = orch.run_cycle()
    assert report.halted and broker._orders == {}


def test_reconcile_mismatch_halts(tmp_path):
    broker = FakeBroker(positions=[PositionView("TSLA", 10, Side.LONG, 200.0)], auto_fill=False)
    orch = make_orch(broker, tmp_path, execute=True)
    report = orch.run_cycle()
    assert report.halted and "reconcile" in report.halt_reason
    assert orch.state.is_halted


def test_kill_switch_halts_and_flattens(tmp_path):
    acct = AccountView(equity=45000, last_equity=50000, buying_power=200000,
                       daytrade_count=0, pattern_day_trader=False)
    broker = FakeBroker(account=acct, auto_fill=False)
    broker.seed_order(OrderView(id="x1", client_order_id="c", symbol="AAPL", qty=1,
                                side="sell", type="stop", status="accepted", stop_price=90.0))
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert report.halted and "kill switch" in report.halt_reason
    assert "x1" in broker.canceled


def test_live_mode_without_optin_halts(tmp_path):
    base = load_config()
    live_cfg = replace(base, settings={**base.settings, "mode": "live"})
    report = make_orch(FakeBroker(), tmp_path, execute=True, config=live_cfg).run_cycle()
    assert report.halted and "live execution" in report.halt_reason


def test_cycle_exception_halts_not_crashes(tmp_path):
    class BoomBroker(FakeBroker):
        def get_account(self):
            raise RuntimeError("api down")

    orch = make_orch(BoomBroker(), tmp_path, execute=False)
    report = orch.run_cycle()
    assert report.halted and "cycle exception" in report.halt_reason
    assert orch.state.is_halted


def test_per_symbol_error_is_isolated(tmp_path):
    def provider(symbol):
        if symbol == "AAPL":
            raise RuntimeError("bad data")
        return approve_frame()

    orch = Orchestrator(
        broker=FakeBroker(), feature_provider=provider, execute=False,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
    )
    report = orch.run_cycle()
    assert not report.halted
    assert any(s[0] == "AAPL" for s in report.skipped)
    assert "MSFT" in report.opened and "SPY" in report.opened


def test_halt_persists_across_cold_runs_until_reset(tmp_path):
    hs = HaltStore(tmp_path / "halt.json")
    # Run 1 halts on an unknown broker position.
    bad = FakeBroker(positions=[PositionView("TSLA", 10, Side.LONG, 200.0)], auto_fill=False)
    assert make_orch(bad, tmp_path, execute=True, halt_store=hs).run_cycle().halted

    # Run 2 is a FRESH process with a clean broker -> must STILL be halted.
    o2 = make_orch(FakeBroker(), tmp_path, execute=True, halt_store=hs)
    assert o2.run_cycle().halted
    assert o2.broker._orders == {}            # no self-resume, no trading

    # Manual reset clears it; the next run trades again.
    o2.reset()
    o3 = make_orch(FakeBroker(), tmp_path, execute=True, halt_store=hs)
    assert not o3.run_cycle().halted


def test_market_closed_places_no_entries(tmp_path):
    broker = FakeBroker(market_open=False)
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert not report.halted
    assert report.opened == []
    assert broker._orders == {}


def test_opposite_ema_exit_closes_position(tmp_path):
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    broker.seed_order(OrderView(id="s1", client_order_id="c", symbol="AAPL", qty=50,
                                side="sell", type="stop", status="held", stop_price=90.0))
    orch = Orchestrator(
        broker=broker, feature_provider=exit_frame, execute=True,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
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
    assert not report.halted
    assert ("AAPL", "opposite_ema_break") in report.exited
    assert broker.closed == ["AAPL"]
