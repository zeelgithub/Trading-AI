"""Cross-sectional momentum ranking: formation return, bucket assignment, causality."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.cross_sectional import add_cross_sectional_momentum


def _series(prices: list[float], start="2024-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame({"close": prices}, index=idx)


def test_strong_performer_lands_in_top_bucket():
    n = 200
    # AAA doubles smoothly; BBB, CCC, DDD, EEE stay flat -- AAA should
    # dominate the top bucket once formation history exists.
    strong = [100.0 * (1 + 0.01) ** i for i in range(n)]
    flat = [100.0] * n
    feats = add_cross_sectional_momentum(
        {"AAA": _series(strong), "BBB": _series(flat), "CCC": _series(flat),
         "DDD": _series(flat), "EEE": _series(flat)},
        lookback=60, skip=5, top_pct=0.2,
    )
    last = feats["AAA"].iloc[-1]
    assert last["momentum_top_bucket"]
    assert last["momentum_percentile"] > 0.5


def test_flat_performer_not_in_top_bucket_when_a_strong_one_exists():
    n = 200
    strong = [100.0 * (1 + 0.01) ** i for i in range(n)]
    flat = [100.0] * n
    feats = add_cross_sectional_momentum(
        {"AAA": _series(strong), "BBB": _series(flat), "CCC": _series(flat),
         "DDD": _series(flat), "EEE": _series(flat)},
        lookback=60, skip=5, top_pct=0.2,
    )
    assert not feats["BBB"].iloc[-1]["momentum_top_bucket"]


def test_too_few_symbols_that_date_never_flags_top_bucket():
    # Only 2 symbols total -- below the min-cross-section-size-of-5 floor --
    # so momentum_top_bucket must stay False everywhere, not "top 20% of 2".
    n = 100
    a = [100.0 * (1 + 0.02) ** i for i in range(n)]
    b = [100.0] * n
    feats = add_cross_sectional_momentum(
        {"AAA": _series(a), "BBB": _series(b)}, lookback=30, skip=5, top_pct=0.2)
    assert not feats["AAA"]["momentum_top_bucket"].any()
    assert not feats["BBB"]["momentum_top_bucket"].any()


def test_bucket_assignment_is_causal_not_affected_by_future_prices():
    """Regression guard: changing prices AFTER date T must not change T's own
    bucket assignment -- the cross-sectional rank at each date must only use
    that date's (already-causal, trailing) formation returns, never a later
    date's values leaking backward."""
    n = 150
    rng = np.random.default_rng(0)
    base = {
        sym: 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
        for sym in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    }
    frames = {sym: _series(list(prices)) for sym, prices in base.items()}
    feats_a = add_cross_sectional_momentum(frames, lookback=40, skip=5, top_pct=0.2)
    cutoff = 100  # a date well before the end of history

    # Now mutate every symbol's prices strictly AFTER `cutoff` and recompute.
    mutated = {}
    for sym, prices in base.items():
        p = list(prices)
        p[cutoff + 1:] = [x * 5 for x in p[cutoff + 1:]]  # wildly different future
        mutated[sym] = _series(p)
    feats_b = add_cross_sectional_momentum(mutated, lookback=40, skip=5, top_pct=0.2)

    for sym in base:
        row_a = feats_a[sym].iloc[cutoff]
        row_b = feats_b[sym].iloc[cutoff]
        assert row_a["momentum_top_bucket"] == row_b["momentum_top_bucket"]
        assert row_a["momentum_percentile"] == row_b["momentum_percentile"]


def test_original_frames_are_not_mutated():
    n = 60
    frames = {"AAA": _series([100.0] * n), "BBB": _series([100.0] * n)}
    original_columns = set(frames["AAA"].columns)
    add_cross_sectional_momentum(frames, lookback=20, skip=5, top_pct=0.2)
    assert set(frames["AAA"].columns) == original_columns  # untouched, no new columns leaked in
