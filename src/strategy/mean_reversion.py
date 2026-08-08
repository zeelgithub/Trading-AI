"""
Strategy 2: Mean Reversion -- strategy layer.

Bollinger-band stretch (price >= 2 sigma from the 20-day SMA) plus an RSI
extreme, confirmed by a real-bodied reversal candle (see
src/strategy/base.py bullish_confirmation/bearish_confirmation). The extreme
touch and the confirmation candle need not be the same bar -- reaching a
2-sigma stretch and then reversing are rarely the identical day (see
`reversion_lookback_bars` in config/strategies.yaml); the confirmation
candle must still be TODAY's bar. Tight 2% stop and ~2% target (the target
IS the strategy's exit; the ratchet only provides the downside stop). Emits
intents only.

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
        lookback = int(self.params.get("conditions", {}).get("reversion_lookback_bars", 3))
        if len(features) < max(2, lookback):
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        if has_nan(last, _REQUIRED):
            return None

        close = last.close
        window = features.iloc[-lookback:]
        touched_long = ((window["close"] <= window["bb_lower"]) & (window["rsi"] < 30)).any()
        touched_short = ((window["close"] >= window["bb_upper"]) & (window["rsi"] > 70)).any()

        if touched_long and bullish_confirmation(last, prev, self.confirmation_min_body_ratio):
            return self._intent(symbol, Side.LONG, close)

        if (touched_short and bearish_confirmation(last, prev, self.confirmation_min_body_ratio)
                and self.shorts_allowed(symbol)):
            return self._intent(symbol, Side.SHORT, close)

        return None

    def should_exit(self, symbol: str, features, side: Side) -> str | None:
        """Exit when price crosses back to the BB mid (20-day SMA) — the 'revert
        to mean' leg documented in strategies.yaml. The +2% take-profit is handled
        separately by the broker OCO order."""
        last = features.iloc[-1]
        if has_nan(last, ["bb_mid"]):
            return None
        if side == Side.LONG and last.close >= last.bb_mid:
            return "mean_reverted_to_mid"
        if side == Side.SHORT and last.close <= last.bb_mid:
            return "mean_reverted_to_mid"
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
