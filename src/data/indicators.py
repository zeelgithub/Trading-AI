"""
Indicators -- data layer.

Pure, causal indicator functions: EMA (20/50/200), SMA, RSI, ATR, ADX,
Bollinger Bands, volume moving average. Implemented directly on pandas/numpy
(no third-party TA lib) so behaviour is explicit and testable.

All functions are causal: the value at row i depends only on rows <= i
(no look-ahead). RSI/ATR/ADX use Wilder smoothing, the genre standard.

Boundary: pure functions, no I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing == EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # When average loss is zero (only gains), RSI is 100 by definition.
    out = out.where(avg_loss != 0, 100.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """Return a frame with plus_di, minus_di, adx."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    atr_ = _wilder(true_range(high, low, close), period)
    plus_di = 100.0 * (_wilder(plus_dm, period) / atr_)
    minus_di = 100.0 * (_wilder(minus_dm, period) / atr_)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_ = _wilder(dx, period)

    return pd.DataFrame(
        {"plus_di": plus_di, "minus_di": minus_di, "adx": adx_}
    )


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    """Return a frame with bb_mid, bb_upper, bb_lower."""
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": mid + num_std * std,
            "bb_lower": mid - num_std * std,
        }
    )


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return sma(volume, period)
