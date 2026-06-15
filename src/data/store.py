"""
Local store -- data layer.

Read/write of daily bars to the local SQLite cache (data/trading_bot.db).
Upserts are idempotent on (symbol, ts) so re-ingesting a range is safe.

Boundary: places orders NO.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "trading_bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    ts     TEXT NOT NULL,          -- ISO-8601 UTC
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, ts)
);
"""


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_bars(conn: sqlite3.Connection, symbol: str, bars: pd.DataFrame) -> int:
    """Insert-or-replace bars. `bars` indexed by timestamp with OHLCV columns.

    Returns the number of rows written.
    """
    if bars.empty:
        return 0
    rows = [
        (
            symbol,
            ts.isoformat(),
            float(r.open),
            float(r.high),
            float(r.low),
            float(r.close),
            float(r.volume),
        )
        for ts, r in bars.iterrows()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO bars "
        "(symbol, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def load_bars(
    conn: sqlite3.Connection,
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load bars for a symbol as a time-indexed OHLCV frame (ascending)."""
    query = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ?"
    params: list[object] = [symbol]
    if start:
        query += " AND ts >= ?"
        params.append(start)
    if end:
        query += " AND ts <= ?"
        params.append(end)
    query += " ORDER BY ts ASC"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["ts"])
    return df.set_index("ts")
