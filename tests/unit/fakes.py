"""In-memory fake broker implementing the BrokerInterface for execution tests.

Simulates protected entries (OTO/bracket), fills (full or partial), resting-stop
replacement, and position-close (which cancels linked legs). `auto_fill=True`
fills the market entry immediately and reflects it as a broker position.
"""

from __future__ import annotations

from dataclasses import replace

from src.common.models import Side
from src.execution.broker_alpaca import AccountView, AssetView, OrderView, PositionView

DEFAULT_ASSETS = [
    AssetView("AAPL", "Apple Inc. Common Stock", True),
    AssetView("TSLA", "Tesla, Inc. Common Stock", True),
    AssetView("NVDA", "NVIDIA Corporation Common Stock", True),
    AssetView("RKLB", "Rocket Lab USA, Inc.", True),
    AssetView("BAC", "Bank of America Corporation", True),
    AssetView("BK", "The Bank of New York Mellon Corporation", True),
    AssetView("HALT", "Halted Holdings", False),  # not tradable
]


class FakeBroker:
    def __init__(
        self,
        positions: list[PositionView] | None = None,
        account: AccountView | None = None,
        auto_fill: bool = True,
        market_open: bool = True,
        reject_entries: bool = False,
        fill_price: float | None = None,
    ) -> None:
        self.positions = list(positions or [])
        self._orders: dict[str, OrderView] = {}
        self.replaced: list[tuple[str, float]] = []
        self.canceled: list[str] = []
        self.closed: list[str] = []
        self._account = account or AccountView(50000, 50000, 200000, 0, False)
        self.auto_fill = auto_fill
        self._market_open = market_open
        # reject_entries: simulate the broker rejecting every submitted entry
        # (accepted-then-rejected is the realistic Alpaca timing; this fake
        # collapses it to immediate for simplicity, which OrderManager.settle
        # can't distinguish from a delayed rejection anyway).
        self.reject_entries = reject_entries
        # fill_price: the position's avg_entry_price on a simulated fill. Set
        # this explicitly in any test that asserts on entry price -- without
        # it, avg_entry_price falls back to stop_price (a real fill price and
        # a 10%-below-entry stop price are NOT the same number; the fallback
        # exists only so tests that don't care about entry price still get
        # some float, not a crash).
        self.fill_price = fill_price
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"ord-{self._seq}"

    def seed_order(self, order: OrderView) -> None:
        self._orders[order.id] = order

    # --- BrokerInterface ---
    def get_account(self) -> AccountView:
        return self._account

    def is_market_open(self) -> bool:
        return self._market_open

    def list_assets(self) -> list[AssetView]:
        return list(DEFAULT_ASSETS)

    def list_positions(self) -> list[PositionView]:
        return list(self.positions)

    def list_open_orders(self) -> list[OrderView]:
        return [o for o in self._orders.values() if o.status in ("new", "accepted", "held")]

    def get_order(self, order_id: str) -> OrderView:
        return self._orders[order_id]

    def submit_protected_entry(self, symbol, qty, side: Side, stop_price, take_profit_price, client_order_id):
        leg_side = "sell" if side == Side.LONG else "buy"
        stop_leg = OrderView(
            id=self._next_id(), client_order_id=client_order_id + "-sl", symbol=symbol,
            qty=qty, side=leg_side, type="stop", status="held", stop_price=round(stop_price, 2),
        )
        legs = [stop_leg]
        if take_profit_price is not None:
            legs.append(OrderView(
                id=self._next_id(), client_order_id=client_order_id + "-tp", symbol=symbol,
                qty=qty, side=leg_side, type="limit", status="held",
                limit_price=round(take_profit_price, 2),
            ))
        filled = self.auto_fill and not self.reject_entries
        entry = OrderView(
            id=self._next_id(), client_order_id=client_order_id, symbol=symbol, qty=qty,
            side="buy" if side == Side.LONG else "sell", type="market",
            status="rejected" if self.reject_entries else ("filled" if filled else "accepted"),
            filled_qty=qty if filled else 0.0, legs=tuple(legs),
        )
        self._orders[entry.id] = entry
        for leg in legs:
            self._orders[leg.id] = leg
        if filled:
            price = self.fill_price if self.fill_price is not None else stop_price
            self.positions.append(PositionView(symbol, qty, side, avg_entry_price=round(price, 2)))
        return entry

    def replace_stop(self, order_id, stop_price):
        o = self._orders.pop(order_id)
        updated = replace(o, id=self._next_id(), stop_price=round(stop_price, 2))
        self._orders[updated.id] = updated
        self.replaced.append((order_id, round(stop_price, 2)))
        return updated

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        self._orders.pop(order_id, None)

    def close_position(self, symbol):
        self.closed.append(symbol)
        self.positions = [p for p in self.positions if p.symbol != symbol]
        for oid in [oid for oid, o in self._orders.items() if o.symbol == symbol]:
            self._orders.pop(oid, None)

    # --- test helper ---
    def set_fill(self, order_id: str, filled_qty: float, status: str = "filled") -> None:
        self._orders[order_id] = replace(self._orders[order_id], filled_qty=filled_qty, status=status)
