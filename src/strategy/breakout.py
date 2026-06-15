"""
Strategy 3: Breakout -- strategy layer.

Close beyond recent support/resistance, confirmed by a volume spike (>= 1.5x
the 20-day average) and rising ATR (volatility expansion). ATR-scaled stop.
Emits intents only.

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.models import Action, Intent, Side
from src.strategy.base import Strategy, has_nan

_REQUIRED = ["atr", "vol_sma"]


class Breakout(Strategy):
    name = "breakout"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        conds = self.params.get("conditions", {})
        lookback = int(self.params.get("indicators", {}).get("sr_lookback", 20))
        min_spike = float(conds.get("min_volume_spike", 1.5))

        if len(features) < lookback + 2:
            return None
        last = features.iloc[-1]
        if has_nan(last, _REQUIRED):
            return None

        # Prior `lookback` bars, excluding the current one.
        window = features.iloc[-(lookback + 1):-1]
        resistance = window["high"].max()
        support = window["low"].min()

        close = last.close
        vol_spike = last.volume >= last.vol_sma * min_spike
        atr_rising = last.atr > features["atr"].iloc[-2]
        if not (vol_spike and atr_rising):
            return None

        if close > resistance:
            return self._intent(symbol, Side.LONG, close, last.atr)
        if close < support and self.shorts_allowed(symbol):
            return self._intent(symbol, Side.SHORT, close, last.atr)
        return None

    def _intent(self, symbol: str, side: Side, entry: float, atr: float) -> Intent:
        intent = Intent(
            symbol=symbol,
            strategy=self.name,
            side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=0.65,
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry, atr=atr), 2),
            take_profit=None,  # rides the ATR ratchet
        )
        intent.validate()
        return intent
