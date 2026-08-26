"""
Portfolio read views -- core layer.

Read-only snapshots of the bot's own state for the cognitive plane: its managed
positions (intent state), HALT status, and the strategy scoreboard. Everything
here comes from local files (StateStore / HaltStore / Scoreboard) -- no broker,
no credentials.

The live broker account is DELIBERATELY not exposed here: the deterministic core
owns the broker, and a read view should never need trading creds. An agent that
wants account equity gets it through a gated path, not this surface.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from src.common.config import load_config
from src.common.logging import AuditLog
from src.core.proposals import ProposalStore
from src.core.state_store import HaltStore, StateStore
from src.execution.order_manager import PositionStatus
from src.research.equity_history import EquityHistory
from src.research.readiness import criteria_from_config, evaluate_readiness
from src.research.scoreboard import Scoreboard


def positions_snapshot(*, state_store: StateStore | None = None) -> dict:
    """Open managed positions (the bot's intent state), as compact rows."""
    state_store = state_store or StateStore()
    rows = []
    for sym, p in state_store.load().items():
        if p.status == PositionStatus.CLOSED:
            continue
        rows.append({
            "symbol": sym,
            "side": p.side.value,
            "qty": p.filled_qty or p.qty,
            "entry": round(getattr(p.ratchet, "entry", 0.0), 2),
            "stop": p.current_stop,
            "strategy": p.strategy,
            "status": p.status.value,
        })
    return {"positions": rows, "count": len(rows)}


def halt_snapshot(*, halt_store: HaltStore | None = None) -> dict:
    """Whether the bot is HALTED, and why."""
    halt_store = halt_store or HaltStore()
    reason = halt_store.is_halted()
    return {"halted": reason is not None, "reason": reason}


def ops_snapshot(*, halt_store: HaltStore | None = None, audit: AuditLog | None = None,
                 proposal_store: ProposalStore | None = None, tail_n: int = 20) -> dict:
    """Operational health in one call: full halt record, when the last cycle
    actually ran (and what it did), recent audit events, and pending proposals.
    Answers "why didn't my bot trade today?" without touching the broker."""
    halt_store = halt_store or HaltStore()
    audit = audit or AuditLog()
    proposal_store = proposal_store or ProposalStore()

    # Search a deeper tail for the last completed cycle; keep the surfaced
    # event list short.
    deep = audit.tail(500)
    last_cycle = next((e for e in reversed(deep) if e.get("event") == "cycle_complete"), None)
    last_halt_event = next((e for e in reversed(deep)
                            if e.get("event") in ("reconcile_mismatch", "kill_switch",
                                                  "stale_data", "cycle_exception")), None)
    pending = proposal_store.list_pending()
    return {
        "halt": halt_store.halt_info(),          # None when not halted
        "last_cycle": last_cycle,                # None if no cycle has ever logged
        "last_halt_event": last_halt_event,
        "recent_events": deep[-max(0, tail_n):],
        "pending_proposals": [
            {"id": p.id, "symbol": p.symbol, "qty": p.approved_qty,
             "strategy": p.strategy, "created": p.created_ts, "expires": p.expiry_ts}
            for p in pending
        ],
    }


def audit_tail(n: int = 20, *, audit: AuditLog | None = None) -> dict:
    """The most recent `n` audit-trail events (oldest first)."""
    return {"events": (audit or AuditLog()).tail(n)}


def scoreboard_snapshot(*, scoreboard: Scoreboard | None = None) -> dict:
    """Ranked strategy scoreboard: validation verdicts + live attribution."""
    scoreboard = scoreboard or Scoreboard()
    return {
        "strategies": [
            {
                "strategy": s.strategy,
                "verdict": s.verdict,
                "num_trades": s.num_trades,
                "sharpe": s.sharpe,
                "psr": s.psr,
                "p_value": s.p_value_adjusted,
                "total_pnl": s.total_pnl,
                "live_num_trades": s.live_num_trades,
                "live_total_pnl": s.live_total_pnl,
            }
            for s in scoreboard.rank()
        ]
    }


def readiness_snapshot(*, equity_history: EquityHistory | None = None) -> dict:
    """Real-capital readiness scorecard: docs/ROADMAP.md Step 7's go/no-go
    bar, computed against the real paper equity track record. Read-only --
    unlike scripts/readiness_report.py, this does NOT append to the
    persisted audit trail (a phone glance shouldn't itself count as a
    recorded decision check-in)."""
    equity_history = equity_history or EquityHistory()
    report = evaluate_readiness(equity_history.load(), criteria_from_config(load_config()))
    return {
        "verdict": report.verdict,
        "track_record_days": report.track_record_days,
        "blocking": report.blocking,
        "checks": [
            {"name": c.name, "passed": c.passed, "actual": c.actual, "threshold": c.threshold}
            for c in report.checks
        ],
    }
