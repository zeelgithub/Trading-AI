"""Tests for the orchestrator: decision cycle, safety gates, exits, exceptions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.common.config import load_config
from src.common.logging import AuditLog
from src.common.models import Side
from src.core.orchestrator import Orchestrator
from src.core.state_store import HaltStore, StateStore
from src.execution.broker_alpaca import AccountView, OrderView, PositionView
from src.execution.order_manager import ManagedPosition, PositionStatus
from src.research.scoreboard import Scoreboard
from src.risk.ratchet_stop import PercentRatchet
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features, small_universe_config


def approve_frame(_symbol=None):
    """Trending long setup that passes regime + trend-following + risk.
    3 rows: pullback_lookback_bars defaults to 3; adx=35 clears the
    evidence-based trending_adx_min (32, see config/strategies.yaml)."""
    rows = [
        {"close": 102, "open": 102},
        {"close": 103, "open": 103},
        {"close": 105, "open": 104, "high": 105.5, "low": 104.0,
         "ema20": 104.5, "ema50": 100, "ema200": 95, "rsi": 55,
         "volume": 2e6, "vol_sma": 1e6, "adx": 35, "atr": 1.0},
    ]
    return make_features(rows)


def exit_frame(_symbol=None):
    """Price has lost the 50 EMA -> trend opposite-EMA exit; no new MR setup."""
    return make_features([{"close": 95, "open": 96, "ema50": 100, "ema200": 90,
                           "bb_lower": 93, "bb_upper": 110, "adx": 20, "atr": 1.0}])


class _CapturingNotifier:
    def __init__(self):
        self.alerts: list[tuple[str, str]] = []

    def alert(self, event: str, detail: str = "") -> None:
        self.alerts.append((event, detail))


def make_orch(broker, tmp_path, execute=False, halt_store=None, feature_provider=approve_frame, **kw):
    kw.setdefault("config", small_universe_config())
    return Orchestrator(
        broker=broker, feature_provider=feature_provider, execute=execute,
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=halt_store or HaltStore(tmp_path / "halt.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        scoreboard=Scoreboard(tmp_path / "scoreboard.json"), **kw,
    )


def test_shadow_cycle_decides_but_places_no_orders(tmp_path):
    broker = FakeBroker()
    report = make_orch(broker, tmp_path, execute=False).run_cycle()
    assert not report.halted and report.state == "idle"
    assert len(report.opened) == 3
    assert broker._orders == {}


def test_wired_bearish_scorer_blocks_a_long_entry(tmp_path):
    """Regression guard: a real scorer (e.g. NewsSentimentScorer) passed as
    Orchestrator(scorer=...) must actually reach SentimentGate and be able to
    block a trade -- this was unreachable with the prior scorer=None default
    (see src/strategy/news_sentiment_scorer.py)."""
    broker = FakeBroker()
    report = make_orch(broker, tmp_path, execute=False, scorer=lambda s: -1).run_cycle()
    assert not report.halted and report.state == "idle"
    assert report.opened == []


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
    acct = AccountView(equity=45000, last_equity=50000, buying_power=200000)
    broker = FakeBroker(account=acct, auto_fill=False)
    broker.seed_order(OrderView(id="x1", client_order_id="c", symbol="AAPL", qty=1,
                                side="sell", type="stop", status="accepted", stop_price=90.0))
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert report.halted and "kill switch" in report.halt_reason
    assert "x1" in broker.canceled


def test_flatten_records_error_when_listing_open_orders_fails(tmp_path):
    """Regression guard: _flatten() used to swallow a list_open_orders()
    failure completely silently -- during a kill-switch flatten, the one
    moment an operator most needs a trace that orders might still be
    resting at the broker."""
    class _ListFailsBroker(FakeBroker):
        """Reconciler.reconcile() also calls list_open_orders() earlier in the
        cycle -- only the SECOND call (inside _flatten, after the kill switch
        has already tripped) should fail, or the whole cycle halts on a
        generic reconcile error before ever reaching _flatten."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._list_open_orders_calls = 0

        def list_open_orders(self):
            self._list_open_orders_calls += 1
            if self._list_open_orders_calls > 1:
                raise RuntimeError("broker unreachable")
            return super().list_open_orders()

    acct = AccountView(equity=45000, last_equity=50000, buying_power=200000)
    broker = _ListFailsBroker(account=acct, auto_fill=False)
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert report.halted and "kill switch" in report.halt_reason
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(50)]
    assert "flatten_list_orders_error" in events


def test_flatten_records_error_when_cancel_fails(tmp_path):
    class _CancelFailsBroker(FakeBroker):
        def cancel_order(self, order_id):
            raise RuntimeError("cancel rejected")

    acct = AccountView(equity=45000, last_equity=50000, buying_power=200000)
    broker = _CancelFailsBroker(account=acct, auto_fill=False)
    broker.seed_order(OrderView(id="x1", client_order_id="c", symbol="AAPL", qty=1,
                                side="sell", type="stop", status="accepted", stop_price=90.0))
    report = make_orch(broker, tmp_path, execute=True).run_cycle()
    assert report.halted and "kill switch" in report.halt_reason
    events = [e["event"] for e in AuditLog(tmp_path / "audit.jsonl").tail(50)]
    assert "flatten_cancel_error" in events
    assert "x1" not in broker.canceled  # the cancel genuinely failed, not silently "succeeded"


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
        scoreboard=Scoreboard(tmp_path / "sb.json"),
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


def _seed_open_position_with_stop(tmp_path, execute: bool):
    """AAPL is genuinely OPEN at the broker with a resting stop -- reconcile
    passes clean, no auto-close, no opposite-EMA exit (approve_frame's
    close=105 > ema50=100)."""
    broker = FakeBroker(positions=[PositionView("AAPL", 50, Side.LONG, 100.0)], auto_fill=False)
    now = datetime.now(timezone.utc)
    broker.seed_order(OrderView(
        id="s1", client_order_id="c", symbol="AAPL", qty=50, side="sell",
        type="stop", status="held", stop_price=90.0, expires_at=now + timedelta(days=10),
    ))
    orch = Orchestrator(
        broker=broker, feature_provider=approve_frame, execute=execute,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
        scoreboard=Scoreboard(tmp_path / "sb.json"),
    )
    orch.positions = {
        "AAPL": ManagedPosition(
            symbol="AAPL", side=Side.LONG, qty=50, strategy="trend_following",
            entry_order_id="e1", stop_order_id="s1", current_stop=90.0,
            ratchet=PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0),
            status=PositionStatus.OPEN, filled_qty=50,
        )
    }
    return broker, orch


def test_refresh_stale_stop_resets_gtc_clock_when_near_expiry(tmp_path):
    """10 days from Alpaca's 90-day GTC aged-order cancel, inside the default
    15-day margin -- the cycle proactively replaces the stop at its OWN
    price, which resets the clock without changing protection."""
    broker, orch = _seed_open_position_with_stop(tmp_path, execute=True)

    report = orch.run_cycle()

    assert not report.halted
    assert report.stops_refreshed == ["AAPL"]
    assert broker.replaced and broker.replaced[-1] == ("s1", pytest.approx(90.0))
    assert orch.positions["AAPL"].current_stop == pytest.approx(90.0)  # unchanged level


def test_refresh_stale_stop_skipped_in_shadow_mode(tmp_path):
    """Shadow/propose mode places nothing -- a near-expiry stop is left for
    the next execute-mode cycle to catch, same posture as raise_stop."""
    broker, orch = _seed_open_position_with_stop(tmp_path, execute=False)

    report = orch.run_cycle()

    assert not report.halted
    assert report.stops_refreshed == []
    assert broker.replaced == []


def _seed_auto_closed_position(tmp_path, stop_order: OrderView, notifier):
    """A position the bot thinks is OPEN, but is gone from the broker with no
    open orders left for it -- reconciler flags this `auto_closed` (stop fired
    or an external/manual close)."""
    broker = FakeBroker(positions=[], auto_fill=False)
    broker.seed_order(stop_order)
    orch = Orchestrator(
        broker=broker, feature_provider=exit_frame, execute=True, notifier=notifier,
        state_store=StateStore(tmp_path / "p.json"),
        halt_store=HaltStore(tmp_path / "h.json"), audit=AuditLog(tmp_path / "a.jsonl"),
        scoreboard=Scoreboard(tmp_path / "sb.json"),
    )
    orch.positions = {
        "AAPL": ManagedPosition(
            symbol="AAPL", side=Side.LONG, qty=50, strategy="trend_following",
            entry_order_id="e1", stop_order_id=stop_order.id, current_stop=90.0,
            ratchet=PercentRatchet(entry=100.0, side=Side.LONG, initial_stop_pct=10.0),
            status=PositionStatus.OPEN, filled_qty=50,
        )
    }
    return orch


def test_auto_close_with_gap_slippage_fires_incident_alert(tmp_path):
    # Filled well below the 90.0 stop -- a real gap-down, not a clean stop-out.
    stop = OrderView(id="s1", client_order_id="c", symbol="AAPL", qty=50, side="sell",
                     type="stop", status="filled", stop_price=90.0, filled_avg_price=85.0)
    notifier = _CapturingNotifier()
    orch = _seed_auto_closed_position(tmp_path, stop, notifier)

    report = orch.run_cycle()

    assert not report.halted
    assert orch.positions["AAPL"].status == PositionStatus.CLOSED
    events = [e for e, _ in notifier.alerts]
    assert "incident" in events
    assert "fill" not in events   # the incident alert replaces the routine one, doesn't duplicate
    detail = dict(notifier.alerts)["incident"]
    assert "AAPL" in detail and "5.6%" in detail
    sb = Scoreboard(tmp_path / "sb.json").load()
    # (85 - 100) * 50 = -750, using the REAL fill price, not the 90.0 stop level.
    assert sb["trend_following"].live_total_pnl == pytest.approx(-750.0)


def test_auto_close_without_real_fill_price_falls_back_quietly(tmp_path):
    # Canceled, not filled (e.g. a manual close at the broker cancels the stop
    # leg instead of filling it) -- no fill price to compare, so no incident
    # claim should be made; this must not regress to the old routine alert.
    stop = OrderView(id="s1", client_order_id="c", symbol="AAPL", qty=50, side="sell",
                     type="stop", status="canceled", stop_price=90.0)
    notifier = _CapturingNotifier()
    orch = _seed_auto_closed_position(tmp_path, stop, notifier)

    report = orch.run_cycle()

    assert not report.halted
    events = [e for e, _ in notifier.alerts]
    assert "incident" not in events
    assert "fill" in events
    sb = Scoreboard(tmp_path / "sb.json").load()
    # Falls back to the stop level: (90 - 100) * 50 = -500.
    assert sb["trend_following"].live_total_pnl == pytest.approx(-500.0)
