"""
Discovery digest -- notify layer.

Pure formatting for the daily ideas push: a header summarising the run plus one
message per idea (the per-idea text + its Approve/Deny buttons are sent
separately so each idea is independently approvable). Takes already-computed
`Candidate`s and returns strings; it places no orders and calls nothing external.

Boundary: formatting only; places orders NO.
"""

from __future__ import annotations

from datetime import date

from src.discovery.candidate import Candidate


def ideas_header(shown: int, screened: int, as_of: date | None = None) -> str:
    day = (as_of or date.today()).isoformat()
    if shown == 0:
        return (f"📊 DAILY IDEAS — {day}\nScreened {screened} names; nothing cleared "
                f"the bar today. Nothing to do.")
    return (f"📊 DAILY IDEAS — {day}   (top {shown} of {screened} screened)\n"
            f"Tap Approve to place on paper (risk-gated), or Deny.")


def source_summary(stats: list) -> str:
    """Per-source contribution table for /sources."""
    if not stats:
        return ("📡 SOURCES\nNo discovery history yet. Run /ideas (or "
                "`python -m scripts.run_discovery`) to start the ledger.")
    lines = ["📡 SOURCES — what each signal contributes"]
    for s in stats:
        lines.append(
            f"{s.source}: {s.surfaced} surfaced · {s.proposed} proposed · "
            f"avg score {s.avg_score:g}"
        )
    lines.append("\nP&L by strategy is on /strategies. Reweight in config "
                 "(discovery.weights) once a source proves itself.")
    return "\n".join(lines)


def idea_text(candidate: Candidate) -> str:
    """One idea, rendered for its own Approve/Deny message."""
    stars = "★" * candidate.stars + "☆" * (5 - candidate.stars)
    lines = [f"{candidate.symbol}  {stars}  score {candidate.score:g}"]
    for reason in candidate.reasons():
        lines.append(f"  • {reason}")
    if candidate.entry_price and candidate.stop_loss and candidate.suggested_qty:
        pct = (candidate.entry_price - candidate.stop_loss) / candidate.entry_price * 100.0
        lines.append(
            f"  → suggest BUY {candidate.suggested_qty:g} @ ~${candidate.entry_price:,.2f}, "
            f"stop ${candidate.stop_loss:,.2f} (-{pct:.0f}%)  [{candidate.strategy}]"
        )
    return "\n".join(lines)
