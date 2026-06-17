"""Tests for market-data read queries (compact snapshots over the bar cache)."""

from __future__ import annotations

import pandas as pd

from src.common.config import load_config
from src.data import queries, store


def _bars(n: int = 60, start: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    closes = [start + i for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def _seeded_conn(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    store.upsert_bars(conn, "TEST", _bars())
    return conn


def test_recent_bars(tmp_path):
    out = queries.recent_bars("test", days=5, conn=_seeded_conn(tmp_path))
    assert out["symbol"] == "TEST"
    assert len(out["bars"]) == 5
    assert set(out["bars"][-1]) == {"date", "open", "high", "low", "close", "volume"}
    assert out["bars"][-1]["close"] == 159.0   # oldest-first; last = 100 + 59


def test_recent_bars_empty(tmp_path):
    out = queries.recent_bars("NONE", conn=store.connect(tmp_path / "empty.db"))
    assert out["bars"] == []
    assert "note" in out


def test_indicator_snapshot(tmp_path):
    out = queries.indicator_snapshot("test", conn=_seeded_conn(tmp_path), config=load_config())
    assert out["symbol"] == "TEST"
    assert out["close"] == 159.0
    assert isinstance(out["regime"], str) and out["regime"]
    assert out["rsi"] is not None        # 14-period RSI is warm after 60 bars
    assert "ema200" in out               # may be null (not warmed up), but present


def test_indicator_snapshot_empty(tmp_path):
    out = queries.indicator_snapshot("NONE", conn=store.connect(tmp_path / "empty.db"))
    assert "note" in out
