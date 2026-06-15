"""
Strategy 2: Mean Reversion -- strategy layer.

Bollinger-band stretch (price >= 2 sigma from the 20-day SMA) plus an RSI
extreme, confirmed by a reversal candle. Tight 2% stop and ~2% target (the
target IS the strategy's exit; the ratchet only provides the downside stop).
Emits intents only.

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.models import Action, Intent, Side
from src.strategy.base import Strategy, bearish_confirmation, bullish_confirmation, has_nan

_REQUIRED = ["bb_lower", "bb_upper", "bb_mid", "rsi"]


class MeanReversion(Strategy):
    name = "mean_reversion"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        if len(features) < 2:
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        if has_nan(last, _REQUIRED):
            return None

        close = last.close

        long_setup = close <= last.bb_lower and last.rsi < 30
        if long_setup and bullish_confirmation(last, prev):
            return self._intent(symbol, Side.LONG, close)

        short_setup = close >= last.bb_upper and last.rsi > 70
        if short_setup and bearish_confirmation(last, prev) and self.shorts_allowed(symbol):
            return self._intent(symbol, Side.SHORT, close)

        return None

    def _intent(self, symbol: str, side: Side, entry: float) -> Intent:
        target_pct = float(self.ratchet_params.get("profit_target_pct", 2.0)) / 100.0
        if side == Side.LONG:
            take_profit = entry * (1 + target_pct)
        else:
            take_profit = entry * (1 - target_pct)
        intent = Intent(
            symbol=symbol,
            strategy=self.name,
            side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=0.6,
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry), 2),
            take_profit=round(take_profit, 2),
        )
        intent.validate()
        return intent
