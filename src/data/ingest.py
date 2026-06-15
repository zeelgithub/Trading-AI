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


def detect_gaps(df: pd.DataFrame, max_gap_days: int = 5) -> int:
    """Count suspicious gaps between consecutive bars (weekends excluded).

    A daily series should advance ~1 business day per row; a gap larger than
    `max_gap_days` calendar days flags missing data worth investigating.
    """
    if len(df) < 2:
        return 0
    deltas = df.index.to_series().diff().dropna()
    return int((deltas.dt.days > max_gap_days).sum())


def ingest_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    lookback_days: int = 400,
    provider: AlpacaData | None = None,
) -> IngestReport:
    """Fetch -> normalize -> store one symbol. Returns an ingest report."""
    provider = provider or AlpacaData()
    raw = provider.get_daily_bars(symbol, lookback_days=lookback_days)
    clean = normalize_bars(raw)
    gaps = detect_gaps(clean)
    written = store.upsert_bars(conn, symbol, clean)
    return IngestReport(symbol=symbol, rows=len(clean), written=written, gap_count=gaps)
