"""
Candidate model -- discovery layer.

A `SignalContribution` is one source's read on one symbol (a 0-1 confidence plus
a one-line human reason for the digest). A `Candidate` groups every source's
contributions for a symbol and carries the blended score plus the risk-gated
suggestion (entry / stop / qty) once the pipeline has sized it.

Pure data + derived views. Places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The canonical source names. Weights in config are keyed by these.
SOURCES = ("congress", "technical", "news", "fundamentals", "volatility", "social")


@dataclass
class SignalContribution:
    """One source's opinion on one symbol."""

    symbol: str
    source: str            # one of SOURCES
    score: float           # raw source confidence, clamped to [0, 1]
    reason: str            # one-line, human-readable -- shown in the digest
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()
        self.score = max(0.0, min(1.0, float(self.score)))


@dataclass
class Candidate:
    """A symbol with every source's contribution, scored and (maybe) sized."""

    symbol: str
    contributions: list[SignalContribution] = field(default_factory=list)
    score: float = 0.0                 # 0-100 blended, set by Scorer
    # Filled by the pipeline once the idea clears the risk gate:
    entry_price: float | None = None
    stop_loss: float | None = None
    suggested_qty: float = 0.0
    strategy: str = "discovery"        # routing strategy used for the proposal

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()

    @property
    def sources(self) -> list[str]:
        return sorted({c.source for c in self.contributions})

    @property
    def stars(self) -> int:
        """Map the 0-100 score to a 1-5 star rating for the digest."""
        for threshold, stars in ((80, 5), (60, 4), (40, 3), (20, 2)):
            if self.score >= threshold:
                return stars
        return 1

    def contribution(self, source: str) -> SignalContribution | None:
        """The (highest-scoring) contribution from a given source, if any."""
        matches = [c for c in self.contributions if c.source == source]
        return max(matches, key=lambda c: c.score) if matches else None

    def reasons(self) -> list[str]:
        """Per-source reason lines, strongest signal first."""
        return [c.reason for c in sorted(self.contributions, key=lambda c: -c.score)]
