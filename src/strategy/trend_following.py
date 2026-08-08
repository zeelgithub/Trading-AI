"""
Strategy 1: Trend Following -- strategy layer.

Daily trend bias via 50/200 EMA, RSI band filter, volume confirmation; entries
on a pullback to the 20 EMA with a confirmation candle. The EMA touch and the
confirmation candle need not be the same bar -- a real pullback-and-reclaim
usually plays out over a couple of days (see `pullback_lookback_bars` in
config/strategies.yaml); the confirmation candle must still be TODAY's bar.
Emits intents only.

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.models import Action, Intent, Side
from src.strategy.base import Strategy, bearish_confirmation, bullish_confirmation, has_nan

_REQUIRED = ["ema20", "ema50", "ema200", "rsi", "vol_sma", "adx"]
_PROXIMITY = 0.02  # "pullback to 20 EMA" = a bar traded within 2% of it


class TrendFollowing(Strategy):
    name = "trend_following"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        lookback = int(self.params.get("conditions", {}).get("pullback_lookback_bars", 3))
        if len(features) < max(2, lookback):
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
        window = features.iloc[-lookback:]

        if long_bias and 40 <= last.rsi <= 70:
            # The touch can be any of the last `lookback` bars; the reclaim
            # (today's close back above the 20 EMA) and the confirmation
            # candle must be TODAY -- that precision is what makes it an entry
            # trigger, not just "sometime recently the price was near the MA."
            pulled_back_recently = (window["low"] <= window["ema20"] * (1 + _PROXIMITY)).any()
            if (pulled_back_recently
                    and bullish_confirmation(last, prev, self.confirmation_min_body_ratio)
                    and close > last.ema20):
                return self._intent(symbol, Side.LONG, close, last.adx)

        if short_bias and 30 <= last.rsi <= 60 and self.shorts_allowed(symbol):
            rejected_recently = (window["high"] >= window["ema20"] * (1 - _PROXIMITY)).any()
            if (rejected_recently
                    and bearish_confirmation(last, prev, self.confirmation_min_body_ratio)
                    and close < last.ema20):
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
