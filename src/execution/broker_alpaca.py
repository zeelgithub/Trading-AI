"""
Alpaca broker client -- execution layer.

The ONLY module permitted to place, replace, or cancel orders. Holds trading
credentials (via secrets.load_trading_credentials, called nowhere else). Wraps
the Alpaca trading API behind the BrokerInterface so the order manager and
reconciler depend on an abstraction (and stay unit-testable with a fake).

Entries are NEVER naked: `submit_protected_entry` attaches the protective stop
atomically (OTO), plus a take-profit leg (bracket/OTOCO) when one is given, so
the stop and target are a broker-linked OCO that can never both fill.

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
    legs: tuple = ()


@dataclass(frozen=True)
class AccountView:
    equity: float
    last_equity: float
    buying_power: float
    daytrade_count: int
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
    def submit_protected_entry(
        self, symbol: str, qty: float, side: Side, stop_price: float,
        take_profit_price: float | None, client_order_id: str,
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
            daytrade_count=int(a.daytrade_count),
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

    def submit_protected_entry(
        self, symbol, qty, side: Side, stop_price, take_profit_price, client_order_id
    ) -> OrderView:
        """Market entry with an atomically-attached protective stop (OTO), plus a
        take-profit leg (bracket) when `take_profit_price` is given."""
        from alpaca.trading.enums import OrderClass, TimeInForce
        from alpaca.trading.requests import (
            MarketOrderRequest, StopLossRequest, TakeProfitRequest,
        )

        common = dict(
            symbol=symbol,
            qty=qty,
            side=self._alpaca_side(side),
            # GTC, not DAY: bracket/OTO legs inherit the parent's TIF, and a DAY
            # stop leg EXPIRES at the close -- leaving a swing position naked
            # the next morning (exactly the UNPROTECTED halt seen 2026-06-19).
            time_in_force=TimeInForce.GTC,
            client_order_id=client_order_id,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        if take_profit_price is not None:
            req = MarketOrderRequest(
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
                **common,
            )
        else:
            req = MarketOrderRequest(order_class=OrderClass.OTO, **common)
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
            legs=legs,
        )
