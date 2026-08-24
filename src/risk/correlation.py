"""
Correlation-aware open-risk grouping -- risk layer.

RiskManager's aggregate open-risk cap (src/risk/risk_manager.py step 7.5,
src/risk/exposure.py) sums qty * |entry - stop| across every open position as
if each dollar of risk were independent. That's a reasonable approximation
for the three regime-routed strategies (trend_following/mean_reversion/
breakout), which mostly fire on different symbols at different times. It
breaks down for a strategy like cross-sectional momentum
(src/strategy/momentum.py, src/research/cross_sectional.py) that can open
several positions from the SAME correlated bucket at once (e.g. a sector
rally) -- a broad selloff doesn't hit those stops independently, it hits them
together, so the flat cap can be "satisfied" while the real, correlated risk
is materially higher.

This computes, for one candidate symbol, the total open_risk_dollars among
ALREADY-OPEN positions whose trailing daily-return correlation with the
candidate is >= `threshold`. RiskManager.evaluate() step 7.6 adds the
candidate's own (not-yet-open) risk to this and checks the sum against a
tighter cap (max_correlated_risk_pct) than the flat aggregate one.

Correlation is computed from closing-price history the caller already has
(the same bars/features used for indicators) -- no network I/O here, pure
pandas over already-fetched data, same boundary as
src/research/cross_sectional.py.

Positions whose correlation can't be computed (symbol missing from
closes_by_symbol, or fewer than `lookback` overlapping return observations)
are EXCLUDED from this tighter check, not conservatively assumed correlated
-- they still count toward the flat aggregate cap (step 7.5), so nothing
silently loses protection; this step only ever tightens further, it never
replaces the flat cap.

Boundary: pure computation, no I/O, places orders NO.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pandas as pd


def correlated_open_risk(
    candidate_symbol: str,
    open_positions: Iterable,
    closes_by_symbol: Mapping[str, pd.Series],
    lookback: int = 60,
    threshold: float = 0.6,
) -> float:
    """Sum of qty * |entry - stop| across `open_positions` (duck-typed: each
    needs `.symbol`, `.qty` optionally shadowed by `.filled_qty`, and
    `.ratchet.entry` / `.ratchet.stop` -- the same shape src.risk.exposure.
    compute_exposure expects) whose symbol's trailing `lookback`-day return
    correlation with `candidate_symbol` is >= `threshold`.

    `closes_by_symbol` must contain a `candidate_symbol` entry to compute
    anything; if it's missing, or has fewer than `lookback + 1` closes (not
    enough to form `lookback` daily returns), this returns 0.0 -- unknown
    correlation is excluded, not assumed high (see module docstring).
    """
    candidate_closes = closes_by_symbol.get(candidate_symbol)
    if candidate_closes is None or len(candidate_closes) < lookback + 1:
        return 0.0
    candidate_returns = candidate_closes.tail(lookback + 1).pct_change().dropna()

    total = 0.0
    for pos in open_positions:
        if pos.symbol == candidate_symbol:
            continue  # this project holds at most one position per symbol
        other_closes = closes_by_symbol.get(pos.symbol)
        if other_closes is None or len(other_closes) < lookback + 1:
            continue
        other_returns = other_closes.tail(lookback + 1).pct_change().dropna()
        aligned = pd.concat([candidate_returns, other_returns], axis=1, join="inner")
        if len(aligned) < lookback // 2:  # too little overlap to trust the estimate
            continue
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if pd.isna(corr) or corr < threshold:
            continue
        qty = getattr(pos, "filled_qty", None) or pos.qty
        entry = pos.ratchet.entry
        stop = pos.ratchet.stop
        total += qty * abs(entry - stop)
    return total
