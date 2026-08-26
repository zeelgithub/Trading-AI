"""
Entrypoint (RESEARCH ONLY): evaluate the 52-week-high anchoring momentum
candidate (src/strategy/week52_high.py).

Runs the SAME rigor trend_following and the cross-sectional momentum
candidate were held to -- in-sample significance (bootstrap p-value, PSR,
temporal consistency) AND walk-forward out-of-sample folds -- against the
SAME survivorship-bias-corrected universe and lookback used for those studies
(settings.research.backtest_universe, 1500 days), so the numbers are directly
comparable to docs/STRATEGIES.md's existing tables.

Unlike scripts/research_momentum.py, this candidate needs no cross-sectional
precompute step (src/research/cross_sectional.py) -- the 52-week-high anchor
is each symbol's OWN price history, read straight off features's raw
high/low/close columns exactly like src/strategy/breakout.py's
support/resistance window. So this script is simpler: build features, force
this one strategy, evaluate.

This NEVER touches the broker, never touches config/*.yaml on disk, and
registers "week52_high" into the strategy registry only for this process's
own lifetime (see the REGISTRY mutation below) -- the live orchestrator,
phone, and discovery are completely unaffected whether or not this script has
ever been run.

    python -m scripts.research_week52_high                  # refresh + evaluate
    python -m scripts.research_week52_high --offline        # cached bars only
    python -m scripts.research_week52_high --no-walk-forward
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from src.common.config import load_config
from src.common.logging import get_logger
from src.data import store
from src.data.features import build_features
from src.research import attribution, significance
from src.research.backtester import Backtester
from src.research.scoreboard import PF_CLAMP, classify
from src.research.walkforward import evaluate_walk_forward
from src.strategy.registry import REGISTRY
from src.strategy.week52_high import Week52High

log = get_logger("research_week52_high")


def _build_features(symbols: list[str], lookback_days: int, offline: bool) -> dict[str, pd.DataFrame]:
    conn = store.connect()
    config = load_config()
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if not offline:
            try:
                from src.data.ingest import ingest_symbol
                rep = ingest_symbol(conn, sym, lookback_days=lookback_days, incremental=True)
                log.info("ingested %s: %d rows (%d gaps)", sym, rep.rows, rep.gap_count)
            except Exception as exc:  # data/creds problem -- fall back to cache
                log.warning("ingest failed for %s (%s); using cached bars", sym, exc)
        feats = build_features(store.load_bars(conn, sym), config)
        if feats.empty:
            log.warning("no bars for %s -- skipping", sym)
            continue
        out[sym] = feats
    return out


def _research_config(base_config, anchor_lookback_bars: int, proximity_pct: float,
                      atr_multiple_initial: float = 2.0, atr_multiple_trail: float = 1.5):
    """A config seeded with a week52_high block, in-memory only -- never
    written to config/*.yaml. build_strategies() reads
    config.strategies.strategies to decide which registered strategies to
    instantiate; the ratchet stop builder separately reads
    config.risk_limits.ratchet_stop -- week52_high needs an entry in both, or
    Backtester._fill_entry crashes building a ratchet for a strategy with no
    block. ATR multiples default to breakout's own (tested, unchanged)
    values -- a reasonable starting point for another ATR-ratchet,
    event-triggered strategy, not yet independently tuned."""
    strategies = {
        **base_config.strategies,
        "strategies": {
            **base_config.strategies["strategies"],
            "week52_high": {
                "indicators": {"anchor_lookback_bars": anchor_lookback_bars},
                "conditions": {"proximity_pct": proximity_pct},
            },
        },
    }
    risk_limits = {
        **base_config.risk_limits,
        "ratchet_stop": {
            **base_config.risk_limits.get("ratchet_stop", {}),
            "week52_high": {
                "atr_multiple_initial": atr_multiple_initial,
                "atr_multiple_trail": atr_multiple_trail,
            },
        },
    }
    return replace(base_config, strategies=strategies, risk_limits=risk_limits)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the 52-week-high anchoring momentum candidate.")
    parser.add_argument("--offline", action="store_true", help="use only cached bars; no data fetch")
    parser.add_argument("--lookback-days", type=int, default=1500,
                        help="matches the trend_following validated study (docs/STRATEGIES.md)")
    parser.add_argument("--anchor-days", type=int, default=252, help="52-week anchor window (trading days)")
    parser.add_argument("--proximity-pct", type=float, default=5.0,
                        help="how close to the anchor counts as 'near' it")
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--no-walk-forward", action="store_true")
    parser.add_argument("--bootstrap", type=int, default=5000)
    args = parser.parse_args()

    base_config = load_config()
    universe = list(base_config.get("settings.research.backtest_universe", []))
    if not universe:
        print("No settings.research.backtest_universe configured -- nothing to evaluate against.")
        return

    print(f"Building features for {len(universe)} symbol(s){' (offline)' if args.offline else ''}...")
    features = _build_features(universe, args.lookback_days, args.offline)
    if not features:
        print("No usable bar data. Run once without --offline to populate the cache.")
        return

    # Opt-in registration, this process only -- see module docstring.
    REGISTRY["week52_high"] = Week52High
    config = _research_config(base_config, args.anchor_days, args.proximity_pct)

    print(f"\nRunning in-sample backtest (anchor={args.anchor_days}d, "
          f"proximity={args.proximity_pct:.1f}%)...")
    result = Backtester(config=config).run(features, force_strategy="week52_high")
    trades = [t for t in result.trades if t.strategy == "week52_high"]
    rets = [float(t.return_pct) for t in trades]
    stats = attribution.per_strategy_breakdown(trades).get("week52_high", {
        "num_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0,
    })
    consistency = attribution.temporal_consistency(trades, n_buckets=4).get("week52_high", {})

    boot = significance.bootstrap_pvalue(rets, n_resamples=args.bootstrap)
    psr = significance.probabilistic_sharpe_ratio(rets)
    p_adj = boot["p_value"]  # standalone trial, not part of any Sidak family below
    cons = consistency.get("consistency")
    pf = stats["profit_factor"]
    verdict = classify(stats["num_trades"], p_adj, psr, cons, min_trades=30)

    print("\n" + "=" * 78)
    print("WEEK52_HIGH -- IN-SAMPLE RESULT")
    print("=" * 78)
    print(f"{'trades':<10}{'win%':<10}{'PF':<10}{'sharpe':<10}{'p-value':<10}{'PSR':<10}{'consist':<10}verdict")
    pf_disp = "inf" if pf == float("inf") or pf >= PF_CLAMP else f"{pf:.2f}"
    cons_disp = "n/a" if cons is None else f"{cons:.2f}"
    print(f"{stats['num_trades']:<10}{stats['win_rate']*100:<10.1f}{pf_disp:<10}"
          f"{significance.trade_sharpe(rets):<10.2f}{boot['p_value']:<10.3f}{psr:<10.2f}"
          f"{cons_disp:<10}{verdict.upper()}")
    print("\nNOTE: p-value here is a STANDALONE trial (n=1), not Sidak-adjusted alongside "
          "the original 3-strategy study or the momentum candidate -- if you consider this a "
          "5th trial in that same family, the honest comparison is Sidak-adjusted for n=5, "
          "which is stricter than what's shown above.")
    print("Verdict is from BACKTEST data. 'validated' is a floor to clear, not a green light "
          "to go live -- same standard applied to trend_following.")

    if not args.no_walk_forward:
        print("\nRunning walk-forward...")
        wf = evaluate_walk_forward(
            features, config, n_folds=args.folds, n_bootstrap=args.bootstrap,
            force_strategy="week52_high")
        wf.folds = [f for f in wf.folds if f.strategy == "week52_high"]
        print("\n" + wf.text())


if __name__ == "__main__":
    main()
