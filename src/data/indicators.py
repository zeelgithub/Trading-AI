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
    # avg_loss == 0 is "only gains" (RSI 100 by definition) ONLY when there
    # were gains at all. A fully frozen series (avg_gain == 0 too -- a
    # stalled/duplicated-close feed, zero movement whatsoever) previously
    # also hit this branch via 0/0 == NaN, forcing RSI to 100 ("extremely
    # overbought") instead of leaving it undefined -- left as NaN here so
    # has_nan() guards catch a frozen feed like any other missing indicator,
    # instead of a stale feed masquerading as a real signal (confirmed
    # reachable: mean_reversion's touched_short also collapses bb_upper to
    # close on a zero-variance window, so both halves of its overbought
    # check would trip together on nothing but stale data).
    only_gains = (avg_loss == 0) & (avg_gain > 0)
    out = out.where(~only_gains, 100.0)
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

    total_di = plus_di + minus_di
    # Guard against 0/0 on flat bars (both DIs zero); NaN propagates safely through
    # Wilder smoothing and is caught by has_nan() checks in the strategy layer.
    dx = 100.0 * (plus_di - minus_di).abs() / total_di.where(total_di != 0, np.nan)
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
