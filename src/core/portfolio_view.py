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

from src.core.state_store import HaltStore, StateStore
from src.execution.order_manager import PositionStatus
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
