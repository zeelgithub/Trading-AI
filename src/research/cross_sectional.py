"""
Cross-sectional momentum ranking -- research layer (offline).

Every existing strategy (trend_following, mean_reversion, breakout) decides
using only ONE symbol's own history -- src/strategy/base.py's Strategy.generate
signature is (symbol, features), no visibility into any other symbol. Momentum
is structurally different: "is this symbol strong" is a question about its
return RELATIVE TO THE REST OF THE UNIVERSE on the same date, not about its
own chart in isolation.

Rather than change the Strategy interface (which the live orchestrator,
discovery, and every other strategy also depend on), this precomputes the
cross-sectional rank as ordinary feature columns, added to each symbol's own
frame, BEFORE the frames are handed to the backtester. src/strategy/momentum.py
then reads those columns through the exact same single-symbol generate()
signature everything else uses -- zero interface changes, zero live-path
changes.

Methodology (Jegadeesh & Titman 1993 "returns to buying winners and selling
losers", the standard academic formation): trailing `lookback` trading days'
return, ending `skip` trading days ago (skipping the most recent ~month is
the well-documented fix for short-term reversal contaminating a momentum
signal). A symbol is in the "top bucket" on a date if its formation return
ranks in the top `top_pct` of all symbols with valid data that date.

Causality: the per-symbol formation return at row i uses only bars <= i (pure
.shift()/.rolling()); the cross-sectional rank at a given date compares only
OTHER SYMBOLS' values at that SAME date (contemporaneous, not future) -- this
is the same causal contract as the rest of src/data/features.py, just applied
across symbols instead of across time.

Boundary: pure functions over already-fetched data; places orders NO, touches
no live code path.
"""

from __future__ import annotations

import pandas as pd


def add_cross_sectional_momentum(
    features_by_symbol: dict[str, pd.DataFrame],
    lookback: int = 126,
    skip: int = 21,
    top_pct: float = 0.2,
) -> dict[str, pd.DataFrame]:
    """Return a NEW dict (inputs untouched) where every frame gains two columns:
      - momentum_formation_return: trailing `lookback`-day return ending `skip`
        days ago, this symbol only (causal, no cross-symbol info yet).
      - momentum_top_bucket: True if this symbol's formation return is in the
        top `top_pct` of all symbols with a valid formation return on that date.

    Dates where fewer than 5 symbols have a valid formation return are left
    with momentum_top_bucket=False everywhere (too small a cross-section to
    rank meaningfully -- e.g. universe warmup at the very start of history).
    """
    formation: dict[str, pd.Series] = {}
    out: dict[str, pd.DataFrame] = {}
    for symbol, df in features_by_symbol.items():
        close = df["close"]
        past = close.shift(skip)
        past_lookback = close.shift(skip + lookback)
        ret = (past / past_lookback) - 1.0
        formation[symbol] = ret
        out[symbol] = df.copy()
        out[symbol]["momentum_formation_return"] = ret

    # Union of all dates, so a date only some symbols have (e.g. a later IPO)
    # still gets ranked correctly among whoever has data that day.
    all_dates = sorted(set().union(*[s.index for s in formation.values()]))
    formation_matrix = pd.DataFrame({sym: s for sym, s in formation.items()}, index=all_dates)

    top_bucket_matrix = pd.DataFrame(False, index=all_dates, columns=formation_matrix.columns)
    for date in all_dates:
        row = formation_matrix.loc[date].dropna()
        if len(row) < 5:
            continue
        cutoff = row.quantile(1.0 - top_pct)
        # Ties at the cutoff all included (>=), consistent with "top_pct of
        # the field", not an exact headcount -- matters only when few symbols
        # share an identical formation return, which is rare with real prices.
        top_bucket_matrix.loc[date, row[row >= cutoff].index] = True

    for symbol, frame in out.items():
        frame["momentum_top_bucket"] = top_bucket_matrix[symbol].reindex(frame.index, fill_value=False)
        # Percentile rank (0-1, higher = stronger) for confidence scaling --
        # NaN (not enough cross-section that date, or this symbol has no
        # formation return yet) becomes 0.0, i.e. "no evidence of strength".
        pct_rank = formation_matrix.rank(axis=1, pct=True)[symbol]
        frame["momentum_percentile"] = pct_rank.reindex(frame.index).fillna(0.0)

    return out
