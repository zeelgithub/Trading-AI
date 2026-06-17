"""
Discovery ledger -- discovery layer.

An append-only record (state/discovery_ledger.jsonl) of every idea discovery has
surfaced: its blended score, which sources voted for it, and whether it was
proposed to the phone. This is the raw material for "track what works, adapt
trust": over time you can see which sources actually lead to good ideas and
reweight them in config. It NEVER changes weights on its own and never touches
the trading path -- it only records and summarises.

Outcome P&L is tracked separately by the existing strategy scoreboard (congress
ideas land on the `congress_copy` row, technical ideas on their strategy row);
this ledger answers the upstream question of source *contribution*.

Boundary: read/write JSON state; places orders NO.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "state" / "discovery_ledger.jsonl"


@dataclass
class SourceStats:
    source: str
    surfaced: int = 0          # ideas this source voted on (above the floor)
    proposed: int = 0          # of those, pushed to the phone
    score_sum: float = 0.0

    @property
    def avg_score(self) -> float:
        return round(self.score_sum / self.surfaced, 1) if self.surfaced else 0.0


@dataclass
class DiscoveryLedger:
    path: Path = field(default_factory=lambda: DEFAULT_PATH)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_surface(self, candidates, proposals, as_of: datetime | None = None) -> None:
        """Append one row per surfaced candidate (those above the score floor)."""
        ts = (as_of or datetime.now(timezone.utc)).isoformat()
        proposed = {p.symbol: p.id for p in proposals}
        lines = []
        for c in candidates:
            lines.append(json.dumps({
                "ts": ts,
                "symbol": c.symbol,
                "score": c.score,
                "sources": c.sources,
                "proposed": c.symbol in proposed,
                "proposal_id": proposed.get(c.symbol),
                "strategy": c.strategy,
            }))
        if lines:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def summarize(self) -> list[SourceStats]:
        """Per-source contribution stats, most-surfaced first."""
        stats: dict[str, SourceStats] = {}
        for row in self.rows():
            for source in row.get("sources", []):
                s = stats.setdefault(source, SourceStats(source=source))
                s.surfaced += 1
                s.score_sum += float(row.get("score", 0.0))
                if row.get("proposed"):
                    s.proposed += 1
        return sorted(stats.values(), key=lambda s: s.surfaced, reverse=True)
