"""
Strategy candidate (RESEARCH ONLY, not live-wired): Cross-sectional momentum.

Reads the momentum_top_bucket / momentum_percentile columns precomputed by
src/research/cross_sectional.add_cross_sectional_momentum -- this class itself
still only ever looks at ONE symbol's own frame, exactly like every other
Strategy subclass; the cross-symbol comparison already happened upstream.

Entry: fires the day a symbol NEWLY enters the top-momentum bucket (wasn't in
it yesterday, is today), confirmed by the same real-bodied candle filter
every other strategy uses -- not "in bucket -> buy immediately", which would
enter on a stale, already-extended move instead of the actual transition.
Exit: the moment the symbol falls OUT of its bucket -- the edge this entry
was based on is gone, mirrors trend_following's opposite-EMA-break design
(a pure function of TODAY's state, no remembered entry-time context needed).

Deliberately NOT decorated with @register and NOT given a block in
config/strategies.yaml -- src/strategy/registry.py's build_strategies() (used
by the live orchestrator, discovery, and the default backtester construction)
therefore can never instantiate this without an explicit code change. This
research module is imported and wired manually, on purpose, by whatever
research script is evaluating it.

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

_REQUIRED = ["momentum_top_bucket", "momentum_bottom_bucket", "momentum_percentile"]


class Momentum(Strategy):
    name = "momentum"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        if len(features) < 2 or has_nan(features.iloc[-1], ["close"]):
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        if has_nan(last, _REQUIRED) or has_nan(prev, _REQUIRED):
            return None
        close = last.close

        entered_top = bool(last.momentum_top_bucket) and not bool(prev.momentum_top_bucket)
        if entered_top and bullish_confirmation(last, prev, self.confirmation_min_body_ratio):
            return self._intent(symbol, Side.LONG, close, last.momentum_percentile)

        # Bottom-bucket entry -- symmetric short leg, matches the academic
        # "buy winners, sell losers" definition. momentum_bottom_bucket is
        # precomputed by add_cross_sectional_momentum from the SAME top_pct
        # used for the long side (not an independently hardcoded cutoff --
        # see that function's docstring for why that used to be a real bug).
        # shorts_allowed() gates it the same way every other strategy's short
        # leg is gated.
        entered_bottom = (
            bool(last.momentum_bottom_bucket) and not bool(prev.momentum_bottom_bucket)
        )
        if (entered_bottom
                and bearish_confirmation(last, prev, self.confirmation_min_body_ratio)
                and self.shorts_allowed(symbol)):
            return self._intent(symbol, Side.SHORT, close, 1.0 - last.momentum_percentile)

        return None

    def should_exit(self, symbol: str, features: pd.DataFrame, side: Side) -> str | None:
        last = features.iloc[-1]
        if has_nan(last, _REQUIRED):
            return None
        if side == Side.LONG and not bool(last.momentum_top_bucket):
            return "left_momentum_top_bucket"
        if side == Side.SHORT and not bool(last.momentum_bottom_bucket):
            return "left_momentum_bottom_bucket"
        return None

    def _intent(self, symbol: str, side: Side, entry: float, strength_percentile: float) -> Intent:
        # Confidence scales with how extreme the formation-return rank is --
        # same spirit as trend_following's ADX-scaled confidence, just using
        # this strategy's own strength measure instead. 0.55 base (same base
        # trend_following uses), capped at 0.9.
        confidence = round(min(0.9, 0.55 + max(0.0, strength_percentile - 0.8) * 1.75), 2)
        intent = Intent(
            symbol=symbol,
            strategy=self.name,
            side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=confidence,
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry), 2),
            take_profit=None,  # rides the ratchet, no fixed target -- same as trend_following
        )
        intent.validate()
        return intent
