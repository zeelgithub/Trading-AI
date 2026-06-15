"""
Strategy 1: Trend Following -- strategy layer.

Daily trend bias via 50/200 EMA, RSI band filter, volume confirmation; entries
on a pullback to the 20 EMA with a confirmation candle. Emits intents only.

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.models import Action, Intent, Side
from src.strategy.base import Strategy, bearish_confirmation, bullish_confirmation, has_nan

_REQUIRED = ["ema20", "ema50", "ema200", "rsi", "vol_sma", "adx"]
_PROXIMITY = 0.02  # "pullback to 20 EMA" = bar traded within 2% of it


class TrendFollowing(Strategy):
    name = "trend_following"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        if len(features) < 2:
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        if has_nan(last, _REQUIRED):
            return None

        close = last.close
        min_vol = float(self.params.get("conditions", {}).get("min_volume_vs_ma", 1.0))
        vol_ok = last.volume > last.vol_sma * min_vol
        if not vol_ok:
            return None

        long_bias = close > last.ema50 and close > last.ema200
        short_bias = close < last.ema50 and close < last.ema200

        if long_bias and 40 <= last.rsi <= 70:
            pulled_back = last.low <= last.ema20 * (1 + _PROXIMITY)
            if pulled_back and bullish_confirmation(last, prev) and close > last.ema20:
                return self._intent(symbol, Side.LONG, close, last.adx)

        if short_bias and 30 <= last.rsi <= 60 and self.shorts_allowed(symbol):
            rejected = last.high >= last.ema20 * (1 - _PROXIMITY)
            if rejected and bearish_confirmation(last, prev) and close < last.ema20:
                return self._intent(symbol, Side.SHORT, close, last.adx)

        return None

    def should_exit(self, symbol: str, features, side: Side) -> str | None:
        """Exit on an opposite-EMA break: a long closes when price loses the
        50 EMA, a short when it reclaims it (the documented trend exit)."""
        last = features.iloc[-1]
        if has_nan(last, ["ema50"]):
            return None
        if side == Side.LONG and last.close < last.ema50:
            return "opposite_ema_break"
        if side == Side.SHORT and last.close > last.ema50:
            return "opposite_ema_break"
        return None

    def _intent(self, symbol: str, side: Side, entry: float, adx: float) -> Intent:
        confidence = round(min(0.9, 0.55 + max(0.0, (adx - 25)) / 100.0), 2)
        intent = Intent(
            symbol=symbol,
            strategy=self.name,
            side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=confidence,
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry), 2),
            take_profit=None,  # trend trades ride the ratchet, no fixed target
        )
        intent.validate()
        return intent
