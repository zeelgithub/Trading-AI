"""
Strategy interface -- strategy layer.

Abstract base every strategy implements. A strategy consumes a feature frame
(causal indicator columns from src/data/features.py) and emits at most one
typed Intent for the latest bar -- never an order. Stop levels come from the
per-strategy ratchet params in risk_limits.yaml so the emitted Intent is fully
specified for the risk gate.

Boundary: places orders NO, holds credentials NO.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from src.common.config import Config
from src.common.models import Intent, Side


class Strategy(ABC):
    name: str = "base"

    def __init__(self, config: Config) -> None:
        self.config = config
        self.params = config.strategies.get("strategies", {}).get(self.name, {})
        self.ratchet_params = config.risk_limits.get("ratchet_stop", {}).get(self.name, {})
        # Shared precision bar for "confirmation candle" across all strategies:
        # the candle's body must be at least this fraction of its own high-low
        # range, not just any tick in the right direction. See
        # config/strategies.yaml -> confirmation_candle.
        self.confirmation_min_body_ratio = float(
            config.strategies.get("confirmation_candle", {}).get("min_body_ratio", 0.5))

    @abstractmethod
    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        """Return an Intent for the latest bar, or None for no signal."""

    def should_exit(self, symbol: str, features: pd.DataFrame, side: Side) -> str | None:
        """Signal-driven exit for an OPEN position: return a reason string to
        close at market, or None to keep holding. Default: no signal exit
        (position rides its resting stop / take-profit). Overridden per strategy.
        """
        return None

    # --- shared helpers ---
    def shorts_allowed(self, symbol: str) -> bool:
        defaults = self.config.symbols.get("defaults", {})
        fallback = bool(defaults.get("allow_short", False))
        for w in self.config.symbols.get("watchlist", []) or []:
            if w.get("symbol") == symbol:
                return bool(w.get("allow_short", fallback))
        return fallback

    def initial_stop(self, side: Side, entry: float, atr: float | None = None) -> float:
        p = self.ratchet_params
        if "atr_multiple_initial" in p and atr is not None:
            dist = float(p["atr_multiple_initial"]) * atr
            return entry - dist if side == Side.LONG else entry + dist
        pct = float(p.get("initial_stop_pct", 10.0)) / 100.0
        return entry * (1 - pct) if side == Side.LONG else entry * (1 + pct)


def has_nan(row: pd.Series, cols: list[str]) -> bool:
    return any(pd.isna(row[c]) for c in cols)


def _body_ratio_ok(curr: pd.Series, min_body_ratio: float) -> bool:
    """True if the candle's real body is at least `min_body_ratio` of its own
    high-low range. Filters out a weak tick or doji that happens to close in
    the right direction but shows no real conviction -- e.g. a 0.1%-body candle
    sitting inside a wide range shouldn't confirm a reversal or a breakout."""
    rng = curr.high - curr.low
    if rng <= 0 or pd.isna(rng):
        return False
    body = abs(curr.close - curr.open)
    return (body / rng) >= min_body_ratio


def bullish_confirmation(curr: pd.Series, prev: pd.Series, min_body_ratio: float = 0.5) -> bool:
    """A real-bodied bullish candle (see `_body_ratio_ok`) that also closes
    above the prior close -- not just any green tick."""
    return (curr.close > curr.open and curr.close > prev.close
            and _body_ratio_ok(curr, min_body_ratio))


def bearish_confirmation(curr: pd.Series, prev: pd.Series, min_body_ratio: float = 0.5) -> bool:
    return (curr.close < curr.open and curr.close < prev.close
            and _body_ratio_ok(curr, min_body_ratio))
