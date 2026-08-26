"""
Entrypoint: data-pipeline smoke run.

Fetches daily bars for each enabled symbol, stores them in the local SQLite
cache, builds the indicator feature frame, and prints a one-line summary per
symbol. Read-only with respect to the broker -- never places an order.

Usage:
    python -m scripts.fetch_data
"""

from __future__ import annotations

from src.common.config import load_config
from src.data import store
from src.data.features import build_features
from src.data.ingest import ingest_symbol


def main() -> None:
    config = load_config()
    lookback = config.data_lookback_days()
    symbols = config.enabled_symbols()

    conn = store.connect()
    print(f"Ingesting {len(symbols)} symbol(s), lookback={lookback}d\n")

    for symbol in symbols:
        report = ingest_symbol(conn, symbol, lookback_days=lookback)
        bars = store.load_bars(conn, symbol)
        feats = build_features(bars, config)
        last = feats.iloc[-1]
        print(
            f"{symbol:<6} rows={report.rows:<4} gaps={report.gap_count} "
            f"close={last['close']:.2f} "
            f"ema50={last['ema50']:.2f} ema200={last['ema200']:.2f} "
            f"rsi={last['rsi']:.1f} adx={last['adx']:.1f}"
        )

    conn.close()


if __name__ == "__main__":
    main()
