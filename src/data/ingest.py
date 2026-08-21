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
    what keeps a large universe cheap to refresh every cycle.
    """
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


def drop_incomplete_bar(df: pd.DataFrame, now: "pd.Timestamp | None" = None) -> pd.DataFrame:
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
