"""
Reconciler -- execution layer.

Broker is the source of truth. Compares the bot's internal managed positions
against what Alpaca reports, and is PENDING-AWARE so a just-submitted (not yet
filled) entry does not trigger a false halt:

  - PENDING_ENTRY  -> expect an open entry order OR a broker position; if neither
    exists the order vanished -> mismatch.
  - OPEN / PENDING_EXIT -> expect a broker position with matching qty AND a
    resting protective stop; otherwise mismatch / UNPROTECTED.
  - A broker position the bot does not track at all -> unknown -> halt.

Boundary: read-compare only; can trigger HALT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.models import Side
from src.execution.broker_alpaca import BrokerInterface
from src.execution.order_manager import PositionStatus

QTY_TOLERANCE = 1e-6


@dataclass
class ReconcileReport:
    ok: bool
    quantity_mismatches: list[str] = field(default_factory=list)
    unknown_positions: list[str] = field(default_factory=list)
    unprotected_positions: list[str] = field(default_factory=list)
    pending_lost: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "reconciled: internal state matches broker"
        parts = []
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
        broker_positions = {p.symbol: p for p in self.broker.list_positions()}
        open_orders = self.broker.list_open_orders()
        order_symbols = {o.symbol for o in open_orders}
        stop_symbols = {o.symbol for o in open_orders if "stop" in o.type}

        report = ReconcileReport(ok=True)
        tracked: set[str] = set()

        for symbol, pos in positions.items():
            if pos.status == PositionStatus.CLOSED:
                continue
            tracked.add(symbol)

            if pos.status == PositionStatus.PENDING_ENTRY:
                # Fine as long as the entry order is still working or it filled.
                if symbol not in order_symbols and symbol not in broker_positions:
                    report.pending_lost.append(symbol)
                continue

            # OPEN / PENDING_EXIT -> must be a real, protected broker position.
            bp = broker_positions.get(symbol)
            if bp is None:
                report.quantity_mismatches.append(symbol)
                continue
            expected_qty = pos.filled_qty or pos.qty
            if abs(bp.qty - expected_qty) > QTY_TOLERANCE:
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
        )
        return report

    @staticmethod
    def _signed(p) -> float:
        return p.qty if p.side == Side.LONG else -p.qty
