"""Pipeline: group -> score -> drop held/low-score -> rank -> risk-gate top N
-> Proposal. Uses a fake source + the real Scorer/RiskManager (offline)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from src.common.config import load_config
from src.discovery.candidate import SignalContribution
from src.discovery.pipeline import Account, DiscoveryPipeline
from src.discovery.scorer import Scorer
from src.execution.order_manager import PositionStatus
from src.risk.risk_manager import RiskManager

ACCOUNT = Account(equity=100_000.0, last_equity=100_000.0, buying_power=200_000.0)


class FakeSource:
    name = "fake"

    def __init__(self, contributions):
        self._contributions = contributions

    def gather(self):
        return list(self._contributions)


def _tech(symbol, score, *, entry, stop, strategy="trend_following", side="long"):
    return SignalContribution(symbol, "technical", score, f"{strategy} setup",
                              meta={"strategy": strategy, "entry_price": entry,
                                    "stop_loss": stop, "atr": None, "side": side})


def _congress(symbol, score):
    return SignalContribution(symbol, "congress", score, "Congress bought")


def _held_pos():
    return SimpleNamespace(status=PositionStatus.OPEN, filled_qty=10, qty=10,
                           ratchet=SimpleNamespace(entry=50.0, stop=45.0))


def _pipeline(contributions, *, top_n=2, min_score=25.0, min_price=5.0, price_fn=None):
    return DiscoveryPipeline(
        sources=[FakeSource(contributions)],
        scorer=Scorer(active_sources=frozenset({"congress", "technical"})),
        risk=RiskManager(load_config()),
        config=load_config(),
        price_fn=price_fn or (lambda s: 100.0),  # for congress-only candidates
        top_n=top_n, min_score=min_score, min_price=min_price,
    )


def _mixed():
    return [
        _congress("BBB", 0.8), _tech("BBB", 0.8, entry=200.0, stop=180.0),  # 80
        _congress("AAA", 0.8),                                              # 40, congress-only
        _congress("CCC", 0.1),                                              # 5  -> below floor
    ]


def test_ranks_scores_and_proposes_top_n():
    report = _pipeline(_mixed()).run(ACCOUNT, {})
    assert report.screened == 3
    assert [c.symbol for c in report.candidates] == ["BBB", "AAA"]   # CCC dropped, ranked desc
    assert [p.symbol for p in report.proposals] == ["BBB", "AAA"]


def test_proposal_levels_and_strategy():
    report = _pipeline(_mixed()).run(ACCOUNT, {})
    bbb = next(p for p in report.proposals if p.symbol == "BBB")
    aaa = next(p for p in report.proposals if p.symbol == "AAA")
    assert bbb.strategy == "trend_following"
    assert bbb.intent["entry_price"] == 200.0 and bbb.intent["stop_loss"] == 180.0
    # Congress-only: priced live, default 10% stop, congress_copy strategy.
    assert aaa.strategy == "congress_copy"
    assert aaa.intent["entry_price"] == 100.0 and aaa.intent["stop_loss"] == 90.0
    assert bbb.approved_qty > 0 and aaa.approved_qty > 0


def test_held_symbols_excluded():
    report = _pipeline(_mixed()).run(ACCOUNT, {"AAA": _held_pos()})
    assert "AAA" not in [c.symbol for c in report.candidates]
    assert "AAA" not in [p.symbol for p in report.proposals]


def test_exclude_set_blocks_pending():
    report = _pipeline(_mixed()).run(ACCOUNT, {}, exclude={"bbb"})
    assert "BBB" not in [p.symbol for p in report.proposals]


def test_top_n_caps_proposals():
    report = _pipeline(_mixed(), top_n=1).run(ACCOUNT, {})
    assert len(report.proposals) == 1 and report.proposals[0].symbol == "BBB"
    assert len(report.candidates) == 2   # both still scored, only one proposed


def test_invalid_stop_is_skipped():
    # entry == stop -> not a valid long; must be skipped, not proposed.
    report = _pipeline([_tech("DDD", 0.8, entry=100.0, stop=100.0)]).run(ACCOUNT, {})
    assert report.proposals == []
    assert any(sym == "DDD" for sym, _ in report.skipped)


def test_short_technical_setup_produces_a_short_proposal_not_dropped():
    """Regression guard: _levels()/_size_and_propose() used to hardcode
    Side.LONG/Action.BUY for every technical contribution, and the "invalid
    stop" check (stop >= entry) was written only for the long convention --
    a genuine short setup (stop ABOVE entry, e.g. entry=100/stop=110) was
    silently dropped as "invalid stop" instead of being proposed as a short.
    shorts_allowed() is already enforced inside the strategy that generated
    this contribution (see TechnicalSource), so this isn't a new bypass --
    just correctly passing through an already-validated short."""
    report = _pipeline(
        [_tech("EEE", 0.8, entry=100.0, stop=110.0, side="short")]
    ).run(ACCOUNT, {})
    assert report.proposals != []
    prop = next(p for p in report.proposals if p.symbol == "EEE")
    assert prop.intent["signal"] == "SHORT"
    assert prop.intent["stop_loss"] == 110.0


def test_short_technical_setup_with_stop_on_wrong_side_is_still_skipped():
    """A short's stop must be ABOVE entry -- stop below entry for a short is
    genuinely invalid (not just "invalid for a long"), and must still be
    skipped, not silently accepted now that shorts are supported."""
    report = _pipeline(
        [_tech("FFF", 0.8, entry=100.0, stop=90.0, side="short")]
    ).run(ACCOUNT, {})
    assert report.proposals == []
    assert any(sym == "FFF" for sym, _ in report.skipped)


def test_penny_stock_priced_candidate_is_skipped_below_default_floor():
    report = _pipeline([_tech("PENNY", 0.8, entry=2.50, stop=2.25)]).run(ACCOUNT, {})
    assert report.proposals == []
    reason = next(r for sym, r in report.skipped if sym == "PENNY")
    assert "min price floor" in reason


def test_candidate_right_at_the_floor_is_not_skipped():
    report = _pipeline([_tech("ATFLOOR", 0.8, entry=5.00, stop=4.50)], min_price=5.0).run(ACCOUNT, {})
    assert [p.symbol for p in report.proposals] == ["ATFLOOR"]


def test_congress_only_candidate_also_respects_the_floor():
    """The floor is enforced centrally in _size_and_propose(), not per-source
    -- a congress-only idea priced live below the floor must be skipped too,
    not just a technical one."""
    report = _pipeline([_congress("CHEAP", 0.8)], price_fn=lambda s: 3.0).run(ACCOUNT, {})
    assert report.proposals == []
    reason = next(r for sym, r in report.skipped if sym == "CHEAP")
    assert "min price floor" in reason


def test_min_price_is_configurable():
    # A candidate that clears the default $5 floor but not a stricter $10 one.
    report = _pipeline([_tech("MIDPRICE", 0.8, entry=7.0, stop=6.0)], min_price=10.0).run(ACCOUNT, {})
    assert report.proposals == []
    reason = next(r for sym, r in report.skipped if sym == "MIDPRICE")
    assert "min price floor ($10)" in reason


# --- concurrent _gather() (Phase 5: sources run on a thread pool now) -------

class SlowSource:
    """Blocks past the pipeline's timeout -- simulates a throttled/hung
    source (the documented `fundamentals` yfinance-throttling scenario)."""
    name = "slow"

    def __init__(self, delay_seconds):
        self._delay = delay_seconds

    def gather(self):
        time.sleep(self._delay)
        return [_congress("NEVERARRIVES", 0.9)]


class FastSource:
    name = "fast"

    def __init__(self, contributions):
        self._contributions = contributions

    def gather(self):
        return list(self._contributions)


def test_gather_runs_sources_concurrently_not_sequentially():
    """Two sources that each sleep 0.3s must together take well under the
    0.6s a sequential run would need."""
    class SleepySource:
        def __init__(self, name, delay):
            self.name = name
            self._delay = delay

        def gather(self):
            time.sleep(self._delay)
            return []

    pipeline = DiscoveryPipeline(
        sources=[SleepySource("a", 0.3), SleepySource("b", 0.3)],
        scorer=Scorer(active_sources=frozenset({"congress", "technical"})),
        risk=RiskManager(load_config()), config=load_config(),
        price_fn=lambda s: 100.0, source_timeout_seconds=5.0,
    )
    start = time.monotonic()
    pipeline.run(ACCOUNT, {})
    elapsed = time.monotonic() - start
    assert elapsed < 0.55


def test_slow_source_times_out_without_blocking_fast_sources():
    pipeline = DiscoveryPipeline(
        sources=[SlowSource(delay_seconds=2.0), FastSource([_congress("QUICK", 0.8)])],
        scorer=Scorer(active_sources=frozenset({"congress", "technical"})),
        risk=RiskManager(load_config()), config=load_config(),
        price_fn=lambda s: 100.0, source_timeout_seconds=0.2,
    )
    start = time.monotonic()
    report = pipeline.run(ACCOUNT, {})
    elapsed = time.monotonic() - start
    assert elapsed < 1.0                                        # didn't wait for the 2s sleep
    assert "QUICK" in [c.symbol for c in report.candidates]      # fast source's result still counts
    assert "NEVERARRIVES" not in [c.symbol for c in report.candidates]


def test_one_source_exception_does_not_sink_the_others():
    class BoomSource:
        name = "boom"

        def gather(self):
            raise RuntimeError("source blew up")

    pipeline = DiscoveryPipeline(
        sources=[BoomSource(), FastSource([_congress("SURVIVOR", 0.8)])],
        scorer=Scorer(active_sources=frozenset({"congress", "technical"})),
        risk=RiskManager(load_config()), config=load_config(),
        price_fn=lambda s: 100.0,
    )
    report = pipeline.run(ACCOUNT, {})
    assert "SURVIVOR" in [c.symbol for c in report.candidates]


def test_timed_out_source_does_not_block_process_exit():
    """Regression guard: an earlier version of _gather() used
    ThreadPoolExecutor, whose worker threads are non-daemon --
    concurrent.futures.thread registers a process-wide atexit hook that
    joins EVERY thread from EVERY executor, even ones already
    shutdown(wait=False)'d, so a single hung source blocked the whole
    Python process at exit for as long as that source kept running (this
    is called from the always-on Telegram listener for `/ideas`, not just
    a one-shot script, so that's a real hang, not just test-suite noise).
    Confirmed live: a 4s-hung source made a `python -c` subprocess take 4s
    to exit even though its own logic finished in ~0.2s. Run the same
    scenario as a real subprocess -- the whole process must exit promptly,
    not just DiscoveryPipeline.run()."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script = f"""
import time
from src.common.config import load_config
from src.discovery.pipeline import Account, DiscoveryPipeline
from src.discovery.scorer import Scorer
from src.risk.risk_manager import RiskManager

class HungSource:
    name = "hung"
    def gather(self):
        time.sleep({4.0})
        return []

pipeline = DiscoveryPipeline(
    sources=[HungSource()],
    scorer=Scorer(active_sources=frozenset({{"congress"}})),
    risk=RiskManager(load_config()), config=load_config(),
    price_fn=lambda s: 100.0, source_timeout_seconds=0.2,
)
pipeline.run(Account(equity=1.0, last_equity=1.0, buying_power=1.0), {{}})
print("script logic finished")
"""
    start = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=15, cwd=repo_root)
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    assert "script logic finished" in result.stdout
    assert elapsed < 2.0, f"process took {elapsed:.2f}s to exit -- hung source blocked shutdown"


def test_cached_feature_provider_is_safe_from_multiple_threads(tmp_path, monkeypatch):
    """The shared sqlite connection (src/discovery/universe.cached_feature_
    provider) must not raise sqlite3.ProgrammingError when the returned
    closure is called from a thread other than the one that built it -- the
    scenario Phase 5's concurrent _gather() actually creates (technical and
    volatility both call into this same closure, now possibly from different
    worker threads). sqlite3 connections are check_same_thread=True by
    default (src/data/store.py's connect()), so this fails loudly, not
    subtly, if the thread-local fix in cached_feature_provider() regresses."""
    import pandas as pd

    import src.data.ingest as ingest_mod
    from src.data import store
    from src.discovery.universe import cached_feature_provider
    from tests.unit.synth import make_features

    db_path = tmp_path / "bars.db"
    real_connect = store.connect
    # cached_feature_provider() always calls store.connect() with no args
    # (module default DB path, bound at import time -- monkeypatching
    # store.DEFAULT_DB after the fact wouldn't redirect it); patch the
    # function itself so every call -- the initial one AND any thread-local
    # fallback -- opens a REAL sqlite3.Connection (default check_same_thread
    # semantics preserved) against this test's tmp file instead.
    monkeypatch.setattr(store, "connect", lambda *a, **kw: real_connect(db_path))

    now = pd.Timestamp("2026-08-25 12:00", tz="America/New_York")
    monkeypatch.setattr(ingest_mod.pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))

    bars = make_features([{"close": 100.0} for _ in range(30)])
    bars.index = pd.date_range(end=now.tz_convert("UTC"), periods=30, freq="D", tz="UTC")
    store.upsert_bars(real_connect(db_path), "AAPL", bars)

    config = load_config()
    # universe=None: skip the batch pre-warm path (its own network fallback
    # isn't what this test is about) -- ingest_symbol()'s own same-day
    # freshness short-circuit (patched `now` above) is enough to guarantee
    # no network call from any thread either way.
    provider = cached_feature_provider(config, universe=None)

    errors: list[BaseException] = []
    results: list = []
    lock = threading.Lock()

    def call_from_thread():
        try:
            feats = provider("AAPL")
        except BaseException as exc:  # noqa: BLE001 - want to see ANY failure, not just Exception
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(feats)

    threads = [threading.Thread(target=call_from_thread) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"cross-thread feature_provider call(s) raised: {errors}"
    assert len(results) == 4
    assert all(not f.empty for f in results)
