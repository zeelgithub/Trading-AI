"""
Candidate source interface -- discovery layer.

A source reads one signal feed and emits `SignalContribution`s (at most one per
symbol per source, but the scorer tolerates more). Sources are READ-ONLY: they
never place orders and never hold trading credentials.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.discovery.candidate import SignalContribution


@runtime_checkable
class CandidateSource(Protocol):
    name: str

    def gather(self) -> list[SignalContribution]:
        """Return this source's contributions for the current run (may be empty)."""
        ...
