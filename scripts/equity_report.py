"""
Entrypoint: print the daily equity/P&L track record (src/research/
equity_history.py) -- one row per day since paper EXECUTE started
(docs/ROADMAP.md Step 7), so you can actually see how the strategies are
doing day to day instead of reconstructing it from logs/audit.jsonl by hand.

Read-only: touches no config, no broker, no trading decision -- just reads
state/equity_history.json (written once per real cycle by the orchestrator).

    python -m scripts.equity_report              # full history
    python -m scripts.equity_report --days 14     # last 14 tracked days only
"""

from __future__ import annotations

import argparse

from src.research.equity_history import EquityHistory, EquityPoint

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return _SPARK_CHARS[0] * len(values)
    span = hi - lo
    return "".join(
        _SPARK_CHARS[min(len(_SPARK_CHARS) - 1, int((v - lo) / span * (len(_SPARK_CHARS) - 1)))]
        for v in values
    )


def build_report(points: list[EquityPoint]) -> str:
    if not points:
        return ("No equity history yet -- nothing has run a real cycle since "
                "src/research/equity_history.py was added. Run `python -m "
                "scripts.run_paper --execute` (or wait for the next scheduled "
                "run) to record the first day.")

    lines: list[str] = []
    lines.append(f"EQUITY TRACK RECORD -- since {points[0].date}")
    lines.append("=" * 80)
    lines.append(f"{'date':<12}{'equity':>14}{'day P&L':>14}{'cum P&L':>14}{'open':>7}  halted")
    start_equity = points[0].equity
    for p in points:
        cum = p.equity - start_equity
        halted_flag = f"  {p.halt_reason}" if p.halted else ""
        lines.append(
            f"{p.date:<12}{p.equity:>14,.2f}{p.day_pnl:>+14,.2f}{cum:>+14,.2f}{p.open_positions:>7}{halted_flag}"
        )
    lines.append("-" * 80)

    equities = [p.equity for p in points]
    spark = _sparkline(equities)
    if spark:
        lines.append(f"trend: {spark}  (low ${min(equities):,.0f} -> high ${max(equities):,.0f})")

    n = len(points)
    current = points[-1].equity
    total_pnl = current - start_equity
    total_pct = (total_pnl / start_equity * 100.0) if start_equity else 0.0
    win_days = sum(1 for p in points if p.day_pnl > 0)
    loss_days = sum(1 for p in points if p.day_pnl < 0)
    flat_days = n - win_days - loss_days
    halts = [p for p in points if p.halted]

    lines.append(f"{n} day(s) tracked ({points[0].date} -> {points[-1].date})")
    lines.append(f"starting equity: ${start_equity:,.2f}   current equity: ${current:,.2f}")
    lines.append(f"total P&L: {total_pnl:+,.2f} ({total_pct:+.2f}%)")
    if points:
        best = max(points, key=lambda p: p.day_pnl)
        worst = min(points, key=lambda p: p.day_pnl)
        lines.append(f"best day: {best.date} ({best.day_pnl:+,.2f})   "
                     f"worst day: {worst.date} ({worst.day_pnl:+,.2f})")
    lines.append(f"win days: {win_days}/{n} ({win_days / n * 100.0:.1f}%)  "
                 f"loss days: {loss_days}  flat days: {flat_days}")
    if halts:
        reasons = ", ".join(f"{p.date}: {p.halt_reason}" for p in halts)
        lines.append(f"halts: {len(halts)} -- {reasons}")
    else:
        lines.append("halts: 0")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the daily equity/P&L track record.")
    parser.add_argument("--days", type=int, default=None, help="only show the last N tracked days")
    args = parser.parse_args()

    points = EquityHistory().load()
    if args.days:
        points = points[-args.days:]
    print(build_report(points))


if __name__ == "__main__":
    main()
