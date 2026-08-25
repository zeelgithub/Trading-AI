"""
Ingestion -- data layer.

Normalizes and validates raw daily bars (sort, de-duplicate, gap detection),
then persists them to the local store. Provider bars are already split/dividend
adjusted upstream; this layer guarantees a clean, monotonic, gap-checked frame.

Boundary: read-only with respect to the broker.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.data import store
from src.data.providers.alpaca_data import AlpacaData

_OHLCV = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class IngestReport:
    symbol: str
    rows: int
    written: int
    gap_count: int


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean OHLCV frame: sorted, de-duplicated, columns validated."""
    if df.empty:
        return df
    missing = [c for c in _OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"Bars missing required columns: {missing}")

    df = df[_OHLCV].copy()
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    # Drop rows with any null OHLCV -- corrupt bars must not reach a strategy.
    df = df.dropna(subset=_OHLCV)
    return df


def last_bar_age_days(df: pd.DataFrame, today: date | None = None) -> int | None:
    """Calendar days between the newest bar and `today` (None if no bars).

    The freshness contract for a daily-swing system: a Friday bar is age 1-3
    over a weekend, so a threshold of ~4 days tolerates weekends + one holiday
    while catching a genuinely stalled feed. Never trade on stale data (rule 3).
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return None  # no bars / non-time index: age unknown, caller decides
    last = pd.Timestamp(df.index[-1]).date()
    ref = today or date.today()
    return (ref - last).days


def detect_gaps(df: pd.DataFrame, max_gap_days: int = 5) -> int:
    """Count suspicious gaps between consecutive bars (weekends excluded).

    A daily series should advance ~1 business day per row; a gap larger than
    `max_gap_days` calendar days flags missing data worth investigating.
    """
    if len(df) < 2:
        return 0
    deltas = df.index.to_series().diff().dropna()
    return int((deltas.dt.days > max_gap_days).sum())


# Re-fetch this many days before the newest stored bar on incremental pulls,
# so late corrections / dividend adjustments land (upserts are idempotent).
_INCREMENTAL_OVERLAP_DAYS = 7


def _already_fresh(conn: sqlite3.Connection, symbol: str, now: pd.Timestamp | None = None) -> bool:
    """True if the newest stored bar is already dated today -- a daily
    provider cannot have anything MORE recent than that, so a fetch would be
    a wasted network round-trip. Doesn't account for weekends/holidays (on a
    non-trading day this just means one avoidable-but-harmless fetch
    attempt); the scheduled tasks that call this only run on weekdays anyway.
    """
    last = store.last_bar_ts(conn, symbol)
    if last is None:
        return False
    now_et = now if now is not None else pd.Timestamp.now(tz="America/New_York")
    last_date = (last.tz_convert("America/New_York") if last.tzinfo else last).date()
    return last_date == now_et.date()


def ingest_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    lookback_days: int = 400,
    provider: AlpacaData | None = None,
    incremental: bool = True,
) -> IngestReport:
    """Fetch -> normalize -> store one symbol. Returns an ingest report.

    Incremental by default: when the cache already has bars, only the window
    since the newest stored bar (minus a small overlap) is fetched -- this is
    what keeps a large universe cheap to refresh every cycle. If the newest
    stored bar is already dated today, skips the network call entirely
    (nothing more recent could exist) -- confirmed live 2026-08-25 that
    without this, screening a ~4,000-symbol universe one HTTP round-trip per
    symbol (via this function, called once per symbol by
    live_feature_provider) took 20-40+ minutes and was the direct cause of
    the bot-discovery / bot-run-paper-propose scheduled-task failures.
    Pairs with batch_ingest_universe() below, which pre-warms many symbols
    in a handful of batched round-trips so this per-symbol path then finds
    everything already fresh and does no network I/O at all.
    """
    if incremental and _already_fresh(conn, symbol):
        return IngestReport(symbol=symbol, rows=0, written=0, gap_count=0)
    provider = provider or AlpacaData()
    start = None
    if incremental:
        last = store.last_bar_ts(conn, symbol)
        if last is not None:
            start = (last - pd.Timedelta(days=_INCREMENTAL_OVERLAP_DAYS)).to_pydatetime()
    if start is not None:
        try:
            raw = provider.get_daily_bars(symbol, lookback_days=lookback_days, start=start)
        except TypeError:  # provider without `start` support -> full fetch
            raw = provider.get_daily_bars(symbol, lookback_days=lookback_days)
    else:
        raw = provider.get_daily_bars(symbol, lookback_days=lookback_days)
    clean = normalize_bars(raw)
    gaps = detect_gaps(clean)
    written = store.upsert_bars(conn, symbol, clean)
    return IngestReport(symbol=symbol, rows=len(clean), written=written, gap_count=gaps)


def batch_ingest_universe(
    conn: sqlite3.Connection,
    symbols: list[str],
    lookback_days: int = 400,
    provider: AlpacaData | None = None,
    chunk_size: int = 200,
) -> dict[str, IngestReport]:
    """Pre-warm the bar cache for MANY symbols using Alpaca's native
    multi-symbol bars endpoint (get_daily_bars_multi) -- one HTTP round-trip
    per `chunk_size` symbols instead of one per symbol.

    Returns a report for EVERY symbol requested (deduplicated, uppercased),
    including ones already fresh today that needed no fetch -- callers use
    the key set as "already handled this run, safe to skip re-checking."
    This matters for a reason discovered live 2026-08-25: many thin,
    illiquid small/micro-cap names (from smallcap.py/volatile.py) simply
    haven't printed a trade YET today when this runs, so `_already_fresh`
    correctly says "not fresh" for them even right after this function
    already fetched their latest available data -- without the caller
    tracking "checked this run," src/discovery/universe.py's
    cached_feature_provider() would call ingest_symbol() AGAIN for each one
    when TechnicalSource/VolatilitySource iterate the universe, re-issuing
    a real (if individually fast) network call per such symbol. Measured
    live: symbols like this ran ~140ms/symbol vs. ~10ms/symbol for
    already-fresh large-caps -- across ~2,500 smallcap/volatile symbols,
    a large chunk of that gap was exactly this redundant re-check.

    Not incremental per-symbol like ingest_symbol -- each chunk fetches the
    full `lookback_days` window for every symbol in it, since Alpaca's
    batched endpoint takes one shared start/end for the whole request, not
    a per-symbol one. Upserts are idempotent, so this is safe and correct,
    just not bandwidth-minimal; the round-trip-count reduction is what
    actually matters at this universe size (confirmed live 2026-08-25:
    ~2,800 symbols in ~15s this way vs. 20-40+ minutes one-by-one).

    Called once per discovery cycle, before the per-symbol
    live_feature_provider closure runs for each of the same symbols --
    see src/discovery/universe.py's cached_feature_provider().
    """
    from src.data.providers.alpaca_data import AlpacaData as _AlpacaData

    provider = provider or _AlpacaData()
    all_symbols = list(dict.fromkeys(sym.upper() for sym in symbols))
    reports: dict[str, IngestReport] = {
        s: IngestReport(symbol=s, rows=0, written=0, gap_count=0)
        for s in all_symbols if _already_fresh(conn, s)
    }
    todo = [s for s in all_symbols if s not in reports]
    for i in range(0, len(todo), chunk_size):
        chunk = todo[i:i + chunk_size]
        bars_by_symbol = provider.get_daily_bars_multi(chunk, lookback_days=lookback_days)
        for sym in chunk:
            raw = bars_by_symbol.get(sym)
            if raw is None or raw.empty:
                reports[sym] = IngestReport(symbol=sym, rows=0, written=0, gap_count=0)
                continue
            clean = normalize_bars(raw)
            gaps = detect_gaps(clean)
            written = store.upsert_bars(conn, sym, clean)
            reports[sym] = IngestReport(symbol=sym, rows=len(clean), written=written, gap_count=gaps)
    return reports


def drop_incomplete_bar(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Remove today's still-forming daily bar during the trading session.

    At 15:45 ET the newest 'daily' bar is partial: its close/high/low aren't
    final and its volume is a fraction of the day (worse on the thin IEX feed),
    so signals computed on it diverge from any backtest run on finalized bars.
    Before 16:00 ET, a bar dated today is dropped; after the close it is kept.
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    now_et = now if now is not None else pd.Timestamp.now(tz="America/New_York")
    last = pd.Timestamp(df.index[-1])
    last_date = (last.tz_convert("America/New_York") if last.tzinfo else last).date()
    if last_date == now_et.date() and (now_et.hour, now_et.minute) < (16, 0):
        return df.iloc[:-1]
    return df


def live_feature_provider(config, conn: sqlite3.Connection | None = None):
    """The ONE symbol -> causal-feature-frame callable used by every live
    signal path (orchestrator cycle and discovery screening): incremental
    ingest -> drop the in-progress bar -> build features. Centralized so live
    signals always match what the backtester sees."""
    from src.data.features import build_features

    conn = conn or store.connect()
    lookback = int(config.get("settings.data.lookback_days", 400))

    def provider(symbol: str) -> pd.DataFrame:
        ingest_symbol(conn, symbol, lookback_days=lookback)
        bars = drop_incomplete_bar(store.load_bars(conn, symbol))
        return build_features(bars, config)

    return provider
