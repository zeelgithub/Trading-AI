"""Tests for the 2026-08-25 discovery-universe ingestion speedup:
ingest_symbol's same-day freshness short-circuit, and
batch_ingest_universe's chunked multi-symbol pre-warm. See
src/discovery/universe.py's cached_feature_provider() docstring for why
this exists -- per-symbol ingest across ~4,000 symbols (one HTTP round-trip
each) was the confirmed cause of the bot-discovery / bot-run-paper-propose
scheduled-task failures that day."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data import store
from src.data.ingest import _already_fresh, batch_ingest_universe, ingest_symbol
from tests.unit.synth import make_features


def _bars(end: pd.Timestamp, n: int = 3) -> pd.DataFrame:
    df = make_features([{"close": 100.0} for _ in range(n)])
    df.index = pd.date_range(end=end, periods=n, freq="D", tz="UTC")
    return df


class _RecordingProvider:
    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.calls = 0

    def get_daily_bars(self, symbol, lookback_days=400, start=None):
        self.calls += 1
        return self.bars


class _RecordingMultiProvider:
    """Fake AlpacaData-shaped provider recording the chunks it was called with."""

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.chunks: list[list[str]] = []

    def get_daily_bars_multi(self, symbols, lookback_days=400):
        self.chunks.append(list(symbols))
        return {s: self.bars for s in symbols}


# --- _already_fresh / ingest_symbol short-circuit ----------------------------

def test_already_fresh_true_when_last_bar_is_today(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    now = pd.Timestamp("2026-08-25 12:00", tz="America/New_York")
    store.upsert_bars(conn, "AAPL", _bars(now.tz_convert("UTC")))
    assert _already_fresh(conn, "AAPL", now=now) is True


def test_already_fresh_false_when_last_bar_is_stale(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    now = pd.Timestamp("2026-08-25 12:00", tz="America/New_York")
    stale_end = pd.Timestamp("2026-08-20 12:00", tz="UTC")
    store.upsert_bars(conn, "AAPL", _bars(stale_end))
    assert _already_fresh(conn, "AAPL", now=now) is False


def test_already_fresh_false_when_no_bars_at_all(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    assert _already_fresh(conn, "NEWSYM") is False


def test_ingest_symbol_skips_network_call_when_already_fresh(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "bars.db")
    now = pd.Timestamp("2026-08-25 12:00", tz="America/New_York")
    store.upsert_bars(conn, "AAPL", _bars(now.tz_convert("UTC")))

    provider = _RecordingProvider(_bars(now.tz_convert("UTC")))
    import src.data.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod.pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))

    report = ingest_symbol(conn, "AAPL", provider=provider)
    assert provider.calls == 0
    assert report.rows == 0


# --- batch_ingest_universe ----------------------------------------------------

def test_batch_ingest_chunks_by_chunk_size(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    stale_end = pd.Timestamp("2026-08-01 12:00", tz="UTC")
    provider = _RecordingMultiProvider(_bars(stale_end))

    symbols = [f"SYM{i}" for i in range(5)]
    batch_ingest_universe(conn, symbols, provider=provider, chunk_size=2)

    assert provider.chunks == [["SYM0", "SYM1"], ["SYM2", "SYM3"], ["SYM4"]]


def test_batch_ingest_writes_bars_for_every_symbol(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    stale_end = pd.Timestamp("2026-08-01 12:00", tz="UTC")
    provider = _RecordingMultiProvider(_bars(stale_end, n=5))

    reports = batch_ingest_universe(conn, ["AAPL", "MSFT"], provider=provider)

    assert reports["AAPL"].rows == 5
    assert reports["MSFT"].rows == 5
    assert not store.load_bars(conn, "AAPL").empty
    assert not store.load_bars(conn, "MSFT").empty


def test_batch_ingest_skips_already_fresh_symbols(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "bars.db")
    now = pd.Timestamp("2026-08-25 12:00", tz="America/New_York")
    store.upsert_bars(conn, "FRESH", _bars(now.tz_convert("UTC")))

    import src.data.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod.pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))

    provider = _RecordingMultiProvider(_bars(pd.Timestamp("2026-08-01", tz="UTC")))
    batch_ingest_universe(conn, ["FRESH", "STALE"], provider=provider)

    all_requested = [s for chunk in provider.chunks for s in chunk]
    assert "FRESH" not in all_requested
    assert "STALE" in all_requested


def test_batch_ingest_deduplicates_and_uppercases_symbols(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    provider = _RecordingMultiProvider(_bars(pd.Timestamp("2026-08-01", tz="UTC")))

    batch_ingest_universe(conn, ["aapl", "AAPL", "Aapl"], provider=provider)

    all_requested = [s for chunk in provider.chunks for s in chunk]
    assert all_requested == ["AAPL"]


def test_batch_ingest_empty_universe_is_a_noop(tmp_path):
    conn = store.connect(tmp_path / "bars.db")
    provider = _RecordingMultiProvider(_bars(pd.Timestamp("2026-08-01", tz="UTC")))
    reports = batch_ingest_universe(conn, [], provider=provider)
    assert reports == {}
    assert provider.chunks == []
