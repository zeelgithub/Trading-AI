"""
Order manager -- execution layer.

Acts ONLY on intents already approved by the risk gate. Opens a position with a
single broker call that attaches the protective stop atomically (and a
take-profit leg when the intent has one) -- so a position is NEVER naked and the
stop/target are a broker OCO that can't both fill. Raises the resting stop as
price advances (never lowers it). Settles fills (incl. partial). Closes via the
broker's position-close (which also cancels the linked legs).

Boundary: submits via the BrokerInterface only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.common.errors import retry_transient
from src.common.models import Decision, RiskDecision, Side
from src.execution.broker_alpaca import BrokerInterface


class PositionStatus(str, Enum):
    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    PENDING_EXIT = "pending_exit"
    CLOSED = "closed"


# Terminal broker statuses that mean "this order will never fill." Distinct
# from PENDING (still working) and from a real fill. Without checking for
# these, a rejected/canceled/expired entry sat in PENDING_ENTRY until the NEXT
# reconcile pass caught it as "pending_lost" -- a real halt, but with the
# broker's actual rejection reason never captured anywhere in the audit trail.
DEAD_ORDER_STATUSES = frozenset({"rejected", "canceled", "expired", "suspended", "stopped"})


@dataclass
class ManagedPosition:
    symbol: str
    side: Side
    qty: float                 # ordered quantity
    strategy: str
    entry_order_id: str
    stop_order_id: str
    current_stop: float
    ratchet: object            # PercentRatchet | AtrRatchet (has .update/.stop)
    status: PositionStatus = PositionStatus.PENDING_ENTRY
    filled_qty: float = 0.0
    tp_order_id: str | None = None
    take_profit: float | None = None
    last_order_status: str | None = None  # broker's raw status, for diagnostics


def is_dead_entry(position: "ManagedPosition") -> bool:
    """True when a PENDING_ENTRY settled to CLOSED with zero fill -- the
    broker rejected/canceled/expired it rather than it ever opening."""
    return (position.status == PositionStatus.CLOSED
            and not position.filled_qty
            and position.last_order_status in DEAD_ORDER_STATUSES)


def _coid(symbol: str, strategy: str, tag: str, kind: str) -> str:
    """Deterministic client_order_id so identical retries are idempotent."""
    return f"{symbol}-{strategy}-{tag}-{kind}"


def realized_pnl(side: Side, entry: float, qty: float, exit_price: float) -> float:
    """Signed PnL of closing `qty` shares at `exit_price` from `entry`."""
    return (exit_price - entry) * qty if side == Side.LONG else (entry - exit_price) * qty


class OrderManager:
    def __init__(self, broker: BrokerInterface) -> None:
        self.broker = broker

    def open_position(self, decision: RiskDecision, ratchet, tag: str) -> ManagedPosition:
        """Open a position from an APPROVED/RESIZED decision plus its ratchet.

        Places one protected entry (entry + stop leg, + take-profit leg if the
        intent carries one). `tag` is a per-trade idempotency token.
        """
        if decision.decision not in (Decision.APPROVE, Decision.RESIZE):
            raise ValueError(f"cannot open position on a {decision.decision.value} decision")
        qty = decision.approved_qty
        if qty <= 0:
            raise ValueError("approved_qty must be > 0 to open a position")

        intent = decision.intent
        side = intent.side
        symbol = intent.symbol
        stop_level = ratchet.stop

        # Deliberately NOT wrapped in retry_transient: if the request reached
        # the broker but the response was lost to the network blip, a blind
        # retry could submit a second entry. `_coid` makes retries idempotent
        # at the broker (duplicate client_order_id is rejected, not
        # duplicated), but that rejection isn't a transient status either, so
        # it would still surface as an error here -- default-to-halt (rule 3)
        # and a human/reconciler confirming what actually happened at the
        # broker is safer than guessing.
        parent = self.broker.submit_protected_entry(
            symbol, qty, side, stop_level, intent.take_profit,
            _coid(symbol, intent.strategy, tag, "entry"),
        )

        stop_id, tp_id = self._leg_ids(parent)
        if stop_id is None:
            # No naked positions: if the protective stop did not attach, refuse.
            raise RuntimeError(f"protected entry for {symbol} returned no stop leg")

        return ManagedPosition(
            symbol=symbol,
            side=side,
            qty=qty,
            strategy=intent.strategy,
            entry_order_id=parent.id,
            stop_order_id=stop_id,
            current_stop=stop_level,
            ratchet=ratchet,
            status=PositionStatus.PENDING_ENTRY,
            filled_qty=0.0,
            tp_order_id=tp_id,
            take_profit=intent.take_profit,
        )

    def settle(self, position: ManagedPosition) -> PositionStatus:
        """Refresh fill state from the broker. Acts on filled_qty, not ordered.

        A dead order (rejected/canceled/expired/...) with zero fill settles to
        CLOSED rather than sitting in PENDING_ENTRY -- `is_dead_entry()` lets
        the caller tell this apart from a real close and audit the reason
        immediately, instead of waiting for the next reconcile pass to notice
        the order vanished and halt on an unexplained "pending_lost".
        """
        order = retry_transient(lambda: self.broker.get_order(position.entry_order_id))
        position.filled_qty = order.filled_qty
        position.last_order_status = order.status
        if order.filled_qty and order.filled_qty > 0:
            position.status = PositionStatus.OPEN
        elif order.status in DEAD_ORDER_STATUSES:
            position.status = PositionStatus.CLOSED
        return position.status

    def raise_stop(self, position: ManagedPosition, price: float, atr: float | None = None) -> bool:
        """If the ratchet advances the stop, replace the resting stop leg. Only
        ever moves to a MORE protective level. Returns True if moved."""
        new_stop = position.ratchet.update(price, atr)
        more_protective = (
            new_stop > position.current_stop
            if position.side == Side.LONG
            else new_stop < position.current_stop
        )
        if not more_protective:
            return False
        updated = self.broker.replace_stop(position.stop_order_id, new_stop)
        position.stop_order_id = updated.id
        position.current_stop = new_stop
        return True

    def close(self, position: ManagedPosition, reason: str = "signal") -> ManagedPosition:
        """Liquidate via the broker (also cancels the linked stop/TP legs)."""
        self.broker.close_position(position.symbol)
        position.status = PositionStatus.CLOSED
        return position

    @staticmethod
    def _leg_ids(parent) -> tuple[str | None, str | None]:
        stop_id = tp_id = None
        for leg in parent.legs:
            if "stop" in leg.type:
                stop_id = leg.id
            elif leg.type in ("limit", "take_profit"):
                tp_id = leg.id
        return stop_id, tp_id
