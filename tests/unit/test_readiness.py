"""Tests for src/research/readiness.py -- the real-capital readiness
scorecard (docs/ROADMAP.md Step 7's go/no-go bar, made checkable)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from src.common.config import load_config
from src.research.equity_history import EquityHistory, EquityPoint
from src.research.readiness import (
    ReadinessAudit,
    ReadinessCriteria,
    criteria_from_config,
    evaluate_readiness,
)

_LOOSE = ReadinessCriteria(
    min_track_record_days=1, min_days_for_stats=1, min_total_return_pct=-100.0,
    min_annualized_sharpe=-100.0, max_drawdown_pct=100.0, max_bootstrap_p_value=1.0,
    min_psr=0.0,
)


def _noisy_uptrend(n: int) -> list[float]:
    """A deterministic, mildly-noisy profitable equity series: a repeating
    4-day cycle of small positive daily returns (mean 0.5%, real but modest
    variance) so Sharpe/bootstrap/PSR are genuinely computable -- a perfectly
    smooth exponential curve has ~zero variance and degenerates every
    variance-based statistic to 0."""
    cycle = [0.006, 0.004, 0.007, 0.003]
    equities = [100_000.0]
    for i in range(n - 1):
        equities.append(equities[-1] * (1.0 + cycle[i % len(cycle)]))
    return equities


def _points(equities: list[float], as_of_start: date = date(2026, 1, 1)) -> list[EquityPoint]:
    """Build a day-over-day EquityPoint series from a list of equity levels."""
    pts = []
    prev = equities[0]
    for i, eq in enumerate(equities):
        pts.append(EquityPoint(
            date=date.fromordinal(as_of_start.toordinal() + i).isoformat(),
            equity=eq, day_pnl=eq - prev if i else 0.0, open_positions=1,
        ))
        prev = eq
    return pts


# --- insufficient data ---

def test_empty_history_is_insufficient_data():
    report = evaluate_readiness([], ReadinessCriteria())
    assert report.verdict == "insufficient_data"
    assert report.track_record_days == 0
    assert report.blocking == ["track_record_days"]


def test_fewer_than_min_days_for_stats_is_insufficient_data():
    points = _points([100_000.0] * 5)
    report = evaluate_readiness(points, ReadinessCriteria(min_days_for_stats=20))
    assert report.verdict == "insufficient_data"
    assert len(report.checks) == 1  # only track_record_days evaluated


def test_insufficient_data_still_flags_short_track_record_as_blocking():
    points = _points([100_000.0] * 5)
    report = evaluate_readiness(points, ReadinessCriteria(min_days_for_stats=10, min_track_record_days=60))
    assert report.verdict == "insufficient_data"
    assert report.blocking == ["track_record_days"]


# --- full evaluation: happy path ---

def test_steady_profitable_low_drawdown_series_is_ready():
    # 60 days of a mildly-noisy uptrend -- should clear every bar.
    points = _points(_noisy_uptrend(60))
    report = evaluate_readiness(points, ReadinessCriteria())
    assert report.verdict == "ready"
    assert report.blocking == []
    assert report.track_record_days == 60


# --- individual criteria fail in isolation ---

def test_net_negative_return_blocks_on_total_return():
    equities = [100_000.0 - i * 50 for i in range(30)]  # steadily losing
    points = _points(equities)
    report = evaluate_readiness(points, replace(_LOOSE, min_total_return_pct=0.0))
    assert "total_return_pct" in report.blocking
    assert report.verdict == "not_yet"


def test_large_drawdown_blocks_on_max_drawdown():
    equities = [100_000.0] * 10 + [100_000.0 * 0.7] + [100_000.0 * 0.7 * 1.01 ** i for i in range(19)]
    points = _points(equities)
    report = evaluate_readiness(points, replace(_LOOSE, max_drawdown_pct=10.0))
    assert "max_drawdown_pct" in report.blocking


def test_noisy_flat_series_blocks_on_sharpe_and_significance():
    # Alternating up/down with no net trend -- low Sharpe, high bootstrap p.
    equities = [100_000.0]
    for i in range(1, 30):
        equities.append(equities[-1] * (1.01 if i % 2 == 0 else 0.99))
    points = _points(equities)
    report = evaluate_readiness(points, ReadinessCriteria())
    assert report.verdict == "not_yet"
    assert set(report.blocking) & {"annualized_sharpe", "bootstrap_p_value", "psr"}


def test_short_track_record_blocks_even_if_everything_else_passes():
    points = _points(_noisy_uptrend(25))
    report = evaluate_readiness(points, ReadinessCriteria(min_days_for_stats=20, min_track_record_days=60))
    assert report.blocking == ["track_record_days"]
    assert report.verdict == "not_yet"


# --- criteria_from_config ---

def test_criteria_from_config_uses_settings_yaml_defaults():
    criteria = criteria_from_config(load_config())
    assert criteria.min_track_record_days == 60
    assert criteria.min_psr == 0.95


def test_criteria_from_config_reads_overrides():
    base = load_config()
    config = replace(base, settings={**base.settings,
                                      "research": {"readiness": {"min_track_record_days": 10}}})
    criteria = criteria_from_config(config)
    assert criteria.min_track_record_days == 10
    assert criteria.min_psr == 0.95  # unset field keeps the dataclass default


# --- ReadinessAudit persistence ---

def test_audit_append_and_load_round_trip(tmp_path):
    audit = ReadinessAudit(tmp_path / "audit.json")
    report = evaluate_readiness(_points([100_000.0] * 5), ReadinessCriteria())
    audit.append(report)
    loaded = audit.load()
    assert len(loaded) == 1
    assert loaded[0].verdict == report.verdict
    assert loaded[0].track_record_days == 5


def test_audit_keeps_every_entry_not_upserted_by_date(tmp_path):
    audit = ReadinessAudit(tmp_path / "audit.json")
    audit.append(evaluate_readiness(_points([100_000.0] * 5), ReadinessCriteria()))
    audit.append(evaluate_readiness(_points([100_000.0] * 10), ReadinessCriteria()))
    assert len(audit.load()) == 2


def test_audit_corrupt_file_quarantines_and_starts_empty(tmp_path):
    path = tmp_path / "audit.json"
    path.write_text("{not valid json", encoding="utf-8")
    audit = ReadinessAudit(path)
    assert audit.load() == []
    assert list(tmp_path.glob("audit.json.corrupt-*"))
