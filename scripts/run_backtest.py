"""
Entrypoint: backtest run.

Loads stored daily bars for the watchlist, builds features, and runs the
event-driven backtester. Prints headline metrics and the closed trades.
Offline / read-only -- never touches the broker.

Usage:
    python -m scripts.fetch_data       # first, to populate the local store
    python -m scripts.run_backtest
"""

from __future__ import annotations

from src.common.config import load_config
from src.data import store
from src.data.features import build_features
from src.research.backtester import Backtester


def main() -> None:
    config = load_config()
    conn = store.connect()

    features_by_symbol = {}
    for symbol in config.enabled_symbols():
        bars = store.load_bars(conn, symbol)
        if bars.empty:
            print(f"{symbol}: no stored bars (run scripts.fetch_data first)")
            continue
        features_by_symbol[symbol] = build_features(bars, config)
    conn.close()

    if not features_by_symbol:
        print("No data to backtest.")
        return

    bt = Backtester(config=config, initial_equity=100_000.0)
    result = bt.run(features_by_symbol)

    print("=== Backtest metrics ===")
    for k, v in result.metrics.items():
        print(f"  {k:<14} {v}")

    print(f"\n=== Trades ({len(result.trades)}) ===")
    for t in result.trades:
        print(
            f"  {t.symbol:<5} {t.side:<5} {t.strategy:<15} "
            f"{str(t.entry_date)[:10]} -> {str(t.exit_date)[:10]} "
            f"entry={t.entry_price} exit={t.exit_price} qty={t.qty:g} "
            f"pnl={t.pnl:+.2f} ({t.return_pct:+.2%}) [{t.reason}]"
        )


if __name__ == "__main__":
    main()
