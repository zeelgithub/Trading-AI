"""
Universe + feature-provider helpers -- discovery layer.

The technical source needs (a) a list of symbols to screen and (b) a way to turn
a symbol into a causal feature frame. Discovery widens the watchlist-only
universe with the names Congress is buying, a configured `extra` list (how
brand-new tickers get a technical read), optionally the S&P 500/400/600
(src/discovery/sp500.py, sp400.py, sp600.py -- static, periodically-refreshed
lists; see sp500.py's docstring for why a static list was chosen over
Alpaca's live most-actives screener), and optionally two data-derived
screens (NOT index membership, built by their own scripts -- see each
module's docstring): a small/micro-cap screen (src/discovery/smallcap.py,
scripts/build_smallcap_universe.py) and a "fluctuating/volatile" screen
(src/discovery/volatile.py, scripts/build_volatile_universe.py) for names
too large for smallcap.py's cap band or not yet in an S&P index but still
genuinely volatile (bitcoin miners, recent high-beta IPOs, etc).

Boundary: read-only; places orders NO, holds credentials NO (the feature
provider uses market-data creds only, via the data layer).
"""

from __future__ import annotations

from pathlib import Path

from congress_copy.models import DisclosedTrade
from src.common.config import Config
from src.discovery.smallcap import SMALLCAP_TICKERS
from src.discovery.sp400 import SP400_TICKERS
from src.discovery.sp500 import SP500_TICKERS
from src.discovery.sp600 import SP600_TICKERS
from src.discovery.volatile import VOLATILE_TICKERS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISCLOSURES = PROJECT_ROOT / "congress_copy" / "data" / "disclosures.json"


def congress_buy_tickers(path: str | Path = DEFAULT_DISCLOSURES) -> list[str]:
    """Distinct tickers with a disclosed stock BUY (order-preserving)."""
    import json

    p = Path(path)
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out: list[str] = []
    for r in rows:
        try:
            t = DisclosedTrade.from_dict(r)
        except (KeyError, ValueError):
            continue
        if t.is_stock and t.is_buy and t.ticker not in out:
            out.append(t.ticker)
    return out


def discovery_universe(config: Config, disclosures: str | Path = DEFAULT_DISCLOSURES) -> list[str]:
    """Watchlist + congress buys + configured extras (+ S&P 500/400/600 and/or
    the small/micro-cap and/or volatile screens if enabled), de-duplicated.

    Order-preserving dedup via a side `seen` set -- an O(n) membership check
    per candidate instead of `t not in syms` (O(n) against the growing list
    itself, i.e. O(n^2) overall), which matters once this merges ~4,000
    symbols across 7 stages."""
    syms: list[str] = list(config.enabled_symbols())
    seen: set[str] = set(syms)

    def _extend(tickers) -> None:
        for t in tickers:
            t = str(t).upper()
            if t not in seen:
                seen.add(t)
                syms.append(t)

    _extend(congress_buy_tickers(disclosures))
    _extend(config.get("settings.discovery.universe.extra", []) or [])
    if config.get("settings.discovery.universe.sp500", False):
        _extend(SP500_TICKERS)
    if config.get("settings.discovery.universe.sp400", False):
        _extend(SP400_TICKERS)
    if config.get("settings.discovery.universe.sp600", False):
        _extend(SP600_TICKERS)
    if config.get("settings.discovery.universe.smallcap", False):
        _extend(SMALLCAP_TICKERS)
    if config.get("settings.discovery.universe.volatile", False):
        _extend(VOLATILE_TICKERS)
    return syms


def cached_feature_provider(config: Config, universe: list[str] | None = None):
    """A symbol -> feature-frame callable. Same underlying pipeline as the
    data layer's `live_feature_provider` (incremental ingest, in-progress
    bar dropped, causal features), so the technical source sees exactly
    what the live cycle sees.

    When `universe` is given, batch-pre-warms the bar cache for every symbol
    in it FIRST (src/data/ingest.batch_ingest_universe -- one HTTP round-trip
    per ~200 symbols via Alpaca's native multi-symbol bars endpoint) before
    returning the per-symbol closure, and the closure then SKIPS re-checking
    freshness entirely for any symbol that pre-warm already covered --
    calling ingest_symbol() again for those would be pure redundant work
    within the same run. Both pieces were needed, discovered live 2026-08-25
    in two stages:
      1. Without pre-warming at all, per-symbol incremental ingest (one HTTP
         round-trip EACH, the original behavior) took a confirmed 20-40+
         minutes across this project's ~4,000-symbol universe -- the direct
         cause of that day's bot-discovery / bot-run-paper-propose
         scheduled-task failures (exit code 3221225786, consistent with Task
         Scheduler killing an overrunning job).
      2. Pre-warming alone wasn't enough: many thin, illiquid small/micro-cap
         names (from smallcap.py/volatile.py) simply haven't printed a trade
         YET today when a cycle runs, so `_already_fresh`'s same-day check
         correctly says "not fresh" for them even moments after pre-warm
         already fetched their latest available data -- every subsequent
         per-symbol call was re-issuing a real (if individually fast, ~140ms)
         network round-trip for exactly these names. Measured live: ~2,500
         smallcap/volatile symbols at that rate is minutes of pure waste.
         Tracking "already handled this run" (via batch_ingest_universe's
         now-complete return value -- every requested symbol, not just the
         ones that needed a fetch) and skipping ingest_symbol entirely for
         them closes that gap; only a symbol OUTSIDE the pre-warmed universe
         (an edge case -- e.g. this closure reused for a single ad hoc
         lookup) still goes through the normal per-symbol ingest path.

    Thread safety: sqlite3 connections are bound to the thread that opened
    them (store.connect()'s default, check_same_thread=True) -- calling this
    from a thread other than the one that opened `conn` raises
    sqlite3.ProgrammingError, not just a race. The discovery pipeline now
    runs sources concurrently (pipeline.py's DiscoveryPipeline._gather()),
    and technical/volatility both share this one closure, so the returned
    `provider` lazily opens (and reuses) one connection PER CALLING THREAD
    via threading.local -- the thread that built this closure keeps using
    `conn` itself (no extra connection), any other thread gets its own,
    first-use-lazy. This only fixes the check_same_thread rejection, not
    write contention: if two threads' connections both try to write at once
    (only possible on a symbol OUTSIDE the pre-warmed universe -- ingest_symbol's
    write path), sqlite3's default busy timeout (5s) can still raise
    "database is locked" past that window. That's not new here -- every
    caller of this closure already wraps it in
    src/discovery/sources/_util.safe_call, so a locked-DB error on one
    symbol fails soft (skipped, not raised) exactly like a missing-data one
    always has."""
    import threading

    from src.data import store
    from src.data.features import build_features
    from src.data.ingest import batch_ingest_universe, drop_incomplete_bar, ingest_symbol

    conn = store.connect()
    lookback = int(config.get("settings.data.lookback_days", 400))
    already_handled: set[str] = set()
    if universe:
        reports = batch_ingest_universe(conn, universe, lookback_days=lookback)
        already_handled = set(reports.keys())

    _local = threading.local()
    _local.conn = conn  # seed the building thread's slot -- reuse, don't reopen

    def _thread_conn():
        c = getattr(_local, "conn", None)
        if c is None:
            c = store.connect()
            _local.conn = c
        return c

    def provider(symbol: str):
        c = _thread_conn()
        sym = symbol.upper()
        if sym not in already_handled:
            ingest_symbol(c, sym, lookback_days=lookback)
        bars = drop_incomplete_bar(store.load_bars(c, sym))
        return build_features(bars, config)

    return provider
