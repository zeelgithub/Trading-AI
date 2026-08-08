"""Tests for the robustness/scalability batch: watchdog health evaluation,
in-progress bar dropping, transient-error classification + retry (disconnect
self-heal path), incremental ingest, and the strategy registry."""

from __future__ import annotations

import time

import pandas as pd
import pytest

from src.common.config import load_config
from src.common.errors import is_transient_error, retry_transient
from src.common.logging import AuditLog
from src.core.orchestrator import Orchestrator
from src.core.state_store import HaltStore, StateStore
from src.core.watchdog import evaluate_health
from src.data import store
from src.data.ingest import drop_incomplete_bar, ingest_symbol
from src.research.scoreboard import Scoreboard
from src.strategy.base import Strategy
from src.strategy.registry import REGISTRY, build_strategies, register
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features


# --- watchdog ----------------------------------------------------------------

def _et(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York")


def _utc_iso(et: str) -> str:
    return _et(et).tz_convert("UTC").isoformat()


HEALTHY = dict(
    halt=None,
    last_cycle_ts=_utc_iso("2026-08-07 15:50"),
    heartbeat_ts=_utc_iso("2026-08-07 16:29"),
    stale_symbols=[],
)


def test_watchdog_healthy_is_quiet():
    issues = evaluate_health(now_et=_et("2026-08-07 16:31"), **HEALTHY)  # Friday
    assert issues == []


def test_watchdog_detects_missed_cycle():
    facts = {**HEALTHY, "last_cycle_ts": _utc_iso("2026-08-06 15:50")}  # yesterday's
    issues = evaluate_health(now_et=_et("2026-08-07 16:31"), **facts)
    assert [i.kind for i in issues] == ["missed_cycle"]


def test_watchdog_no_missed_cycle_before_grace_or_on_non_trading_day():
    facts = {**HEALTHY, "last_cycle_ts": None}
    assert evaluate_health(now_et=_et("2026-08-07 16:29"), **facts) == []  # within grace
    non_trading = {**facts, "heartbeat_ts": _utc_iso("2026-08-08 12:00"), "is_trading_day": False}
    assert evaluate_health(now_et=_et("2026-08-08 12:01"), **non_trading) == []  # weekend/holiday


def test_watchdog_detects_dead_listener():
    stale_hb = {**HEALTHY, "heartbeat_ts": _utc_iso("2026-08-07 10:00")}
    issues = evaluate_health(now_et=_et("2026-08-07 16:31"), **stale_hb)
    assert [i.kind for i in issues] == ["listener_down"]
    missing = {**HEALTHY, "heartbeat_ts": None}
    issues = evaluate_health(now_et=_et("2026-08-07 16:31"), **missing)
    assert [i.kind for i in issues] == ["listener_down"]


def test_watchdog_reports_halt_and_stale_data():
    facts = {**HEALTHY, "halt": {"reason": "kill switch", "class": "kill_switch"},
             "stale_symbols": ["AAPL"]}
    kinds = {i.kind for i in evaluate_health(now_et=_et("2026-08-07 16:31"), **facts)}
    assert kinds == {"halted", "stale_data"}


# --- in-progress bar ---------------------------------------------------------

def _bars(end_utc: str, n: int = 3) -> pd.DataFrame:
    df = make_features([{"close": 100.0} for _ in range(n)])
    df.index = pd.date_range(end=pd.Timestamp(end_utc, tz="UTC"), periods=n, freq="D")
    return df


def test_todays_bar_dropped_during_session():
    # Bar stamped midnight ET today (04:00 UTC); clock says 15:45 ET same day.
    bars = _bars("2026-08-07 04:00")
    out = drop_incomplete_bar(bars, now=_et("2026-08-07 15:45"))
    assert len(out) == len(bars) - 1


def test_todays_bar_kept_after_close_and_final_bars_untouched():
    bars = _bars("2026-08-07 04:00")
    assert len(drop_incomplete_bar(bars, now=_et("2026-08-07 16:05"))) == len(bars)
    assert len(drop_incomplete_bar(bars, now=_et("2026-08-08 10:00"))) == len(bars)


def test_non_datetime_index_left_alone():
    df = make_features([{"close": 1.0}])
    assert drop_incomplete_bar(df, now=_et("2026-08-07 15:45")) is df


# --- transient errors + disconnect halt --------------------------------------

def test_transient_classification():
    assert is_transient_error(ConnectionError("reset"))
    assert is_transient_error(TimeoutError())
    assert not is_transient_error(ValueError("bad config"))
    api_err = type("APIError", (Exception,), {})()
    api_err.status_code = 503
    assert is_transient_error(api_err)
    wrapped = RuntimeError("cycle failed")
    wrapped.__cause__ = ConnectionError("underneath")
    assert is_transient_error(wrapped)


def test_retry_transient_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return "ok"

    assert retry_transient(flaky, attempts=3, sleep=lambda s: None) == "ok"
    assert calls["n"] == 3


def test_retry_transient_never_retries_logic_errors():
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise ValueError("bug")

    with pytest.raises(ValueError):
        retry_transient(broken, attempts=3, sleep=lambda s: None)
    assert calls["n"] == 1


class DisconnectedBroker(FakeBroker):
    def get_account(self):
        raise ConnectionError("api unreachable")


def test_connection_failure_halts_as_disconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip retry backoff
    orch = Orchestrator(
        broker=DisconnectedBroker(), feature_provider=lambda s: make_features([]),
        state_store=StateStore(tmp_path / "positions.json"),
        halt_store=HaltStore(tmp_path / "halt.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        scoreboard=Scoreboard(tmp_path / "scoreboard.json"),
    )
    report = orch.run_cycle()
    assert report.halted
    assert HaltStore(tmp_path / "halt.json").halt_info()["class"] == "disconnect"


# --- incremental ingest ------------------------------------------------------

class RecordingProvider:
    """Fake data provider that records the `start` it was called with."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self.bars = bars
        self.starts: list = []

    def get_daily_bars(self, symbol, lookback_days=400, start=None):
        self.starts.append(start)
        return self.bars


def test_ingest_is_incremental_after_first_fetch(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    bars = _bars("2026-08-05 04:00", n=10)[["open", "high", "low", "close", "volume"]]
    provider = RecordingProvider(bars)

    ingest_symbol(conn, "AAPL", provider=provider)          # cold: full window
    assert provider.starts == [None]

    ingest_symbol(conn, "AAPL", provider=provider)          # warm: overlap only
    assert provider.starts[1] is not None
    newest = bars.index[-1]
    assert pd.Timestamp(provider.starts[1]) == newest - pd.Timedelta(days=7)


def test_ingest_full_fetch_for_provider_without_start_support(tmp_path):
    class LegacyProvider:
        def get_daily_bars(self, symbol, lookback_days=400):
            return _bars("2026-08-05 04:00", n=5)[["open", "high", "low", "close", "volume"]]

    conn = store.connect(tmp_path / "bars.db")
    ingest_symbol(conn, "AAPL", provider=LegacyProvider())
    report = ingest_symbol(conn, "AAPL", provider=LegacyProvider())  # falls back, no crash
    assert report.rows == 5


# --- strategy registry -------------------------------------------------------

def test_registry_builds_configured_strategies():
    strategies = build_strategies(load_config())
    assert set(strategies) == {"trend_following", "mean_reversion", "breakout"}
    assert all(s.name == name for name, s in strategies.items())


def test_register_decorator_adds_new_strategy():
    @register
    class Dummy(Strategy):
        name = "dummy_strategy"

        def generate(self, symbol, features):
            return None

    try:
        assert REGISTRY["dummy_strategy"] is Dummy
    finally:
        del REGISTRY["dummy_strategy"]  # keep the global registry clean
