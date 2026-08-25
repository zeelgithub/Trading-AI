"""Volatility source: percentile-ranks the given universe by ATR-as-%-of-price
-- relative to today's screen, not an absolute threshold -- and skips symbols
with no usable atr/close data rather than crashing the run."""

from __future__ import annotations

import pandas as pd

from src.discovery.sources.volatility import VolatilitySource


def _feats(close: float, atr: float | None) -> pd.DataFrame:
    row = {"close": close}
    if atr is not None:
        row["atr"] = atr
    return pd.DataFrame([row])


def _provider(by_symbol: dict[str, pd.DataFrame]):
    return lambda sym: by_symbol.get(sym, pd.DataFrame())


def test_most_volatile_symbol_scores_highest():
    by_symbol = {
        "CALM": _feats(close=100.0, atr=1.0),    # ATR 1.0%
        "MID": _feats(close=100.0, atr=3.0),     # ATR 3.0%
        "WILD": _feats(close=100.0, atr=8.0),    # ATR 8.0%
    }
    src = VolatilitySource(feature_provider=_provider(by_symbol), universe=list(by_symbol))
    out = {c.symbol: c for c in src.gather()}
    assert out["WILD"].score > out["MID"].score > out["CALM"].score
    assert out["WILD"].score == 1.0   # top of the percentile ranking


def test_reason_and_meta_carry_atr_pct():
    by_symbol = {"AAA": _feats(close=50.0, atr=2.5)}  # ATR 5.0% of price
    c = VolatilitySource(feature_provider=_provider(by_symbol), universe=["AAA"]).gather()[0]
    assert c.meta["atr_pct"] == 0.05
    assert "5.0%" in c.reason


def test_symbol_missing_atr_is_skipped_not_crashed():
    by_symbol = {
        "HASDATA": _feats(close=100.0, atr=2.0),
        "NOATR": _feats(close=100.0, atr=None),
    }
    out = {c.symbol: c for c in VolatilitySource(
        feature_provider=_provider(by_symbol), universe=list(by_symbol)).gather()}
    assert "HASDATA" in out
    assert "NOATR" not in out


def test_symbol_with_zero_close_is_skipped():
    by_symbol = {"ZERO": _feats(close=0.0, atr=1.0)}
    assert VolatilitySource(feature_provider=_provider(by_symbol), universe=["ZERO"]).gather() == []


def test_feature_provider_exception_is_skipped_not_crashed():
    def bad_provider(sym):
        if sym == "BROKEN":
            raise RuntimeError("no data")
        return _feats(close=100.0, atr=2.0)

    out = VolatilitySource(feature_provider=bad_provider, universe=["BROKEN", "OK"]).gather()
    assert [c.symbol for c in out] == ["OK"]


def test_empty_universe_returns_empty():
    assert VolatilitySource(feature_provider=lambda s: pd.DataFrame(), universe=[]).gather() == []
