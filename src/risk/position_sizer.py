"""
Position sizer -- risk layer.

Risk-based sizing: shares are chosen so that hitting the stop loses no more than
`per_trade_risk_pct` of equity, then capped by the max-position limit and
available buying power. Pure, deterministic, no I/O.

Boundary: pure logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    qty: int
    risk_dollars: float
    capped_by: str  # "risk" | "max_position" | "buying_power" | "none"


def size_position(
    equity: float,
    entry_price: float,
    stop_price: float,
    per_trade_risk_pct: float,
    max_position_pct: float,
    buying_power: float | None = None,
) -> SizingResult:
    """Return whole-share quantity for a trade.

    Sizing is the minimum of three caps:
      1. risk cap   -- (equity * risk%) / |entry - stop|
      2. position   -- (equity * max_position%) / entry
      3. buying pwr -- buying_power / entry  (if provided)
    """
    if entry_price <= 0:
        return SizingResult(0, 0.0, "none")

    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        # No defined risk distance -> refuse to size (avoid div-by-zero blowups).
        return SizingResult(0, 0.0, "risk")

    risk_dollars = equity * (per_trade_risk_pct / 100.0)
    risk_shares = risk_dollars / risk_per_share

    max_position_value = equity * (max_position_pct / 100.0)
    position_shares = max_position_value / entry_price

    caps = {"risk": risk_shares, "max_position": position_shares}
    if buying_power is not None:
        caps["buying_power"] = buying_power / entry_price

    capped_by = min(caps, key=caps.get)
    qty = int(math.floor(caps[capped_by]))
    if qty <= 0:
        return SizingResult(0, risk_dollars, capped_by)
    return SizingResult(qty, risk_dollars, capped_by)
