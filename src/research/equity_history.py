"""
Daily equity/P&L history -- research layer.

Neither `state/scoreboard.json` (a running CUMULATIVE per-strategy total,
overwritten each update) nor `logs/audit.jsonl` (every individual event, not
a daily summary) can answer "how has my equity moved day by day" without
manual reconstruction. This module is the missing piece: one row per
CALENDAR DATE (not per cycle -- a date already tracked gets its row replaced,
not duplicated, so running multiple manual cycles the same day doesn't skew
the series), recording equity, that day's P&L, open position count, and
whether the cycle halted (and why) -- so a halt/kill-switch day shows up in
the history as clearly as a profitable one.

Boundary: places orders NO, touches no live trading decision -- purely an
observability record of what already happened.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "state" / "equity_history.json"


@dataclass
class EquityPoint:
    date: str            # ISO date, e.g. "2026-08-24"
    equity: float
    day_pnl: float        # equity - start_of_day_equity, this cycle
    open_positions: int
    halted: bool = False
    halt_reason: str | None = None


class EquityHistory:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)

    def load(self) -> list[EquityPoint]:
        payload, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("equity_history").error(
                "equity history corrupt; moved to %s -- starting empty", quarantined)
            return []
        rows = payload or []
        return [EquityPoint(**r) for r in rows]

    def record(
        self, equity: float, day_pnl: float, open_positions: int,
        halted: bool = False, halt_reason: str | None = None,
        as_of: date | None = None,
    ) -> None:
        """Upsert today's row -- replaces any existing entry for the same
        date rather than appending a duplicate, so re-running a cycle
        (a manual test, or a retried scheduled run) doesn't distort the
        series with multiple points per day."""
        today = (as_of or date.today()).isoformat()
        points = self.load()
        points = [p for p in points if p.date != today]
        points.append(EquityPoint(
            date=today, equity=equity, day_pnl=day_pnl,
            open_positions=open_positions, halted=halted, halt_reason=halt_reason,
        ))
        points.sort(key=lambda p: p.date)
        atomic_write_json(self.path, [asdict(p) for p in points])
