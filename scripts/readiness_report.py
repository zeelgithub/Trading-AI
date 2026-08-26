"""
Entrypoint: print the real-capital readiness scorecard (src/research/
readiness.py) -- turns docs/ROADMAP.md Step 7's open-ended "weeks/months of
genuine profitability" into checkable pass/fail criteria against the real
paper track record, and appends the result to state/readiness_audit.json so
there's a persisted trail of when the bar was (and wasn't) met.

Read-only: touches no broker, no trading decision -- reads
state/equity_history.json and config/settings.yaml's research.readiness
block, evaluates, prints, and appends to the audit trail.

    python -m scripts.readiness_report                # current scorecard
    python -m scripts.readiness_report --history 10   # last 10 audit entries
"""

from __future__ import annotations

import argparse

from src.common.config import load_config
from src.research.equity_history import EquityHistory
from src.research.readiness import (
    ReadinessAudit,
    ReadinessReport,
    criteria_from_config,
    evaluate_readiness,
)

_VERDICT_LABEL = {
    "ready": "READY",
    "not_yet": "NOT YET",
    "insufficient_data": "INSUFFICIENT DATA",
}


def build_scorecard(report: ReadinessReport) -> str:
    lines = [
        f"REAL-CAPITAL READINESS -- {_VERDICT_LABEL.get(report.verdict, report.verdict.upper())}",
        "=" * 72,
        f"track record: {report.track_record_days} day(s)   as of {report.computed_at}",
    ]
    if not report.checks:
        lines.append("Not enough tracked days yet to evaluate any criteria.")
    for c in report.checks:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name:<20} actual={c.actual!s:<10} threshold={c.threshold}")
    lines.append("-" * 72)
    if report.blocking:
        lines.append("blocking: " + ", ".join(report.blocking))
    else:
        lines.append("nothing blocking" if report.checks else "waiting on more tracked days")
    return "\n".join(lines)


def build_history(reports: list[ReadinessReport]) -> str:
    if not reports:
        return "No readiness checks recorded yet -- run this script once to record the first."
    lines = ["READINESS HISTORY", "=" * 72]
    for r in reports:
        label = _VERDICT_LABEL.get(r.verdict, r.verdict.upper())
        blocking = f"   blocking: {', '.join(r.blocking)}" if r.blocking else ""
        lines.append(f"{r.computed_at}  {label:<20} {r.track_record_days} day(s) tracked{blocking}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the real-capital readiness scorecard.")
    parser.add_argument("--history", type=int, default=None,
                         help="print the last N audit entries instead of a fresh scorecard")
    args = parser.parse_args()

    audit = ReadinessAudit()
    if args.history:
        print(build_history(audit.load()[-args.history:]))
        return

    config = load_config()
    points = EquityHistory().load()
    report = evaluate_readiness(points, criteria_from_config(config))
    print(build_scorecard(report))
    audit.append(report)


if __name__ == "__main__":
    main()
