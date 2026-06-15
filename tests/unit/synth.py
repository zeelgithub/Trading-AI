"""Synthetic feature-frame builders for strategy/regime unit tests.

Tests set indicator columns directly (rather than computing them) so each
strategy's *decision logic* is exercised in isolation, deterministically.
"""

from __future__ import annotations

import pandas as pd

COLS = [
    "open", "high", "low", "close", "volume",
    "ema20", "ema50", "ema200", "rsi", "atr",
    "plus_di", "minus_di", "adx", "bb_mid", "bb_upper", "bb_lower", "vol_sma",
]


def _default_row(value: float, volume: float) -> dict:
    return {
        "open": value, "high": value, "low": value, "close": value, "volume": volume,
        "ema20": value, "ema50": value, "ema200": value, "rsi": 50.0, "atr": 1.0,
        "plus_di": 20.0, "minus_di": 20.0, "adx": 20.0,
        "bb_mid": value, "bb_upper": value + 2, "bb_lower": value - 2, "vol_sma": volume,
    }


def make_features(rows: list[dict]) -> pd.DataFrame:
    """Build a frame from partial row dicts; missing fields get defaults."""
    data = []
    for r in rows:
        base = _default_row(r.get("close", 100.0), r.get("volume", 1e6))
        base.update(r)
        data.append(base)
    return pd.DataFrame(data, columns=COLS)


def flat_frame(n: int, value: float = 100.0, volume: float = 1e6, **last_overrides) -> pd.DataFrame:
    """n flat bars; override columns on the final bar via kwargs."""
    df = make_features([_default_row(value, volume) for _ in range(n)])
    for col, val in last_overrides.items():
        df.iloc[-1, df.columns.get_loc(col)] = val
    return df
