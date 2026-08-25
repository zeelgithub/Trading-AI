"""
Shared source helpers -- discovery layer.

Every source in this package independently re-implemented the same handful of
small patterns: a threaded per-item fetch, percentile ranking relative to
today's screen, one-line text clipping for the digest, safe numeric
extraction from a features row, and "one bad symbol/subreddit/request must
never sink the whole gather()." Factored here so a new source reuses these
instead of copy-pasting them (that copy-paste is exactly how `_clip`/`_num`
ended up duplicated verbatim across news.py/social.py and
volatility.py/technical.py).

Boundary: pure helpers, no IO of their own, no orders.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable
from typing import TypeVar

import pandas as pd

T = TypeVar("T")
R = TypeVar("R")


def safe_call(fn: Callable[..., R], *args, **kwargs) -> R | None:
    """Call fn(*args, **kwargs); fail-soft to None on any exception. One bad
    symbol/subreddit/request must never sink the whole gather()."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def fetch_concurrent(
    items: Iterable[T], fetch_one: Callable[[T], R | None], *, max_workers: int = 16,
) -> list[R]:
    """Run fetch_one(item) across `items` on a bounded pool of daemon worker
    threads (min(max_workers, len(items)) of them, pulling from a shared work
    queue -- same pooling ThreadPoolExecutor would give), collecting every
    non-None result. `fetch_one` is expected to already be fail-soft (e.g.
    wrap its own network call in `safe_call`) -- an exception raised here
    still propagates to the caller, rather than being silently swallowed a
    second time.

    Deliberately NOT ThreadPoolExecutor: this runs inside
    DiscoveryPipeline's own per-source daemon thread (pipeline.py's
    _gather()), and ThreadPoolExecutor's worker threads are non-daemon --
    concurrent.futures.thread registers a process-wide atexit hook that
    joins EVERY thread from EVERY executor it ever created, including ones
    spawned deep inside another daemon thread. Confirmed live: a
    ThreadPoolExecutor here, given one hung network call, blocked the whole
    process at exit for as long as that call hung, regardless of the OUTER
    thread being a daemon. Plain daemon threads are simply abandoned at
    interpreter shutdown instead."""
    items = list(items)
    if not items:
        return []

    work: queue.Queue = queue.Queue()
    for item in items:
        work.put(item)
    results: queue.Queue = queue.Queue()

    def _worker() -> None:
        while True:
            try:
                item = work.get_nowait()
            except queue.Empty:
                return
            try:
                results.put((fetch_one(item), None))
            except BaseException as exc:  # noqa: BLE001 - must always post a
                # result, even for something broader than Exception, or the
                # collector loop below (expects exactly len(items) results)
                # blocks forever on the one that never arrives. Matches
                # ThreadPoolExecutor's own _WorkItem.run(), which catches
                # BaseException for the same reason.
                results.put((None, exc))

    for _ in range(min(max_workers, len(items))):
        threading.Thread(target=_worker, daemon=True).start()

    out: list[R] = []
    first_exc: BaseException | None = None
    for _ in items:
        result, exc = results.get()
        if exc is not None:
            first_exc = first_exc or exc
            continue
        if result is not None:
            out.append(result)
    if first_exc is not None:
        raise first_exc
    return out


def percentile_rank(values: dict[str, float]) -> dict[str, float]:
    """Percentile-rank (0-1, ties share the same rank) each key's value
    relative to every other key in `values` -- "how volatile/how mentioned is
    this symbol relative to everything else screened this run," not against a
    hardcoded absolute threshold that would go stale as the market's regime
    shifts."""
    if not values:
        return {}
    ranked = pd.Series(values).rank(pct=True)
    return {k: float(ranked[k]) for k in values}


def clip_text(text: str, n: int = 60) -> str:
    """Truncate `text` to at most `n` characters for a one-line digest reason."""
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def safe_num(row: pd.Series, col: str) -> float | None:
    """Extract row[col] as a float, or None if the column is missing/NaN."""
    if col not in row or pd.isna(row[col]):
        return None
    return float(row[col])
