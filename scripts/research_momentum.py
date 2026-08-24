"""
Entrypoint (RESEARCH ONLY): evaluate the cross-sectional momentum candidate.

Runs the SAME rigor trend_following was held to -- in-sample significance
(bootstrap p-value, PSR, temporal consistency) AND walk-forward out-of-sample
folds -- against the SAME survivorship-bias-corrected universe and lookback
used for the validated study (settings.research.backtest_universe, 1500
days), so the numbers are directly comparable to docs/STRATEGIES.md's
existing table.

This NEVER touches the broker, never touches config/*.yaml on disk, and
registers "momentum" into the strategy registry only for this process's own
lifetime (see the REGISTRY mutation below) -- the live orchestrator, phone,
and discovery are completely unaffected whether or not this script has ever
been run.

    python -m scripts.research_momentum                  # refresh + evaluate
    python -m scripts.research_momentum --offline        # cached bars only
    python -m scripts.research_momentum --no-walk-forward
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
from src.research.cross_sectional import add_cross_sectional_momentum
from src.research.scoreboard import classify
from src.research.walkforward import evaluate_walk_forward
from src.strategy.momentum import Momentum
from src.strategy.registry import REGISTRY

log = get_logger("research_momentum")
_PF_CLAMP = 999.0


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


def _research_config(base_config, initial_stop_pct: float = 10.0):
    """A config seeded with a momentum block, in-memory only -- never written
    to config/*.yaml. build_strategies() reads config.strategies.strategies
    to decide which registered strategies to instantiate; the ratchet stop
    builder separately reads config.risk_limits.ratchet_stop -- momentum
    needs an entry in both, or Backtester._fill_entry crashes on a bare
    dict.__getitem__ trying to build a ratchet for a strategy with no block."""
    strategies = {
        **base_config.strategies,
        "strategies": {**base_config.strategies["strategies"], "momentum": {}},
    }
    risk_limits = {
        **base_config.risk_limits,
        "ratchet_stop": {
            **base_config.risk_limits.get("ratchet_stop", {}),
            "momentum": {"initial_stop_pct": initial_stop_pct},
        },
    }
    return replace(base_config, strategies=strategies, risk_limits=risk_limits)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the cross-sectional momentum candidate.")
    parser.add_argument("--offline", action="store_true", help="use only cached bars; no data fetch")
    parser.add_argument("--lookback-days", type=int, default=1500,
                        help="matches the trend_following validated study (docs/STRATEGIES.md)")
    parser.add_argument("--formation-days", type=int, default=126, help="momentum formation window (~6mo)")
    parser.add_argument("--skip-days", type=int, default=21, help="skip most recent ~1mo (reversal effect)")
    parser.add_argument("--top-pct", type=float, default=0.2, help="top/bottom bucket size, e.g. 0.2 = quintile")
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

    print(f"Computing cross-sectional momentum (formation={args.formation_days}d, "
          f"skip={args.skip_days}d, bucket={args.top_pct:.0%})...")
    features = add_cross_sectional_momentum(
        features, lookback=args.formation_days, skip=args.skip_days, top_pct=args.top_pct)

    # Opt-in registration, this process only -- see module docstring.
    REGISTRY["momentum"] = Momentum
    config = _research_config(base_config)

    print("\nRunning in-sample backtest...")
    result = Backtester(config=config).run(features, force_strategy="momentum")
    trades = [t for t in result.trades if t.strategy == "momentum"]
    rets = [float(t.return_pct) for t in trades]
    stats = attribution.per_strategy_breakdown(trades).get("momentum", {
        "num_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0,
    })
    consistency = attribution.temporal_consistency(trades, n_buckets=4).get("momentum", {})

    boot = significance.bootstrap_pvalue(rets, n_resamples=args.bootstrap)
    psr = significance.probabilistic_sharpe_ratio(rets)
    p_adj = boot["p_value"]  # n_trials=1: this is its own standalone trial, not part of the original 3
    cons = consistency.get("consistency")
    pf = stats["profit_factor"]
    verdict = classify(stats["num_trades"], p_adj, psr, cons, min_trades=30)

    print("\n" + "=" * 78)
    print("MOMENTUM -- IN-SAMPLE RESULT")
    print("=" * 78)
    print(f"{'trades':<10}{'win%':<10}{'PF':<10}{'sharpe':<10}{'p-value':<10}{'PSR':<10}{'consist':<10}verdict")
    pf_disp = "inf" if pf == float("inf") or pf >= _PF_CLAMP else f"{pf:.2f}"
    cons_disp = "n/a" if cons is None else f"{cons:.2f}"
    print(f"{stats['num_trades']:<10}{stats['win_rate']*100:<10.1f}{pf_disp:<10}"
          f"{significance.trade_sharpe(rets):<10.2f}{boot['p_value']:<10.3f}{psr:<10.2f}"
          f"{cons_disp:<10}{verdict.upper()}")
    print("\nNOTE: p-value here is a STANDALONE trial (n=1), not Sidak-adjusted alongside "
          "the original 3-strategy study -- if you consider this a 4th trial in that same "
          "family, the honest comparison is Sidak-adjusted for n=4, which is stricter than "
          "what's shown above.")
    print("Verdict is from BACKTEST data. 'validated' is a floor to clear, not a green light "
          "to go live -- same standard applied to trend_following.")

    if not args.no_walk_forward:
        print("\nRunning walk-forward...")
        wf = evaluate_walk_forward(
            features, config, n_folds=args.folds, n_bootstrap=args.bootstrap,
            force_strategy="momentum")
        wf.folds = [f for f in wf.folds if f.strategy == "momentum"]
        print("\n" + wf.text())


if __name__ == "__main__":
    main()
