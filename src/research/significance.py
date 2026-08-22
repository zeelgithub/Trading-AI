"""
Significance testing -- research layer.

A backtest Sharpe means little until you ask two questions: could this have
arisen by chance, and does it beat simply holding the benchmark? These pure
functions answer the first with three independent lenses:

  * bootstrap_pvalue -- resample the trade returns to estimate the probability
    the true mean return is <= 0 (i.e. there is no edge), with a confidence band.
  * probabilistic_sharpe_ratio -- the probability the true Sharpe exceeds zero,
    correcting for sample size, skew and fat tails (Bailey & Lopez de Prado, 2012).
    Formula verified 2026-08-21 against the original paper (cross-checked via
    QuantConnect's reproduction and a López de Prado-credited reference
    implementation): z = (SR-SR*)*sqrt(n-1) / sqrt(1-skew*SR+((kurt-1)/4)*SR^2),
    kurtosis RAW (normal=3, matching pandas .kurt()+3 below, not pandas' own
    excess convention) -- this implementation matches exactly, no bug found.
  * sidak_adjust -- inflate a p-value for the number of strategies tried, so that
    testing many strategies cannot manufacture a false "winner" by luck.

`buy_and_hold` gives the benchmark leg of the comparison.

Boundary: pure functions; places orders NO.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

_TRADING_DAYS = 252


def bootstrap_pvalue(returns: Iterable[float], n_resamples: int = 5000, seed: int = 0) -> dict:
    """Bootstrap the mean per-trade return.

    Returns a one-sided p-value = the fraction of resampled means that are <= 0
    (the probability the strategy has no positive edge), plus a 95% CI on the
    mean return.
    """
    r = np.asarray(list(returns), dtype=float)
    n = len(r)
    if n < 2:
        return {"p_value": 1.0, "ci_low": 0.0, "ci_high": 0.0,
                "mean_return": float(r.mean()) if n else 0.0, "n": n}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    means = r[idx].mean(axis=1)
    p = float(np.mean(means <= 0.0))
    return {
        "p_value": round(p, 4),
        "ci_low": round(float(np.percentile(means, 2.5)), 5),
        "ci_high": round(float(np.percentile(means, 97.5)), 5),
        "mean_return": round(float(r.mean()), 5),
        "n": n,
    }


def probabilistic_sharpe_ratio(returns: Iterable[float], benchmark_sr: float = 0.0) -> float:
    """Probability the true (per-trade) Sharpe ratio exceeds `benchmark_sr`.

    Uses the PSR estimator, which corrects the naive Sharpe for a finite sample
    and for non-normal returns (skew / kurtosis). Returns a probability in [0, 1];
    >= 0.95 is the usual bar for "the Sharpe is real."
    """
    r = np.asarray(list(returns), dtype=float)
    n = len(r)
    sd = r.std(ddof=1) if n >= 2 else 0.0
    if n < 3 or sd < 1e-12:   # effectively constant returns -> Sharpe undefined
        return 0.0
    sr = float(r.mean() / sd)  # per-observation (trade) Sharpe
    s = pd.Series(r)
    skew = float(s.skew())
    kurt = float(s.kurt()) + 3.0  # pandas .kurt() is excess; PSR needs raw kurtosis
    if not math.isfinite(skew):
        skew = 0.0
    if not math.isfinite(kurt):
        kurt = 3.0
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2))
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / denom
    return round(_norm_cdf(z), 4)


def trade_sharpe(returns: Iterable[float]) -> float:
    """Per-trade Sharpe (mean / std of trade returns). NOT annualized -- it is a
    unit-free quality measure of the trade-return distribution."""
    r = np.asarray(list(returns), dtype=float)
    if len(r) < 2 or r.std(ddof=1) < 1e-12:
        return 0.0
    return round(float(r.mean() / r.std(ddof=1)), 3)


def sidak_adjust(p_value: float, n_trials: int) -> float:
    """Sidak family-wise correction: the probability that at least one of
    `n_trials` independent tests would beat this p-value by chance."""
    if n_trials <= 1:
        return round(p_value, 4)
    return round(1.0 - (1.0 - p_value) ** n_trials, 4)


def buy_and_hold(bars: pd.DataFrame) -> dict:
    """Benchmark: total return and annualized Sharpe of holding `bars` (OHLCV)."""
    if bars is None or "close" not in bars or len(bars["close"].dropna()) < 2:
        return {"total_return": 0.0, "sharpe": 0.0}
    close = bars["close"].dropna()
    rets = close.pct_change().dropna()
    total = float(close.iloc[-1] / close.iloc[0] - 1.0)
    sharpe = float(rets.mean() / rets.std() * math.sqrt(_TRADING_DAYS)) if rets.std() > 0 else 0.0
    return {"total_return": round(total, 4), "sharpe": round(sharpe, 2)}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
