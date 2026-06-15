"""
Feature engineering -- data layer.

Assembles the indicator feature frame consumed by the regime filter and
strategies. Every column is causal (no look-ahead): the value at row i uses
only bars <= i. Indicator periods are read from config/strategies.yaml.

Boundary: read-only.
"""

from __future__ import annotations

import pandas as pd

from src.common.config import Config, load_config
from src.data import indicators as ind


def build_features(bars: pd.DataFrame, config: Config | None = None) -> pd.DataFrame:
    """Return bars augmented with indicator columns used across the system.

    Columns added: ema20/50/200, rsi, atr, plus_di, minus_di, adx,
    bb_mid/upper/lower, vol_sma.
    """
    if bars.empty:
        return bars

    config = config or load_config()
    rf = config.strategies.get("regime_filter", {})
    mr = config.strategies.get("strategies", {}).get("mean_reversion", {})
    bo = config.strategies.get("strategies", {}).get("breakout", {})

    adx_period = int(rf.get("adx_period", 14))
    bb_period = int(mr.get("indicators", {}).get("bollinger_period", 20))
    bb_std = float(mr.get("indicators", {}).get("bollinger_std", 2.0))
    atr_period = int(bo.get("indicators", {}).get("atr_period", 14))
    vol_period = int(bo.get("indicators", {}).get("volume_ma_period", 20))

    out = bars.copy()
    close, high, low, vol = out["close"], out["high"], out["low"], out["volume"]

    out["ema20"] = ind.ema(close, 20)
    out["ema50"] = ind.ema(close, 50)
    out["ema200"] = ind.ema(close, 200)
    out["rsi"] = ind.rsi(close, 14)
    out["atr"] = ind.atr(high, low, close, atr_period)

    adx_df = ind.adx(high, low, close, adx_period)
    out = out.join(adx_df)

    bb_df = ind.bollinger(close, bb_period, bb_std)
    out = out.join(bb_df)

    out["vol_sma"] = ind.volume_sma(vol, vol_period)
    return out
