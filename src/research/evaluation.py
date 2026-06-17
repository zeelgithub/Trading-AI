"""
Strategy evaluation pipeline -- research layer (offline).

Ties the backtester, per-strategy attribution, and significance tests into a
single verdict per strategy: is its edge real, or noise? It reuses the SAME
components the live bot trades with (via the backtester), so the verdict is
about the real logic, not a re-implementation.

Output is a list of StrategyScore rows (persisted to the scoreboard) plus a
portfolio-vs-benchmark comparison. Nothing here touches the live broker.

Boundary: offline; places orders NO.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from src.common.config import Config, load_config
from src.research import attribution, significance
from src.research.backtester import Backtester
from src.research.scoreboard import VERDICT_RANK, StrategyScore, classify

_PF_CLAMP = 999.0


@dataclass
class EvaluationReport:
    scores: list[StrategyScore]
    portfolio_metrics: dict
    benchmark: dict | None
    benchmark_symbol: str | None
    n_trials: int

    def text(self) -> str:
        lines = ["STRATEGY EVALUATION", "=" * 78]
        header = (f"{'strategy':<16}{'trades':>7}{'win%':>7}{'PF':>7}"
                  f"{'sharpe':>8}{'p(adj)':>8}{'PSR':>7}{'consist':>9}  verdict")
        lines.append(header)
        lines.append("-" * 78)
        for s in self.scores:
            cons = "n/a" if s.consistency is None else f"{s.consistency:.2f}"
            pf = "inf" if s.profit_factor >= _PF_CLAMP else f"{s.profit_factor:.2f}"
            lines.append(
                f"{s.strategy:<16}{s.num_trades:>7}{s.win_rate*100:>6.1f}{pf:>7}"
                f"{s.sharpe:>8.2f}{s.p_value_adjusted:>8.3f}{s.psr:>7.2f}{cons:>9}"
                f"  {s.verdict.upper()}"
            )
        lines.append("-" * 78)
        pm = self.portfolio_metrics
        lines.append(
            f"portfolio: return {pm.get('total_return', 0)*100:+.1f}%  "
            f"Sharpe {pm.get('sharpe', 0):.2f}  maxDD {pm.get('max_drawdown', 0)*100:.1f}%  "
            f"trades {pm.get('num_trades', 0)}"
        )
        if self.benchmark is not None:
            lines.append(
                f"benchmark {self.benchmark_symbol}: return "
                f"{self.benchmark['total_return']*100:+.1f}%  Sharpe {self.benchmark['sharpe']:.2f}  "
                "(buy & hold, same window)"
            )
        lines.append(f"\nmultiple-testing: {self.n_trials} strategy trial(s) -> p-values Sidak-adjusted.")
        lines.append("NOTE: verdicts are from BACKTEST data. 'validated' is a floor to clear, "
                     "not a green light to go live.")
        return "\n".join(lines)


def evaluate_strategies(
    features_by_symbol: dict[str, pd.DataFrame],
    config: Config | None = None,
    *,
    benchmark_symbol: str | None = "SPY",
    n_bootstrap: int = 5000,
    n_buckets: int = 4,
    initial_equity: float = 100_000.0,
    min_trades: int = 30,
    seed: int = 0,
) -> EvaluationReport:
    config = config or load_config()
    result = Backtester(config=config, initial_equity=initial_equity).run(features_by_symbol)

    breakdown = attribution.per_strategy_breakdown(result.trades)
    consistency = attribution.temporal_consistency(result.trades, n_buckets=n_buckets)

    rets_by_strategy: dict[str, list] = defaultdict(list)
    for t in result.trades:
        rets_by_strategy[t.strategy].append(float(t.return_pct))

    n_trials = max(1, len(breakdown))
    now = datetime.now(timezone.utc).isoformat()
    scores: list[StrategyScore] = []
    for name, stats in breakdown.items():
        rets = rets_by_strategy[name]
        boot = significance.bootstrap_pvalue(rets, n_resamples=n_bootstrap, seed=seed)
        psr = significance.probabilistic_sharpe_ratio(rets)
        p_adj = significance.sidak_adjust(boot["p_value"], n_trials)
        cons = consistency.get(name, {}).get("consistency")
        pf = stats["profit_factor"]
        scores.append(StrategyScore(
            strategy=name,
            num_trades=stats["num_trades"],
            win_rate=stats["win_rate"],
            profit_factor=_PF_CLAMP if pf == float("inf") else pf,
            total_pnl=stats["total_pnl"],
            sharpe=significance.trade_sharpe(rets),
            bootstrap_p_value=boot["p_value"],
            p_value_adjusted=p_adj,
            psr=psr,
            consistency=cons,
            verdict=classify(stats["num_trades"], p_adj, psr, cons, min_trades=min_trades),
            updated=now,
        ))

    benchmark = None
    if benchmark_symbol and benchmark_symbol in features_by_symbol:
        benchmark = significance.buy_and_hold(features_by_symbol[benchmark_symbol])

    scores.sort(key=lambda s: (VERDICT_RANK.get(s.verdict, 0), s.psr, s.total_pnl), reverse=True)
    return EvaluationReport(
        scores=scores,
        portfolio_metrics=result.metrics,
        benchmark=benchmark,
        benchmark_symbol=benchmark_symbol if benchmark else None,
        n_trials=n_trials,
    )
