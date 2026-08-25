"""Unit tests for scripts/equity_report.py's build_report() -- the pure
text-report function (no I/O), separated from main() so it's testable and
reusable (e.g. a future phone /report command) without re-reading the file."""

from __future__ import annotations

from scripts.equity_report import build_report
from src.research.equity_history import EquityPoint


def test_empty_history_gives_a_helpful_message():
    report = build_report([])
    assert "No equity history yet" in report


def test_single_day_report_shows_zero_change():
    points = [EquityPoint(date="2026-08-24", equity=100_000.0, day_pnl=0.0, open_positions=0)]
    report = build_report(points)
    assert "2026-08-24" in report
    assert "1 day(s) tracked" in report
    assert "total P&L: +0.00 (+0.00%)" in report


def test_multi_day_report_computes_cumulative_pnl_and_win_rate():
    points = [
        EquityPoint(date="2026-08-24", equity=100_000.0, day_pnl=0.0, open_positions=0),
        EquityPoint(date="2026-08-25", equity=100_500.0, day_pnl=500.0, open_positions=1),
        EquityPoint(date="2026-08-26", equity=100_200.0, day_pnl=-300.0, open_positions=1),
    ]
    report = build_report(points)
    assert "3 day(s) tracked (2026-08-24 -> 2026-08-26)" in report
    assert "starting equity: $100,000.00   current equity: $100,200.00" in report
    assert "total P&L: +200.00 (+0.20%)" in report
    assert "best day: 2026-08-25 (+500.00)" in report
    assert "worst day: 2026-08-26 (-300.00)" in report
    assert "win days: 1/3" in report
    assert "halts: 0" in report


def test_halted_day_appears_in_report_with_reason():
    points = [
        EquityPoint(date="2026-08-24", equity=100_000.0, day_pnl=0.0, open_positions=0),
        EquityPoint(date="2026-08-25", equity=95_000.0, day_pnl=-5000.0, open_positions=3,
                    halted=True, halt_reason="kill switch: daily loss -5.0%"),
    ]
    report = build_report(points)
    assert "halts: 1" in report
    assert "kill switch: daily loss -5.0%" in report
