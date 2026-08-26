"""Unit tests for the pure indicator functions.

These run with no network and no credentials -- just numpy/pandas. The most
important test is causality: an indicator value must not change when future
bars are appended (no look-ahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data import indicators as ind


@pytest.fixture
def close() -> pd.Series:
    # Deterministic pseudo-random walk.
    rng = np.random.default_rng(42)
    steps = rng.normal(0, 1, 300).cumsum()
    return pd.Series(100 + steps)


@pytest.fixture
def ohlc(close: pd.Series) -> pd.DataFrame:
    high = close + 1.0
    low = close - 1.0
    vol = pd.Series(np.linspace(1e6, 2e6, len(close)))
    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": vol})


def test_ema_of_constant_is_constant():
    s = pd.Series([5.0] * 50)
    out = ind.ema(s, 10).dropna()
    assert np.allclose(out.values, 5.0)


def test_sma_window():
    s = pd.Series(range(10), dtype=float)
    out = ind.sma(s, 3)
    assert out.iloc[2] == pytest.approx(1.0)  # mean(0,1,2)
    assert pd.isna(out.iloc[1])  # not enough data yet


def test_rsi_bounds_and_extremes():
    rising = pd.Series(range(1, 60), dtype=float)
    falling = pd.Series(range(60, 1, -1), dtype=float)
    r_up = ind.rsi(rising).dropna()
    r_dn = ind.rsi(falling).dropna()
    assert (r_up >= 0).all() and (r_up <= 100).all()
    assert r_up.iloc[-1] == pytest.approx(100.0)  # only gains
    assert r_dn.iloc[-1] == pytest.approx(0.0)  # only losses


def test_rsi_frozen_series_is_nan_not_100():
    """Regression guard: a fully frozen/stalled series (zero price movement
    at all -- avg_gain AND avg_loss both 0, not just avg_loss) used to be
    forced to RSI 100 ("extremely overbought") via the same avg_loss==0
    branch that correctly handles a genuine only-gains run. RSI is
    undefined for zero movement, not 100 -- left as NaN so has_nan() guards
    catch a stalled feed instead of trading on it."""
    frozen = pd.Series([100.0] * 30)
    out = ind.rsi(frozen)
    assert pd.isna(out.iloc[-1])


def test_atr_non_negative(ohlc):
    a = ind.atr(ohlc["high"], ohlc["low"], ohlc["close"]).dropna()
    assert (a >= 0).all()


def test_adx_in_range(ohlc):
    df = ind.adx(ohlc["high"], ohlc["low"], ohlc["close"]).dropna()
    assert (df["adx"] >= 0).all() and (df["adx"] <= 100).all()


def test_bollinger_ordering(ohlc):
    bb = ind.bollinger(ohlc["close"]).dropna()
    assert (bb["bb_upper"] >= bb["bb_mid"]).all()
    assert (bb["bb_mid"] >= bb["bb_lower"]).all()


def test_no_lookahead_causality(ohlc):
    """An indicator value at row i must be identical whether or not future
    rows exist. This guards against accidental look-ahead leakage."""
    cut = 200
    full_rsi = ind.rsi(ohlc["close"])
    partial_rsi = ind.rsi(ohlc["close"].iloc[:cut])
    pd.testing.assert_series_equal(
        full_rsi.iloc[:cut], partial_rsi, check_names=False
    )

    full_atr = ind.atr(ohlc["high"], ohlc["low"], ohlc["close"])
    partial_atr = ind.atr(
        ohlc["high"].iloc[:cut], ohlc["low"].iloc[:cut], ohlc["close"].iloc[:cut]
    )
    pd.testing.assert_series_equal(
        full_atr.iloc[:cut], partial_atr, check_names=False
    )
