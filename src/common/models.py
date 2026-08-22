"""
Typed message contracts -- common layer.

The shared vocabulary that crosses layer boundaries: Bar, Signal, Intent,
RiskDecision. No layer reaches into another layer state; they exchange only
these typed messages. `Intent` is the canonical trade-signal contract and
(de)serializes to/from the JSON wire format documented in docs/CONTRACTS.md.

Boundary: places orders NO, holds credentials NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class Action(str, Enum):
    """The external `signal` label a strategy emits."""

    BUY = "BUY"      # open long
    SHORT = "SHORT"  # open short
    SELL = "SELL"    # close long
    COVER = "COVER"  # close short
    HOLD = "HOLD"


# Which actions open a position, and the side they imply.
_OPENING = {Action.BUY: Side.LONG, Action.SHORT: Side.SHORT}


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    EXPANSION = "expansion"
    NONE = "none"  # dead zone -- stand aside


class Decision(str, Enum):
    APPROVE = "approve"
    VETO = "veto"
    RESIZE = "resize"


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. The atomic unit produced by the data layer."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    """A strategy observation about a symbol. Not yet a sized trade."""

    symbol: str
    strategy: str
    side: Side
    regime: Regime
    confidence: float  # 0.0 - 1.0
    ts: datetime
    reason: str = ""


@dataclass(frozen=True)
class Intent:
    """A proposed trade. The canonical trade-signal contract.

    Travels strategy -> sentiment gate -> risk gate. Carries a *suggested* size
    and the strategy's own price levels; the risk layer sets the final quantity.

    JSON wire format (see docs/CONTRACTS.md):
        {
          "symbol": "AAPL", "signal": "BUY", "confidence": 0.72,
          "strategy": "trend_following", "entry_price": 190.2,
          "stop_loss": 185.0, "take_profit": 205.0
        }
    """

    symbol: str
    strategy: str
    side: Side
    action: Action | None = None
    confidence: float = 0.0
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    suggested_qty: float | None = None
    ts: datetime | None = None
    meta: dict = field(default_factory=dict)

    def validate(self) -> None:
        """Raise ValueError if the intent is internally inconsistent."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if self.entry_price is not None and self.stop_loss is not None:
            if self.side == Side.LONG and self.stop_loss >= self.entry_price:
                raise ValueError("long stop_loss must be below entry_price")
            if self.side == Side.SHORT and self.stop_loss <= self.entry_price:
                raise ValueError("short stop_loss must be above entry_price")
        if self.entry_price is not None and self.take_profit is not None:
            if self.side == Side.LONG and self.take_profit <= self.entry_price:
                raise ValueError("long take_profit must be above entry_price")
            if self.side == Side.SHORT and self.take_profit >= self.entry_price:
                raise ValueError("short take_profit must be below entry_price")

    def to_dict(self) -> dict[str, Any]:
        action = self.action or (Action.BUY if self.side == Side.LONG else Action.SHORT)
        return {
            "symbol": self.symbol,
            "signal": action.value,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Intent:
        required = ("symbol", "signal", "strategy")
        missing = [k for k in required if k not in d]
        if missing:
            raise ValueError(f"trade signal missing required keys: {missing}")
        action = Action(d["signal"])
        if action not in _OPENING:
            raise ValueError(f"signal {action.value!r} does not open a position")
        intent = cls(
            symbol=d["symbol"],
            strategy=d["strategy"],
            side=_OPENING[action],
            action=action,
            confidence=float(d.get("confidence", 0.0)),
            entry_price=_optf(d, "entry_price"),
            stop_loss=_optf(d, "stop_loss"),
            take_profit=_optf(d, "take_profit"),
        )
        intent.validate()
        return intent


def _optf(d: dict[str, Any], key: str) -> float | None:
    return float(d[key]) if d.get(key) is not None else None


@dataclass(frozen=True)
class RiskDecision:
    """The gatekeeper verdict on an Intent. The only path to execution."""

    intent: Intent
    decision: Decision
    approved_qty: float = 0.0
    reason: str = ""
