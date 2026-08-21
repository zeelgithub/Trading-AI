"""
Alpaca broker client -- execution layer.

The ONLY module permitted to place, replace, or cancel orders. Holds trading
credentials (via secrets.load_trading_credentials, called nowhere else). Wraps
the Alpaca trading API behind the BrokerInterface so the order manager and
reconciler depend on an abstraction (and stay unit-testable with a fake).

Entries are NEVER naked, but NOT via an atomic OTO/bracket -- Alpaca does not
honor a requested GTC time-in-force on OTO/bracket child legs (confirmed
against real account order history: a GTC-requested entry+stop came back from
Alpaca as DAY on both the parent and the stop leg, which then silently expired
at the next close; matches Alpaca's own community forum on this exact
limitation -- see docs/SAFEGUARDS.md "Stop order type"). Instead: submit the
market entry alone (`submit_market_entry`), confirm the fill, THEN submit the
protective stop as its own standalone GTC order (`submit_stop`), or a
standalone GTC OCO (`submit_oco_exit`) when there's a take-profit too -- an OCO
is Alpaca's documented pattern for a persistent protective exit outside the
bracket structure. `src/execution/order_manager.py` sequences this: the stop
is attached the moment a fill is confirmed, before the position is ever
considered OPEN.

Boundary: places orders YES, holds trading credentials YES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.common.models import Side
from src.common.secrets import TradingCredentials, load_trading_credentials


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: float
    side: Side
    avg_entry_price: float


@dataclass(frozen=True)
class OrderView:
    id: str
    client_order_id: str
    symbol: str
    qty: float
    side: str
    type: str
    status: str
    stop_price: float | None = None
    limit_price: float | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    legs: tuple = ()


@dataclass(frozen=True)
class AccountView:
    equity: float
    last_equity: float
    buying_power: float
    # Optional to match alpaca-py's own TradeAccount.daytrade_count typing --
    # Alpaca can genuinely omit this (e.g. before it's been computed for a
    # fresh account). AccountState.day_trade_count downstream (risk_manager.py)
    # already treats None as "no broker reconciliation this cycle, trust the
    # local PDT tracker" -- forcing an int() cast here used to crash the WHOLE
    # cycle on a None instead of falling through to that already-safe path.
    daytrade_count: int | None
    pattern_day_trader: bool


@dataclass(frozen=True)
class AssetView:
    """One tradable listing from the broker's asset catalog (read-only)."""

    symbol: str
    name: str
    tradable: bool


class BrokerInterface(Protocol):
    """The minimal surface the execution layer needs. AlpacaBroker implements
    it; tests provide a fake."""

    def get_account(self) -> AccountView: ...
    def is_market_open(self) -> bool: ...
    def list_assets(self) -> list[AssetView]: ...
    def list_positions(self) -> list[PositionView]: ...
    def list_open_orders(self) -> list[OrderView]: ...
    def get_order(self, order_id: str) -> OrderView: ...
    def submit_market_entry(
        self, symbol: str, qty: float, side: Side, client_order_id: str,
    ) -> OrderView: ...
    def submit_stop(
        self, symbol: str, qty: float, side: Side, stop_price: float, client_order_id: str,
    ) -> OrderView: ...
    def submit_oco_exit(
        self, symbol: str, qty: float, side: Side, stop_price: float,
        take_profit_price: float, client_order_id: str,
    ) -> OrderView: ...
    def replace_stop(self, order_id: str, stop_price: float) -> OrderView: ...
    def cancel_order(self, order_id: str) -> None: ...
    def close_position(self, symbol: str) -> None: ...


# Exit side is the opposite of the position side.
def exit_side(position_side: Side) -> Side:
    return Side.SHORT if position_side == Side.LONG else Side.LONG


class AlpacaBroker:
    """Concrete BrokerInterface backed by alpaca-py's TradingClient.

    The client is constructed lazily so importing this module never requires
    alpaca-py or live credentials.
    """

    def __init__(self, creds: TradingCredentials | None = None, paper: bool = True) -> None:
        self._creds = creds or load_trading_credentials()
        self._paper = paper
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from alpaca.trading.client import TradingClient
            except ImportError as exc:  # pragma: no cover - env guard
                raise RuntimeError("alpaca-py is not installed. Run: pip install alpaca-py") from exc
            self._client = TradingClient(
                api_key=self._creds.key_id,
                secret_key=self._creds.secret_key,
                paper=self._paper,
            )
        return self._client

    @staticmethod
    def _alpaca_side(side: Side):
        from alpaca.trading.enums import OrderSide

        return OrderSide.BUY if side == Side.LONG else OrderSide.SELL

    def get_account(self) -> AccountView:
        a = self._get_client().get_account()
        return AccountView(
            equity=float(a.equity),
            last_equity=float(a.last_equity),
            buying_power=float(a.buying_power),
            daytrade_count=int(a.daytrade_count) if a.daytrade_count is not None else None,
            pattern_day_trader=bool(a.pattern_day_trader),
        )

    def is_market_open(self) -> bool:
        return bool(self._get_client().get_clock().is_open)

    def list_assets(self) -> list[AssetView]:
        """Active US-equity listings (symbol + company name). Read-only; feeds
        the symbol resolver so 'buy palantir' works without a hardcoded table."""
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        return [
            AssetView(symbol=str(a.symbol), name=str(a.name or ""), tradable=bool(a.tradable))
            for a in self._get_client().get_all_assets(req)
        ]

    def list_positions(self) -> list[PositionView]:
        out = []
        for p in self._get_client().get_all_positions():
            qty = float(p.qty)
            out.append(
                PositionView(
                    symbol=p.symbol,
                    qty=abs(qty),
                    side=Side.LONG if qty >= 0 else Side.SHORT,
                    avg_entry_price=float(p.avg_entry_price),
                )
            )
        return out

    def list_open_orders(self) -> list[OrderView]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
        return [self._to_order_view(o) for o in self._get_client().get_orders(req)]

    def get_order(self, order_id: str) -> OrderView:
        return self._to_order_view(self._get_client().get_order_by_id(order_id))

    def submit_market_entry(self, symbol, qty, side: Side, client_order_id) -> OrderView:
        """Plain market entry, no attached legs. DAY is fine here -- a market
        order fills immediately during market hours or not at all, so TIF on
        the entry itself was never the source of the naked-position bug (see
        module docstring). The protective stop is submitted separately, once
        the fill is confirmed: `submit_stop` / `submit_oco_exit`."""
        from alpaca.trading.enums import TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=self._alpaca_side(side),
            time_in_force=TimeInForce.DAY, client_order_id=client_order_id,
        )
        return self._to_order_view(self._get_client().submit_order(req))

    def submit_stop(self, symbol, qty, side: Side, stop_price, client_order_id) -> OrderView:
        """Standalone GTC protective stop for an ALREADY-FILLED position.
        NOT attached to any entry -- an independent top-level order, so it
        isn't subject to Alpaca's OTO/bracket per-leg time-in-force
        limitation (see module docstring). `side` is the POSITION's side;
        the stop exits it, so the order itself trades the opposite side."""
        from alpaca.trading.enums import TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        req = StopOrderRequest(
            symbol=symbol, qty=qty, side=self._alpaca_side(exit_side(side)),
            time_in_force=TimeInForce.GTC, stop_price=round(stop_price, 2),
            client_order_id=client_order_id,
        )
        return self._to_order_view(self._get_client().submit_order(req))

    def submit_oco_exit(
        self, symbol, qty, side: Side, stop_price, take_profit_price, client_order_id
    ) -> OrderView:
        """Standalone GTC OCO (stop + take-profit; one fill cancels the other)
        for an already-filled position -- Alpaca's own documented pattern for
        a persistent protective exit outside the bracket/OTO structure."""
        from alpaca.trading.enums import OrderClass, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest, StopLossRequest, TakeProfitRequest,
        )

        req = LimitOrderRequest(
            symbol=symbol, qty=qty, side=self._alpaca_side(exit_side(side)),
            time_in_force=TimeInForce.GTC, order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            client_order_id=client_order_id,
        )
        return self._to_order_view(self._get_client().submit_order(req))

    def replace_stop(self, order_id: str, stop_price: float) -> OrderView:
        from alpaca.trading.requests import ReplaceOrderRequest

        req = ReplaceOrderRequest(stop_price=round(stop_price, 2))
        return self._to_order_view(self._get_client().replace_order_by_id(order_id, req))

    def cancel_order(self, order_id: str) -> None:
        self._get_client().cancel_order_by_id(order_id)

    def close_position(self, symbol: str) -> None:
        """Liquidate the position AND cancel its related open orders (Alpaca does
        both for DELETE /v2/positions/{symbol})."""
        self._get_client().close_position(symbol)

    @classmethod
    def _to_order_view(cls, o) -> OrderView:
        legs = tuple(cls._to_order_view(leg) for leg in (getattr(o, "legs", None) or []))
        return OrderView(
            id=str(o.id),
            client_order_id=str(o.client_order_id),
            symbol=o.symbol,
            qty=float(o.qty),
            side=str(o.side.value if hasattr(o.side, "value") else o.side),
            type=str(o.type.value if hasattr(o.type, "value") else o.type),
            status=str(o.status.value if hasattr(o.status, "value") else o.status),
            stop_price=float(o.stop_price) if getattr(o, "stop_price", None) else None,
            limit_price=float(o.limit_price) if getattr(o, "limit_price", None) else None,
            filled_qty=float(getattr(o, "filled_qty", 0) or 0),
            filled_avg_price=(
                float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) else None
            ),
            legs=legs,
        )


class AlpacaAccountReader:
    """Read-only account/positions snapshot for callers OUTSIDE src/execution/
    (paper-shadow scans, discovery, self-heal connectivity probes, the
    congress-copy shadow trader). Alpaca ties account/position reads to the
    trading API key -- there's no separate read-only key type -- so this still
    goes through `load_trading_credentials`, but wraps `AlpacaBroker` by
    composition rather than exposing it: no `submit_market_entry`,
    `submit_stop`, `submit_oco_exit`, `replace_stop`, `cancel_order`, or
    `close_position` is reachable from this object, so a caller that only
    ever needed a read can never accidentally (or via a future edit) place,
    modify, or cancel an order.

    Boundary: places orders NO, holds trading credentials YES (read-only use).
    """

    def __init__(self, creds: TradingCredentials | None = None, paper: bool = True) -> None:
        self._broker = AlpacaBroker(creds=creds, paper=paper)

    def get_account(self) -> AccountView:
        return self._broker.get_account()

    def list_positions(self) -> list[PositionView]:
        return self._broker.list_positions()
