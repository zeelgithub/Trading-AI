"""
Reconciler -- execution layer.

Broker is the source of truth. Compares the bot's internal managed positions
against what Alpaca reports, and is PENDING-AWARE so a just-submitted (not yet
filled) entry does not trigger a false halt:

  - PENDING_ENTRY, no broker position yet -> expect a working entry order; if
    neither exists the order vanished -> mismatch.
  - PENDING_ENTRY, but a broker position ALREADY exists -> it's actually
    filled, whatever the bot's own bookkeeping says (a crash between fill and
    settle() can leave this stale indefinitely) -> held to the SAME
    protection standard as OPEN below, not skipped. This is exactly the gap
    that let real, filled, unprotected positions go undetected for weeks: the
    reconciler only checked broker-position EXISTENCE for a pending entry,
    never whether it was protected once it turned out to be real.
  - OPEN / PENDING_EXIT -> expect a broker position with matching qty AND a
    resting protective stop; otherwise mismatch / UNPROTECTED.
  - A broker position the bot does not track at all -> unknown -> halt.

Boundary: read-compare only; can trigger HALT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.errors import retry_transient
from src.common.models import Side
from src.execution.broker_alpaca import BrokerInterface, is_stop_order
from src.execution.order_manager import PositionStatus

QTY_TOLERANCE = 1e-6


@dataclass
class ReconcileReport:
    ok: bool
    quantity_mismatches: list[str] = field(default_factory=list)
    unknown_positions: list[str] = field(default_factory=list)
    unprotected_positions: list[str] = field(default_factory=list)
    pending_lost: list[str] = field(default_factory=list)
    auto_closed: list[str] = field(default_factory=list)  # stop fired / closed at broker

    def summary(self) -> str:
        parts = []
        if self.auto_closed:
            parts.append(f"auto-closed (stop fired): {self.auto_closed}")
        if self.ok and not parts:
            return "reconciled: internal state matches broker"
        if self.quantity_mismatches:
            parts.append(f"qty mismatch: {self.quantity_mismatches}")
        if self.unknown_positions:
            parts.append(f"unknown positions: {self.unknown_positions}")
        if self.unprotected_positions:
            parts.append(f"UNPROTECTED: {self.unprotected_positions}")
        if self.pending_lost:
            parts.append(f"pending lost: {self.pending_lost}")
        return "; ".join(parts)


class Reconciler:
    def __init__(self, broker: BrokerInterface) -> None:
        self.broker = broker

    def reconcile(self, positions: dict) -> ReconcileReport:
        """`positions` maps symbol -> ManagedPosition (duck-typed: needs
        .status, .side, .qty, .filled_qty)."""
        # Read-only calls: safe to retry on a transient network blip instead of
        # halting the whole cycle over one dropped connection.
        broker_positions = {p.symbol: p for p in retry_transient(self.broker.list_positions)}
        open_orders = retry_transient(self.broker.list_open_orders)
        order_symbols = {o.symbol for o in open_orders}
        stop_symbols = {o.symbol for o in open_orders if is_stop_order(o.type)}

        report = ReconcileReport(ok=True)
        tracked: set[str] = set()

        for symbol, pos in positions.items():
            if pos.status == PositionStatus.CLOSED:
                continue
            tracked.add(symbol)

            if pos.status == PositionStatus.PENDING_ENTRY:
                if symbol not in order_symbols and symbol not in broker_positions:
                    report.pending_lost.append(symbol)
                elif symbol in broker_positions and symbol not in stop_symbols:
                    # Already a real, filled broker position -- exposed to the
                    # same risk as an OPEN one, so it doesn't get a pass just
                    # because local bookkeeping hasn't caught up yet.
                    report.unprotected_positions.append(symbol)
                continue

            # OPEN / PENDING_EXIT -> must be a real, protected broker position.
            bp = broker_positions.get(symbol)
            if bp is None:
                # Position is gone from broker. If there is also no open order,
                # the protective stop (or a manual close) fired and filled — that
                # is expected and should not halt the bot. Only flag as a true
                # mismatch when an order exists but the position has vanished
                # (genuinely unexpected broker state).
                if symbol in order_symbols:
                    report.quantity_mismatches.append(symbol)
                else:
                    report.auto_closed.append(symbol)
                continue
            expected_qty = pos.filled_qty or pos.qty
            # Compare SIGNED size so a short at the broker never reconciles
            # against a long the bot thinks it holds (equal |qty| is not enough).
            expected_signed = expected_qty if pos.side == Side.LONG else -expected_qty
            if abs(self._signed(bp) - expected_signed) > QTY_TOLERANCE:
                report.quantity_mismatches.append(symbol)
            if symbol not in stop_symbols:
                report.unprotected_positions.append(symbol)

        # Broker positions the bot does not track at all.
        for symbol in broker_positions:
            if symbol not in tracked:
                report.unknown_positions.append(symbol)

        report.ok = not (
            report.quantity_mismatches
            or report.unknown_positions
            or report.unprotected_positions
            or report.pending_lost
            # auto_closed is expected (stop fired) — does not block the cycle
        )
        return report

    @staticmethod
    def _signed(p) -> float:
        return p.qty if p.side == Side.LONG else -p.qty
