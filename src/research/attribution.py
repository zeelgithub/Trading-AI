"""
Per-strategy attribution -- research layer.

Segments a backtest's closed trades by strategy and across time so you can see
WHICH strategy produced the edge -- and whether that edge is spread across the
whole window or concentrated in one lucky stretch. The portfolio-level
`compute_metrics` deliberately stays untouched; this adds the per-strategy view
it never had.

Boundary: pure functions over closed trades; places orders NO.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

# A "trade" here is any object exposing .strategy, .pnl, .return_pct, .exit_date
# (e.g. research.backtester.Trade). We stay duck-typed so tests can pass stubs.


def per_strategy_breakdown(trades: Iterable[Any]) -> dict[str, dict]:
    """Group closed trades by strategy and compute headline stats for each."""
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        groups[t.strategy].append(t)
    return {name: _stats(ts) for name, ts in groups.items()}


def _stats(trades: list) -> dict:
    pnls = [float(t.pnl) for t in trades]
    rets = [float(t.return_pct) for t in trades]
    n = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    total_pnl = sum(pnls)
    return {
        "num_trades": n,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "avg_return_pct": round(sum(rets) / n, 5) if n else 0.0,
        "expectancy": round(total_pnl / n, 2) if n else 0.0,  # avg $ per trade
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
    }


def temporal_consistency(trades: Iterable[Any], n_buckets: int = 4) -> dict[str, dict]:
    """For each strategy, split its trades into `n_buckets` equal-size, time-ordered
    chunks and report how many chunks were net profitable.

    This is the cheap, robust answer to "is the edge consistent or one-period
    luck?" -- it needs no parameter re-fitting (these strategies fit nothing),
    so it cannot introduce look-ahead. A strategy whose whole profit came from a
    single quarter scores low here even with a great headline Sharpe.
    """
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        groups[t.strategy].append(t)

    out: dict[str, dict] = {}
    for name, ts in groups.items():
        if len(ts) < n_buckets:
            out[name] = {
                "buckets": len(ts),
                "positive": None,
                "consistency": None,
                "note": "too few trades to assess consistency",
            }
            continue
        ordered = sorted(ts, key=lambda x: x.exit_date)
        chunks = _split_evenly(ordered, n_buckets)
        bucket_pnls = [round(sum(float(t.pnl) for t in c), 2) for c in chunks]
        positive = sum(1 for p in bucket_pnls if p > 0)
        out[name] = {
            "buckets": n_buckets,
            "positive": positive,
            "consistency": round(positive / n_buckets, 4),
            "bucket_pnls": bucket_pnls,
        }
    return out


def _split_evenly(items: list, n: int) -> list[list]:
    """Split a list into n contiguous chunks whose sizes differ by at most one."""
    k, m = divmod(len(items), n)
    chunks = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(items[start:start + size])
        start += size
    return chunks
