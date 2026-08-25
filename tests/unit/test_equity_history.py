"""Unit tests for src/research/equity_history.py -- the daily equity/P&L
track record used to answer "how is the paper track record actually doing"
over weeks/months, not just today."""

from __future__ import annotations

from datetime import date

from src.research.equity_history import EquityHistory, EquityPoint


def test_empty_history_is_empty_list(tmp_path):
    hist = EquityHistory(tmp_path / "eq.json")
    assert hist.load() == []


def test_record_appends_a_point(tmp_path):
    hist = EquityHistory(tmp_path / "eq.json")
    hist.record(equity=100_000.0, day_pnl=500.0, open_positions=2, as_of=date(2026, 8, 24))
    points = hist.load()
    assert points == [EquityPoint(
        date="2026-08-24", equity=100_000.0, day_pnl=500.0,
        open_positions=2, halted=False, halt_reason=None,
    )]


def test_recording_the_same_date_twice_replaces_not_duplicates(tmp_path):
    """A manual test cycle and the real scheduled cycle both running the same
    day (or a retried run) must not turn one real day into two points on the
    graph."""
    hist = EquityHistory(tmp_path / "eq.json")
    hist.record(equity=100_000.0, day_pnl=0.0, open_positions=0, as_of=date(2026, 8, 24))
    hist.record(equity=100_500.0, day_pnl=500.0, open_positions=1, as_of=date(2026, 8, 24))
    points = hist.load()
    assert len(points) == 1
    assert points[0].equity == 100_500.0
    assert points[0].day_pnl == 500.0


def test_multiple_dates_accumulate_in_order(tmp_path):
    hist = EquityHistory(tmp_path / "eq.json")
    hist.record(equity=100_000.0, day_pnl=0.0, open_positions=0, as_of=date(2026, 8, 24))
    hist.record(equity=99_000.0, day_pnl=-1000.0, open_positions=1, as_of=date(2026, 8, 21))
    hist.record(equity=101_000.0, day_pnl=2000.0, open_positions=1, as_of=date(2026, 8, 25))
    points = hist.load()
    assert [p.date for p in points] == ["2026-08-21", "2026-08-24", "2026-08-25"]


def test_halted_day_records_halt_reason(tmp_path):
    hist = EquityHistory(tmp_path / "eq.json")
    hist.record(equity=95_000.0, day_pnl=-5000.0, open_positions=3, halted=True,
                halt_reason="kill switch: daily loss -5.0%", as_of=date(2026, 8, 24))
    points = hist.load()
    assert points[0].halted is True
    assert points[0].halt_reason == "kill switch: daily loss -5.0%"


def test_corrupt_file_quarantines_and_starts_empty(tmp_path):
    path = tmp_path / "eq.json"
    path.write_text("{not valid json", encoding="utf-8")
    hist = EquityHistory(path)
    assert hist.load() == []
    # original corrupt content preserved somewhere, not silently deleted
    quarantined = list(tmp_path.glob("eq.json.corrupt-*"))
    assert len(quarantined) == 1
