"""
Scorer -- discovery layer.

Blends a candidate's per-source contributions into a single 0-100 score.

Design:
  - Each source has a weight (from config). The blended score is the
    weight-weighted average of the contributions that came from *active*
    sources, renormalised by the active weight total -- so the score is always
    a clean 0-100 regardless of how many sources are switched on this phase.
  - Because a missing source contributes nothing, a symbol that lights up on
    several sources at once naturally outscores one that fires on only one.
    Confluence is rewarded without a special-case bonus.

Deterministic and pure. Places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.discovery.candidate import SOURCES, Candidate

DEFAULT_WEIGHTS = {
    "congress": 0.35,
    "technical": 0.35,
    "news": 0.15,
    "fundamentals": 0.15,
}


@dataclass
class Scorer:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    active_sources: frozenset[str] = frozenset(SOURCES)

    @classmethod
    def from_config(cls, config) -> Scorer:
        weights = {**DEFAULT_WEIGHTS, **(config.get("settings.discovery.weights", {}) or {})}
        srcs = config.get("settings.discovery.sources", {}) or {}
        active = frozenset(s for s in SOURCES if srcs.get(s, False))
        return cls(weights={k: float(v) for k, v in weights.items()}, active_sources=active)

    def score(self, candidate: Candidate) -> float:
        total_w = sum(self.weights.get(s, 0.0) for s in self.active_sources)
        if total_w <= 0:
            return 0.0
        # Best contribution per active source (a source may emit more than once).
        best: dict[str, float] = {}
        for c in candidate.contributions:
            if c.source in self.active_sources:
                best[c.source] = max(best.get(c.source, 0.0), c.score)
        got = sum(self.weights.get(s, 0.0) * v for s, v in best.items())
        return round(100.0 * got / total_w, 1)
