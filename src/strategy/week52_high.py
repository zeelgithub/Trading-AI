"""
Strategy candidate (RESEARCH ONLY, not live-wired): 52-week-high anchoring
momentum.

Distinct mechanism from both trend_following (EMA/ADX trend bias) and the
cross-sectional momentum candidate (src/strategy/momentum.py, relative-return
rank against the rest of the universe on a given date). This one is a
single-symbol, absolute-threshold signal: George & Hwang (2004) "The 52-Week
High and Momentum Investing" -- investors anchor on a stock's own trailing
52-week high as a reference price and are slow to bid above it even on good
news, so a stock reaching or breaking that anchor tends to keep drifting for
weeks afterward as the market gradually re-rates it. The anchor is this
stock's OWN price history, not a comparison to any other symbol -- no
cross-sectional precompute step needed (contrast src/research/
cross_sectional.py), so this reads straight off features's own high/low/close
columns, computed inline exactly like src/strategy/breakout.py's
support/resistance window.

Entry: fires the day a symbol NEWLY enters the "near its trailing 252-bar
high" zone (within `proximity_pct` of it, or above it) -- a transition, not
"already near the high -> buy the extended move" (mirrors momentum.py's
bucket-entry design, applied to a different ranking measure). Confirmed by
the same real-bodied confirmation candle every other strategy requires.
Symmetric short leg: newly enters the "near its trailing 252-bar low" zone
(the anchoring literature documents a weaker but real symmetric effect on
the downside).

Exit: no signal-based exit (should_exit uses the base class's "no signal
exit" default) -- the entry is a punctual breakthrough EVENT, not an ongoing
state like momentum's bucket membership, so re-checking "still near the
anchor" daily would exit far too early on the very next small pullback and
cut off the drift this strategy exists to capture. Rides the ATR ratchet
instead, same exit philosophy as breakout (also event-triggered, also
ATR-ratchet-only) -- initial ATR multiples reused from breakout's own
(tested, unchanged) defaults as a reasonable starting point, not yet tuned
for this strategy specifically.

Deliberately NOT decorated with @register and NOT given a block in
config/strategies.yaml -- src/strategy/registry.py's build_strategies() (used
by the live orchestrator, discovery, and the default backtester construction)
therefore can never instantiate this without an explicit code change. This
research module is imported and wired manually, on purpose, by whatever
research script is evaluating it (see scripts/research_week52_high.py).

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

_REQUIRED = ["atr"]


class Week52High(Strategy):
    name = "week52_high"

    def generate(self, symbol: str, features: pd.DataFrame) -> Intent | None:
        lookback = int(self.params.get("indicators", {}).get("anchor_lookback_bars", 252))
        proximity = float(self.params.get("conditions", {}).get("proximity_pct", 5.0)) / 100.0

        if len(features) < lookback + 2:
            return None
        last, prev = features.iloc[-1], features.iloc[-2]
        if has_nan(last, _REQUIRED):
            return None

        # Trailing `lookback`-bar high/low, EXCLUDING today (shift(1)) so
        # today's own bar never contaminates its own anchor -- otherwise
        # "close is within X% of the high" would be tautologically true
        # whenever today itself makes the new high, rather than measuring
        # proximity to a pre-existing reference price. High/low (not close),
        # same convention breakout.py's support/resistance window uses --
        # the anchor is the actual traded extreme, not just where it settled.
        anchor_high = features["high"].shift(1).rolling(lookback).max()
        anchor_low = features["low"].shift(1).rolling(lookback).min()
        if pd.isna(anchor_high.iloc[-1]) or pd.isna(anchor_high.iloc[-2]):
            return None

        near_high_today = last.close >= anchor_high.iloc[-1] * (1 - proximity)
        near_high_yday = prev.close >= anchor_high.iloc[-2] * (1 - proximity)
        entered_near_high = near_high_today and not near_high_yday
        if entered_near_high and bullish_confirmation(last, prev, self.confirmation_min_body_ratio):
            return self._intent(symbol, Side.LONG, last.close, last.atr)

        near_low_today = last.close <= anchor_low.iloc[-1] * (1 + proximity)
        near_low_yday = prev.close <= anchor_low.iloc[-2] * (1 + proximity)
        entered_near_low = near_low_today and not near_low_yday
        if (entered_near_low and self.shorts_allowed(symbol)
                and bearish_confirmation(last, prev, self.confirmation_min_body_ratio)):
            return self._intent(symbol, Side.SHORT, last.close, last.atr)

        return None

    def _intent(self, symbol: str, side: Side, entry: float, atr: float) -> Intent:
        intent = Intent(
            symbol=symbol,
            strategy=self.name,
            side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=float(self.params.get("confidence", 0.6)),
            entry_price=round(entry, 2),
            stop_loss=round(self.initial_stop(side, entry, atr=atr), 2),
            take_profit=None,  # rides the ATR ratchet, no fixed target -- same as breakout
        )
        intent.validate()
        return intent
