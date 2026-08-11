"""
Aggregate position exposure -- risk layer.

Shared by every caller that builds an AccountState for RiskManager.evaluate()
(the orchestrator, the phone/manual trade path, the discovery pipeline, and
the backtester). Each previously hand-duplicated this loop; a bugfix or
formula change had to land in four places, and they had already started to
drift (one used `filled_qty or qty`, another didn't). One implementation now,
duck-typed against the one shape both the live ManagedPosition
(src/execution/order_manager.py) and the backtester's _Position expose
identically: `.qty` (optionally `.filled_qty`), `.ratchet.entry`,
`.ratchet.stop`. `current_stop` on ManagedPosition is deliberately NOT used
here -- `.ratchet.stop` is kept in lockstep with it by OrderManager.raise_stop
and is the one attribute name that also exists on the backtester's _Position.

The caller filters to positions that are actually open BEFORE calling this --
"open" means something different per caller (a live PositionStatus enum vs.
the backtester deleting closed entries outright the moment they close), so
this function stays agnostic to any particular status representation.

gross_value here is cost-basis (qty * entry price) -- this matches the
orchestrator / trade_service / discovery callers. The backtester tracks gross
exposure as live mark-to-market (qty * current close) instead, a deliberately
different metric for a different purpose; it uses only open_risk_dollars (and
open_count) from this module, not gross_value.

Boundary: pure computation, no I/O, places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ExposureSnapshot:
    gross_value: float        # sum of qty * entry (cost basis) across the given positions
    open_count: int
    open_risk_dollars: float  # sum of qty * |entry - stop|; see AccountState.open_risk_dollars


def compute_exposure(open_positions: Iterable) -> ExposureSnapshot:
    gross = 0.0
    open_risk = 0.0
    count = 0
    for pos in open_positions:
        qty = getattr(pos, "filled_qty", None) or pos.qty
        entry = pos.ratchet.entry
        stop = pos.ratchet.stop
        gross += qty * entry
        open_risk += qty * abs(entry - stop)
        count += 1
    return ExposureSnapshot(gross_value=gross, open_count=count, open_risk_dollars=open_risk)
