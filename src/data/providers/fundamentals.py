"""
Fundamentals provider -- data layer.

Free, coarse company-quality snapshot via yfinance (unofficial Yahoo data). Used
as a *quality lens* for ranking -- "is this a profitable, growing, real-sized
company?" -- NOT precise valuation. yfinance is rate-limited and occasionally
returns partial data, so every field is optional and failures degrade to "no
data" (the source then simply withholds its vote).

Boundary: read-only; places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    profitable: bool | None = None
    revenue_growth: float | None = None    # fraction, e.g. 0.22 == +22% YoY
    market_cap: float | None = None
    trailing_pe: float | None = None

    @property
    def has_data(self) -> bool:
        return self.profitable is not None or self.revenue_growth is not None


@runtime_checkable
class FundamentalsProvider(Protocol):
    def fetch(self, symbol: str) -> Fundamentals:
        ...


class YFinanceFundamentals:
    """Reads a small slice of yfinance's `.info`. Lazily imported and defensive."""

    def fetch(self, symbol: str) -> Fundamentals:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - env guard
            raise RuntimeError("yfinance is not installed. Run: pip install yfinance") from exc
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            return Fundamentals(symbol=symbol.upper())

        profitable = None
        margin = info.get("profitMargins")
        eps = info.get("trailingEps")
        if margin is not None:
            profitable = float(margin) > 0
        elif eps is not None:
            profitable = float(eps) > 0

        return Fundamentals(
            symbol=symbol.upper(),
            profitable=profitable,
            revenue_growth=_optf(info.get("revenueGrowth")),
            market_cap=_optf(info.get("marketCap")),
            trailing_pe=_optf(info.get("trailingPE")),
        )


def _optf(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
