"""
Walk-forward / out-of-sample evaluation -- research layer (offline).

`evaluation.py` runs ONE backtest over the whole window and asks "is this
edge statistically real" -- but every number it produces comes from a single
in-sample pass. Two problems that alone: (1) a strategy that wins on 8 of 10
years and craters on the other 2 still reports one blended, flattering p-value;
(2) this project's own regime/entry thresholds were hand-tuned by inspecting
outcomes on part of this exact window (see docs/STRATEGIES.md "Regime
thresholds") -- so the in-sample verdict is partly grading itself.

This module splits the window into `n_folds` sequential, non-overlapping
chronological folds and runs an INDEPENDENT backtest per fold: fresh equity,
no carried-over positions, no entries before the fold's own start -- but the
FULL preceding history is still fed in for indicator warmup (EMA200 etc.),
via `Backtester.run(..., entries_start=fold_start)`. Each symbol's frame is
also truncated to the fold's end, so a position still open at the fold
boundary force-closes there (`Backtester`'s existing "data_end" handling)
instead of leaking into the next fold.

This does NOT re-fit strategy parameters per fold -- these strategies fit
nothing (fixed, config-driven thresholds), so there is nothing to overfit
fold-to-fold. It answers a narrower, honest question: does the SAME fixed
logic hold up across time slices it wasn't hand-tuned to fit, including a
final fold that is a genuine holdout not used to pick any threshold.

Boundary: offline; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.common.config import Config, load_config
from src.research import attribution, significance
from src.research.backtester import Backtester

_PF_CLAMP = 999.0


@dataclass
class FoldScore:
    fold: int
    start: str
    end: str
    strategy: str
    num_trades: int
    win_rate: float
    profit_factor: float
    mean_return_pct: float
    bootstrap_p_value: float   # raw, NOT Sidak-adjusted -- see module docstring


@dataclass
class WalkForwardReport:
    folds: list[FoldScore]
    n_folds: int

    def text(self) -> str:
        if not self.folds:
            return "WALK-FORWARD: no folds produced (insufficient history)."
        lines = ["WALK-FORWARD (out-of-sample by time slice)", "=" * 78]
        header = (f"{'fold':<6}{'window':<24}{'strategy':<16}{'trades':>7}"
                  f"{'win%':>7}{'PF':>7}{'p(raw)':>8}")
        lines.append(header)
        lines.append("-" * 78)
        for f in self.folds:
            pf = "inf" if f.profit_factor >= _PF_CLAMP else f"{f.profit_factor:.2f}"
            window = f"{f.start}..{f.end}"
            lines.append(
                f"{f.fold:<6}{window:<24}{f.strategy:<16}{f.num_trades:>7}"
                f"{f.win_rate*100:>6.1f}{pf:>7}{f.bootstrap_p_value:>8.3f}"
            )
        lines.append("-" * 78)
        lines.append(
            "p-values here are raw (not Sidak-adjusted) and per-fold trade counts are "
            "small by construction -- read this for DIRECTIONAL consistency across time "
            "(does PF stay > 1, does the sign hold), not as a formal significance test. "
            f"The last fold (fold {self.n_folds}) is the closest thing this project has "
            "to a genuine out-of-sample holdout."
        )
        return "\n".join(lines)


def evaluate_walk_forward(
    features_by_symbol: dict[str, pd.DataFrame],
    config: Config | None = None,
    *,
    n_folds: int = 3,
    initial_equity: float = 100_000.0,
    n_bootstrap: int = 5000,
    seed: int = 0,
    force_strategy: str | None = None,
) -> WalkForwardReport:
    config = config or load_config()
    if n_folds < 2:
        raise ValueError("n_folds must be >= 2 (need at least one holdout split)")

    all_dates = sorted(set().union(*[df.index for df in features_by_symbol.values()]))
    if len(all_dates) < n_folds * 2:
        return WalkForwardReport(folds=[], n_folds=n_folds)

    boundaries = _split_evenly(all_dates, n_folds)
    scores: list[FoldScore] = []

    for i, fold_dates in enumerate(boundaries, start=1):
        fold_start, fold_end = fold_dates[0], fold_dates[-1]
        # Full history through fold_end (real EMA200 warmup), but truncated
        # there so a position still open at the boundary force-closes instead
        # of bleeding into the next fold.
        truncated = {
            sym: df.loc[:fold_end] for sym, df in features_by_symbol.items()
            if not df.loc[:fold_end].empty
        }
        result = Backtester(config=config, initial_equity=initial_equity).run(
            truncated, entries_start=fold_start, force_strategy=force_strategy,
        )
        breakdown = attribution.per_strategy_breakdown(result.trades)
        rets_by_strategy: dict[str, list] = {}
        for t in result.trades:
            rets_by_strategy.setdefault(t.strategy, []).append(float(t.return_pct))

        for name, stats in breakdown.items():
            rets = rets_by_strategy[name]
            boot = significance.bootstrap_pvalue(rets, n_resamples=n_bootstrap, seed=seed)
            pf = stats["profit_factor"]
            scores.append(FoldScore(
                fold=i,
                start=str(pd.Timestamp(fold_start).date()),
                end=str(pd.Timestamp(fold_end).date()),
                strategy=name,
                num_trades=stats["num_trades"],
                win_rate=stats["win_rate"],
                profit_factor=_PF_CLAMP if pf == float("inf") else pf,
                mean_return_pct=round(sum(rets) / len(rets), 5) if rets else 0.0,
                bootstrap_p_value=boot["p_value"],
            ))

    scores.sort(key=lambda s: (s.fold, s.strategy))
    return WalkForwardReport(folds=scores, n_folds=n_folds)


def _split_evenly(items: list, n: int) -> list[list]:
    """Split a time-ordered list into n contiguous, chronological chunks
    whose sizes differ by at most one (same convention as
    attribution._split_evenly, duplicated here to keep this module
    dependency-free of attribution's trade-shaped assumptions)."""
    k, m = divmod(len(items), n)
    chunks = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(items[start:start + size])
        start += size
    return chunks
