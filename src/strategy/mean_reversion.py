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

RSI extreme thresholds (`rsi_oversold`/`rsi_overbought`, config/strategies.yaml)
are now actually read from config -- same class of bug as trend_following's
rsi_long_band/rsi_short_band fix (2026-08-21): declared in config but hardcoded
as literals here, so editing the config silently did nothing. Values unchanged
(30/70).

Primary-trend direction filter (`require_trend_alignment`, config-toggleable,
DEFAULT OFF): a long dip-buy would require close > ema200, a short rip-sell
close < ema200 -- distinct from the regime filter's ADX gate (trend STRENGTH,
not DIRECTION), aimed at this strategy's own documented "falling knife" risk.
Backed by established mean-reversion practice in general, but empirically
TESTED against this project's own cached backtest data and found to hurt, not
help (win rate 37.5%->30%, PF 0.56->0.40, trade count collapsed 48->10) --
the losing trades turned out to be ordinary large-caps (JNJ, CAT, HD...), not
the crisis/delisted names the filter was meant to catch, so ema200 alignment
isn't the variable actually driving this strategy's weakness. Kept as an
off-by-default, toggleable building block rather than deleted, in case a
better-targeted variant is worth trying later -- see docs/STRATEGIES.md
"Mean reversion trend filter (tested, rejected)".

Boundary: places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.models import Action, Intent, Side
from src.strategy.base import (
    Strategy,
    bearish_confirmation,
    bullish_confirmation,
    has_nan,
)

_REQUIRED = ["bb_lower", "bb_upper", "bb_mid", "rsi"]


class MeanReversion(Strategy):
    name = "mean_reversion"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        conds = self.params.get("conditions", {})
        lookback = int(conds.get("reversion_lookback_bars", 3))
        require_trend_alignment = bool(conds.get("require_trend_alignment", False))
        rsi_oversold = float(conds.get("rsi_oversold", 30))
        rsi_overbought = float(conds.get("rsi_overbought", 70))
        if len(features) < max(2, lookback):
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        required = _REQUIRED + ["ema200"] if require_trend_alignment else _REQUIRED
        if has_nan(last, required):
            return None

        close = last.close
        window = features.iloc[-lookback:]
        touched_long = ((window["close"] <= window["bb_lower"]) & (window["rsi"] < rsi_oversold)).any()
        touched_short = ((window["close"] >= window["bb_upper"]) & (window["rsi"] > rsi_overbought)).any()
        # See module docstring: only buy a dip inside an intact uptrend / sell
        # a rip inside an intact downtrend, not against the primary trend.
        long_trend_ok = not require_trend_alignment or close > last.ema200
        short_trend_ok = not require_trend_alignment or close < last.ema200

        if touched_long and long_trend_ok and bullish_confirmation(last, prev, self.confirmation_min_body_ratio):
            return self._intent(symbol, Side.LONG, close)

        if (touched_short and short_trend_ok
                and bearish_confirmation(last, prev, self.confirmation_min_body_ratio)
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
            confidence=float(self.params.get("confidence", 0.6)),
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry), 2),
            take_profit=round(take_profit, 2),
        )
        intent.validate()
        return intent
