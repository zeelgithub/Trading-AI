"""
Market-data read queries -- data layer.

Compact, JSON-friendly snapshots of cached market data for the cognitive plane.
Reads the local bar cache (no broker, no credentials) and the causal feature
frame, returning small dicts so an agent never has to ingest raw OHLCV dumps --
this is what keeps an agent's context small.

These functions are the single source of truth shared by the market_data MCP
server and the in-process read tools (src/agents/tools/reads.py).

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

import pandas as pd

from src.common.config import Config, load_config
from src.data import store
from src.data.features import build_features
from src.strategy.regime_filter import RegimeFilter


def recent_bars(symbol: str, days: int = 20, *, config: Config | None = None, conn=None) -> dict:
    """The most recent `days` daily OHLCV bars for a symbol (oldest first)."""
    symbol = symbol.upper()  # store keys are uppercase; agents may pass any case
    conn = conn or store.connect()
    bars = store.load_bars(conn, symbol)
    if bars.empty:
        return {"symbol": symbol, "bars": [], "note": "no cached bars"}
    rows = [
        {
            "date": _date(ts),
            "open": round(float(r.open), 2),
            "high": round(float(r.high), 2),
            "low": round(float(r.low), 2),
            "close": round(float(r.close), 2),
            "volume": int(r.volume),
        }
        for ts, r in bars.tail(max(1, days)).iterrows()
    ]
    return {"symbol": symbol.upper(), "bars": rows}


def indicator_snapshot(symbol: str, *, config: Config | None = None, conn=None) -> dict:
    """Latest causal indicator values + regime classification for a symbol."""
    symbol = symbol.upper()  # store keys are uppercase; agents may pass any case
    config = config or load_config()
    conn = conn or store.connect()
    feats = build_features(store.load_bars(conn, symbol), config)
    if feats.empty:
        return {"symbol": symbol, "note": "no cached bars"}

    last = feats.iloc[-1]
    regime = RegimeFilter(config).classify(feats)

    def g(col: str):
        v = last.get(col)
        return None if v is None or pd.isna(v) else round(float(v), 4)

    return {
        "symbol": symbol.upper(),
        "date": _date(feats.index[-1]),
        "close": g("close"),
        "ema20": g("ema20"),
        "ema50": g("ema50"),
        "ema200": g("ema200"),
        "rsi": g("rsi"),
        "atr": g("atr"),
        "adx": g("adx"),
        "bb_upper": g("bb_upper"),
        "bb_lower": g("bb_lower"),
        "regime": regime.value,
    }


def _date(ts) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)
