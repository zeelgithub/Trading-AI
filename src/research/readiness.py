"""
Real-capital readiness audit -- research layer.

Turns docs/ROADMAP.md Step 7's open-ended "weeks/months of genuine
profitability" bar into named, computed, pass/fail criteria against the
real paper track record (src/research/equity_history.py), so the
real-capital go/no-go question has a concrete, revisitable answer instead
of a sentence. Reuses the same statistical machinery already validated for
backtests (src/research/significance.py) -- this module applies the
existing bootstrap-p-value and probabilistic-Sharpe-ratio functions to the
live daily-equity series instead of per-trade backtest returns; it does
not invent new formulas.

This is the "decision audit" piece of docs/ROADMAP.md's Phase 6, reframed
2026-08-26: the original scope (data-driven agent registries, per-agent LLM
budgets) assumed the cognitive-plane agents would eventually run live
against a real Anthropic API key. They remain deliberately keyless/dormant
(human-in-the-loop via pasted Claude.ai briefs, see src/notify/briefs.py),
so that infrastructure has nothing to observe yet. The one part of Phase 6
that still answers a real open question -- a persisted decision audit -- is
redirected here instead.

Boundary: pure evaluation + a small JSON store; places orders NO; never
gates or automates the real-capital decision, only informs it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields as _dc_fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger
from src.research.equity_history import EquityPoint
from src.research.significance import bootstrap_pvalue, probabilistic_sharpe_ratio

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "state" / "readiness_audit.json"

_TRADING_DAYS = 252


@dataclass(frozen=True)
class ReadinessCriteria:
    """The configurable real-capital readiness bar. Defaults here mirror
    config/settings.yaml's research.readiness block -- see that file's
    comments for the rationale behind each number."""
    min_track_record_days: int = 60
    min_days_for_stats: int = 20
    min_total_return_pct: float = 0.0
    min_annualized_sharpe: float = 0.5
    max_drawdown_pct: float = 15.0
    max_bootstrap_p_value: float = 0.05
    min_psr: float = 0.95


@dataclass
class ReadinessCheck:
    name: str
    passed: bool
    actual: float
    threshold: float


@dataclass
class ReadinessReport:
    computed_at: str
    track_record_days: int
    verdict: str  # "ready" | "not_yet" | "insufficient_data"
    checks: list[ReadinessCheck] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)


def criteria_from_config(config) -> ReadinessCriteria:
    """Build ReadinessCriteria from config/settings.yaml's research.readiness
    block, falling back to this module's own defaults for anything unset or
    unrecognized (config_schema.py already validates types/ranges for the
    known fields; this just filters to what the dataclass accepts)."""
    raw = config.get("settings.research.readiness", {}) or {}
    valid = {f.name for f in _dc_fields(ReadinessCriteria)}
    return ReadinessCriteria(**{k: v for k, v in raw.items() if k in valid})


def _daily_returns(points: list[EquityPoint]) -> list[float]:
    """One return per tracked day: day_pnl / prior-day equity (equity minus
    that same day's own P&L -- no separate lookup of the previous point
    needed). A day with zero prior equity contributes 0.0 rather than
    dividing by zero."""
    out = []
    for p in points:
        prior = p.equity - p.day_pnl
        out.append(p.day_pnl / prior if prior else 0.0)
    return out


def _max_drawdown_pct(points: list[EquityPoint]) -> float:
    peak = points[0].equity
    worst = 0.0
    for p in points:
        peak = max(peak, p.equity)
        if peak > 0:
            worst = max(worst, (peak - p.equity) / peak * 100.0)
    return round(worst, 2)


def evaluate_readiness(
    points: list[EquityPoint], criteria: ReadinessCriteria | None = None,
) -> ReadinessReport:
    """Score the real paper track record against `criteria`. Pure function --
    reads/writes nothing; the caller decides whether to persist the result
    via ReadinessAudit."""
    criteria = criteria or ReadinessCriteria()
    now = datetime.now(timezone.utc).isoformat()
    n = len(points)

    track_record_check = ReadinessCheck(
        name="track_record_days", passed=n >= criteria.min_track_record_days,
        actual=n, threshold=criteria.min_track_record_days,
    )

    if n < criteria.min_days_for_stats:
        # Too few points for Sharpe/drawdown/bootstrap/PSR to mean anything
        # (mirrors scoreboard.classify()'s own num_trades < 10 -> noise
        # floor) -- report this honestly rather than computing garbage stats.
        return ReadinessReport(
            computed_at=now, track_record_days=n, verdict="insufficient_data",
            checks=[track_record_check],
            blocking=[] if track_record_check.passed else ["track_record_days"],
        )

    daily_returns = _daily_returns(points)
    start_equity = points[0].equity
    current_equity = points[-1].equity
    total_return_pct = ((current_equity - start_equity) / start_equity * 100.0) if start_equity else 0.0

    r = np.asarray(daily_returns, dtype=float)
    sd = float(r.std(ddof=1)) if n >= 2 else 0.0
    annualized_sharpe = float(r.mean() / sd * (_TRADING_DAYS ** 0.5)) if sd > 1e-12 else 0.0

    drawdown = _max_drawdown_pct(points)
    boot = bootstrap_pvalue(daily_returns)
    psr = probabilistic_sharpe_ratio(daily_returns)

    checks = [
        track_record_check,
        ReadinessCheck("total_return_pct", total_return_pct >= criteria.min_total_return_pct,
                       round(total_return_pct, 2), criteria.min_total_return_pct),
        ReadinessCheck("annualized_sharpe", annualized_sharpe >= criteria.min_annualized_sharpe,
                       round(annualized_sharpe, 2), criteria.min_annualized_sharpe),
        ReadinessCheck("max_drawdown_pct", drawdown <= criteria.max_drawdown_pct,
                       drawdown, criteria.max_drawdown_pct),
        ReadinessCheck("bootstrap_p_value", boot["p_value"] <= criteria.max_bootstrap_p_value,
                       boot["p_value"], criteria.max_bootstrap_p_value),
        ReadinessCheck("psr", psr >= criteria.min_psr, psr, criteria.min_psr),
    ]
    blocking = [c.name for c in checks if not c.passed]
    verdict = "ready" if not blocking else "not_yet"
    return ReadinessReport(computed_at=now, track_record_days=n, verdict=verdict,
                            checks=checks, blocking=blocking)


class ReadinessAudit:
    """Append-only persisted trail of every readiness evaluation -- the
    'decision audit' this module exists to provide. Unlike EquityHistory
    (one row per calendar date, upserted), every explicit check-in is kept
    as its own event: a same-day re-check after acting on a blocking issue
    is itself meaningful history, not noise to collapse."""

    def __init__(self, path: str | Path = DEFAULT_AUDIT_PATH) -> None:
        self.path = Path(path)

    def load(self) -> list[ReadinessReport]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("readiness").error(
                "readiness audit corrupt; moved to %s -- starting empty", quarantined)
            return []
        rows = payload or []
        return [_report_from_dict(r) for r in rows]

    def append(self, report: ReadinessReport) -> None:
        rows = self.load()
        rows.append(report)
        atomic_write_json(self.path, [asdict(r) for r in rows])


def _report_from_dict(d: dict) -> ReadinessReport:
    checks = [ReadinessCheck(**c) for c in d.get("checks", [])]
    return ReadinessReport(
        computed_at=d["computed_at"], track_record_days=d["track_record_days"],
        verdict=d["verdict"], checks=checks, blocking=d.get("blocking", []),
    )
