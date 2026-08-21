"""
Entrypoint: evaluate the strategies -- signal vs. noise (offline research).

Runs a point-in-time-correct backtest over your watchlist using the SAME
strategy / regime / risk / ratchet components the live bot uses, then asks of
each strategy: is its edge statistically real, consistent over time, and better
than buy-and-hold? Writes the verdict to the scoreboard (state/scoreboard.json).

    python -m scripts.evaluate_strategies                 # watchlist + SPY, refresh data
    python -m scripts.evaluate_strategies --offline       # use only cached bars (no creds)
    python -m scripts.evaluate_strategies --symbols AAPL MSFT NVDA --no-save

This NEVER touches the broker and places no orders. A "validated" verdict is a
floor a strategy must clear -- not authorization to trade live.
"""

from __future__ import annotations

import argparse

import pandas as pd

from src.common.config import load_config
from src.common.logging import get_logger
from src.data import store
from src.data.features import build_features
from src.research.evaluation import evaluate_strategies
from src.research.scoreboard import Scoreboard
from src.research.walkforward import evaluate_walk_forward

log = get_logger("evaluate")

_BENCHMARK = "SPY"


def _build_features(
    symbols: list[str], lookback_days: int, offline: bool, full_refetch: bool = False
) -> dict[str, pd.DataFrame]:
    conn = store.connect()
    config = load_config()
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if not offline:
            try:
                from src.data.ingest import ingest_symbol
                rep = ingest_symbol(
                    conn, sym, lookback_days=lookback_days, incremental=not full_refetch
                )
                log.info("ingested %s: %d rows (%d gaps)", sym, rep.rows, rep.gap_count)
            except Exception as exc:  # data/creds problem -- fall back to cache
                log.warning("ingest failed for %s (%s); using cached bars", sym, exc)
        feats = build_features(store.load_bars(conn, sym), config)
        if feats.empty:
            log.warning("no bars for %s -- skipping", sym)
            continue
        out[sym] = feats
    return out


def main() -> None:
    config = load_config()
    # Default to the wider research universe (settings.research.backtest_universe)
    # when set -- 3 live-watchlist symbols produce too few trades to tell a real
    # edge from noise against the pipeline's own min_trades bar. Falls back to
    # the watchlist if the research universe isn't configured.
    default_symbols = list(config.get("settings.research.backtest_universe", None)
                           or config.enabled_symbols())
    default_lookback = int(config.get("settings.data.lookback_days", 400))

    parser = argparse.ArgumentParser(description="Evaluate strategies: signal vs. noise.")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=f"symbols to evaluate (default: research universe, "
                             f"{len(default_symbols)} symbols)")
    parser.add_argument("--lookback-days", type=int, default=default_lookback)
    parser.add_argument("--offline", action="store_true", help="use only cached bars; no data fetch")
    parser.add_argument("--full-refetch", action="store_true",
                        help="ignore the incremental cache and pull the full --lookback-days "
                             "window fresh (use this to widen history; plain runs only fetch "
                             "since the newest cached bar regardless of --lookback-days)")
    parser.add_argument("--bootstrap", type=int, default=5000, help="bootstrap resamples")
    parser.add_argument("--buckets", type=int, default=4, help="time-buckets for consistency")
    parser.add_argument("--min-trades", type=int, default=30, help="trades required for 'validated'")
    parser.add_argument("--no-benchmark", action="store_true", help="skip SPY benchmark comparison")
    parser.add_argument("--no-save", action="store_true", help="don't write the scoreboard")
    parser.add_argument("--folds", type=int, default=3,
                        help="walk-forward folds (default 3); the last fold is the "
                             "closest thing this project has to a real out-of-sample holdout")
    parser.add_argument("--no-walk-forward", action="store_true",
                        help="skip the walk-forward pass (it reuses the same features, "
                             "but re-runs the backtest once per fold, so it's slower)")
    args = parser.parse_args()

    symbols = list(args.symbols or default_symbols)
    benchmark = None if args.no_benchmark else _BENCHMARK
    if benchmark and benchmark not in symbols:
        symbols.append(benchmark)

    if not symbols:
        print("No symbols to evaluate. Add a watchlist in config/symbols.yaml or pass --symbols.")
        return

    print(f"Building features for {len(symbols)} symbol(s)"
          f"{' (offline)' if args.offline else ''}...")
    features = _build_features(symbols, args.lookback_days, args.offline, args.full_refetch)
    if not features:
        print("No usable bar data. Run once without --offline to populate the cache, "
              "or check your data credentials.")
        return

    report = evaluate_strategies(
        features,
        config=config,
        benchmark_symbol=benchmark,
        n_bootstrap=args.bootstrap,
        n_buckets=args.buckets,
        min_trades=args.min_trades,
    )

    print()
    print(report.text())

    if not args.no_walk_forward:
        wf_report = evaluate_walk_forward(
            features, config=config, n_folds=args.folds, n_bootstrap=args.bootstrap,
        )
        print()
        print(wf_report.text())

    if not args.no_save:
        sb = Scoreboard()
        for score in report.scores:
            sb.upsert(score)
        print(f"\nScoreboard updated: {sb.path}")


if __name__ == "__main__":
    main()
