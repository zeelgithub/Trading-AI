"""
Volatility source -- discovery layer.

A "more aggressive, more suitable for active trading" lens, added 2026-08-24
at the user's request -- but deliberately NOT a new universe of penny stocks
or leveraged ETFs (the exact noise Alpaca's most-actives screener surfaced
when checked live for src/discovery/sp500.py). Instead this re-ranks the
EXISTING quality-filtered universe (watchlist + S&P 500/400/600) by how much
each symbol actually moves, so bigger-swing names surface higher without
adding a single new, less-liquid, or gap-risky ticker.

Score is each symbol's ATR (Average True Range, already computed by
src/data/features.py -- no new indicator) as a percentage of its own price,
ranked as a PERCENTILE against every other symbol screened in the same run
-- not an absolute threshold. Relative ranking adapts to the market's
current volatility regime automatically instead of a hardcoded "5% ATR is
high" that would go stale; the most-volatile name screened today gets
score ~1.0, the least gets ~0.0, regardless of whether it's a calm month or
a choppy one.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from src.discovery.candidate import SignalContribution
from src.discovery.sources._util import percentile_rank, safe_call, safe_num

FeatureProvider = Callable[[str], pd.DataFrame]


@dataclass
class VolatilitySource:
    feature_provider: FeatureProvider
    universe: list[str]
    name: str = "volatility"

    def gather(self) -> list[SignalContribution]:
        atr_pct_by_symbol: dict[str, float] = {}
        for symbol in dict.fromkeys(s.upper() for s in self.universe):
            feats = safe_call(self.feature_provider, symbol)
            if feats is None or feats.empty:
                continue
            last = feats.iloc[-1]
            atr = safe_num(last, "atr")
            close = safe_num(last, "close")
            if atr is None or close is None or close <= 0:
                continue
            atr_pct_by_symbol[symbol] = atr / close

        if not atr_pct_by_symbol:
            return []

        # Percentile rank (0-1, higher = more volatile relative to today's
        # screened peers) -- ties (e.g. two symbols with identical ATR%,
        # common with only a handful of data points) share the same rank.
        ranked = percentile_rank(atr_pct_by_symbol)

        out: list[SignalContribution] = []
        for symbol, atr_pct in atr_pct_by_symbol.items():
            pct_rank = ranked[symbol]
            out.append(SignalContribution(
                symbol=symbol, source=self.name, score=pct_rank,
                reason=f"Volatility: ATR {atr_pct * 100:.1f}% of price "
                       f"(top {(1 - pct_rank) * 100:.0f}% of today's screen)",
                meta={"atr_pct": atr_pct, "percentile": pct_rank},
            ))
        return out
