"""Unit tests for src/discovery/sources/_util.py -- the shared helpers
extracted from news/fundamentals/social/volatility/technical to remove the
duplicated concurrent-fetch, percentile-rank, text-clip, and safe-numeric-
extraction patterns each source used to hand-roll independently."""

from __future__ import annotations

import threading

import pandas as pd
import pytest

from src.discovery.sources._util import (
    clip_text,
    fetch_concurrent,
    percentile_rank,
    safe_call,
    safe_num,
)


def test_safe_call_passes_through_result_and_fails_soft_on_exception():
    assert safe_call(lambda a, b, c=None: (a, b, c), 1, 2, c=3) == (1, 2, 3)

    def boom():
        raise RuntimeError("network blew up")
    assert safe_call(boom) is None


def test_fetch_concurrent_collects_all_non_none_results():
    def fetch_one(x):
        return x * 2 if x % 2 == 0 else None

    out = fetch_concurrent(range(10), fetch_one, max_workers=4)
    assert sorted(out) == [0, 4, 8, 12, 16]


def test_fetch_concurrent_empty_items():
    assert fetch_concurrent([], lambda x: x, max_workers=4) == []


def test_fetch_concurrent_propagates_exception_after_processing_the_rest():
    """One item's exception must not stop the others from being processed,
    but the caller still sees it -- matches the old ThreadPoolExecutor+
    as_completed behavior. fetch_one is only NOT fail-soft here in this
    adversarial test; every real caller already wraps its own call in
    safe_call."""
    processed: list[int] = []
    lock = threading.Lock()

    def fetch_one(x):
        if x == 0:
            raise ValueError("boom")
        with lock:
            processed.append(x)
        return x

    with pytest.raises(ValueError):
        fetch_concurrent(range(5), fetch_one, max_workers=5)

    assert sorted(processed) == [1, 2, 3, 4]


def test_fetch_concurrent_uses_at_most_min_max_workers_and_item_count_threads():
    seen_thread_ids = set()
    lock = threading.Lock()

    def fetch_one(x):
        with lock:
            seen_thread_ids.add(threading.get_ident())
        return x

    fetch_concurrent(range(20), fetch_one, max_workers=3)
    assert len(seen_thread_ids) <= 3

    seen_thread_ids.clear()
    fetch_concurrent(range(2), fetch_one, max_workers=16)
    assert len(seen_thread_ids) <= 2


def test_fetch_concurrent_does_not_block_process_exit_when_one_item_hangs():
    """Regression guard: fetch_concurrent used to be a ThreadPoolExecutor,
    whose worker threads are non-daemon -- concurrent.futures.thread's
    process-wide atexit hook joins every thread from every executor it ever
    created, even ones spawned deep inside another daemon thread (this runs
    inside DiscoveryPipeline's own per-source daemon thread in real use, see
    pipeline.py's _gather()). Confirmed live: nesting a ThreadPoolExecutor
    inside an already-daemon thread still blocked process exit for as long
    as a hung worker inside it kept running. Exercise the same nesting for
    real, as a subprocess -- the whole process must exit promptly even
    though fetch_concurrent itself is still waiting on other items when the
    outer daemon thread is abandoned."""
    import subprocess
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script = """
import threading, time
from src.discovery.sources._util import fetch_concurrent

def fetch_one(x):
    if x == 0:
        time.sleep(4)  # simulates one hung network call among many
    return x

def outer_daemon_work():
    fetch_concurrent(range(4), fetch_one, max_workers=4)

t = threading.Thread(target=outer_daemon_work, daemon=True)
t.start()
time.sleep(0.2)
print("outer daemon thread abandoned -- main returning now")
"""
    start = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=15, cwd=repo_root)
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    assert "outer daemon thread abandoned" in result.stdout
    assert elapsed < 2.0, f"process took {elapsed:.2f}s to exit -- nested hang blocked shutdown"


def test_percentile_rank_orders_and_handles_empty_and_ties():
    assert percentile_rank({}) == {}

    ranked = percentile_rank({"A": 1.0, "B": 3.0, "C": 2.0})
    assert ranked["A"] < ranked["C"] < ranked["B"]
    assert ranked["B"] == 1.0  # highest value -> top percentile

    tied = percentile_rank({"A": 5.0, "B": 5.0})
    assert tied["A"] == tied["B"]


def test_clip_text_strips_and_truncates_with_ellipsis():
    assert clip_text("short") == "short"
    assert clip_text("  padded  ") == "padded"

    clipped = clip_text("x" * 100, n=60)
    assert len(clipped) == 60
    assert clipped.endswith("…")


def test_safe_num_extracts_missing_nan_and_valid():
    assert safe_num(pd.Series({"a": 1.0}), "missing") is None
    assert safe_num(pd.Series({"a": float("nan")}), "a") is None
    assert safe_num(pd.Series({"a": 5}), "a") == 5.0
