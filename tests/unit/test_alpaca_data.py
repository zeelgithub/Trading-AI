"""Unit tests for src/data/providers/alpaca_data.py.

The real alpaca-py client is never hit -- `_get_client()` is monkeypatched to
a fake object exposing only `get_stock_bars`, mirroring how the production
code uses it (a `.df` attribute on the response). Credentials are stubbed so
these tests need no network and no .env.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.secrets import MarketDataCredentials
from src.data.providers.alpaca_data import AlpacaData, _OHLCV


def _creds() -> MarketDataCredentials:
    return MarketDataCredentials(key_id="k", secret_key="s", base_url="https://data.alpaca.markets", feed="iex")


class _FakeBars:
    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df


class _FakeClient:
    def __init__(self, df: pd.DataFrame, exc_sequence: list[Exception] | None = None) -> None:
        self._df = df
        self._exc_sequence = list(exc_sequence or [])
        self.calls = 0

    def get_stock_bars(self, request):
        self.calls += 1
        if self._exc_sequence:
            raise self._exc_sequence.pop(0)
        return _FakeBars(self._df)


def _multiindex_df(symbols: list[str], rows_per_symbol: int = 2) -> pd.DataFrame:
    idx_tuples = []
    data = []
    for sym in symbols:
        for i in range(rows_per_symbol):
            ts = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i)
            idx_tuples.append((sym, ts))
            data.append([100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000 + i])
    index = pd.MultiIndex.from_tuples(idx_tuples, names=["symbol", "timestamp"])
    return pd.DataFrame(data, index=index, columns=_OHLCV)


def _install_fake_client(provider: AlpacaData, client: _FakeClient) -> None:
    provider._get_client = lambda: client  # bypass alpaca-py entirely


def test_get_daily_bars_multi_empty_symbols_short_circuits() -> None:
    provider = AlpacaData(creds=_creds())
    assert provider.get_daily_bars_multi([]) == {}


def test_get_daily_bars_multi_parses_multiindex_response() -> None:
    provider = AlpacaData(creds=_creds())
    df = _multiindex_df(["AAPL", "MSFT"])
    client = _FakeClient(df)
    _install_fake_client(provider, client)

    out = provider.get_daily_bars_multi(["AAPL", "MSFT"])

    assert set(out) == {"AAPL", "MSFT"}
    for sym, frame in out.items():
        assert list(frame.columns) == _OHLCV
        assert len(frame) == 2
        assert frame.index.name == "ts"
        assert str(frame.index.tz) == "UTC"


def test_get_daily_bars_multi_missing_symbol_returns_empty_frame() -> None:
    provider = AlpacaData(creds=_creds())
    df = _multiindex_df(["AAPL"])
    client = _FakeClient(df)
    _install_fake_client(provider, client)

    out = provider.get_daily_bars_multi(["AAPL", "GHOST"])

    assert len(out["AAPL"]) == 2
    assert out["GHOST"].empty
    assert list(out["GHOST"].columns) == _OHLCV


def test_get_daily_bars_multi_empty_response_returns_empty_frame_per_symbol() -> None:
    provider = AlpacaData(creds=_creds())
    client = _FakeClient(pd.DataFrame(columns=_OHLCV))
    _install_fake_client(provider, client)

    out = provider.get_daily_bars_multi(["AAPL", "MSFT"])

    assert set(out) == {"AAPL", "MSFT"}
    assert all(frame.empty for frame in out.values())


def test_get_daily_bars_single_symbol_delegates_to_multi() -> None:
    provider = AlpacaData(creds=_creds())
    df = _multiindex_df(["AAPL"])
    client = _FakeClient(df)
    _install_fake_client(provider, client)

    frame = provider.get_daily_bars("AAPL")

    assert len(frame) == 2
    assert list(frame.columns) == _OHLCV


def test_get_daily_bars_single_symbol_missing_returns_empty_frame() -> None:
    provider = AlpacaData(creds=_creds())
    client = _FakeClient(pd.DataFrame(columns=_OHLCV))
    _install_fake_client(provider, client)

    frame = provider.get_daily_bars("GHOST")

    assert frame.empty
    assert list(frame.columns) == _OHLCV


def test_get_daily_bars_multi_retries_transient_error_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("src.common.errors.time.sleep", lambda _: None)
    provider = AlpacaData(creds=_creds())
    df = _multiindex_df(["AAPL"])
    client = _FakeClient(df, exc_sequence=[ConnectionError("blip")])
    _install_fake_client(provider, client)

    out = provider.get_daily_bars_multi(["AAPL"])

    assert client.calls == 2
    assert len(out["AAPL"]) == 2


def test_get_daily_bars_multi_does_not_retry_non_transient_error(monkeypatch) -> None:
    monkeypatch.setattr("src.common.errors.time.sleep", lambda _: None)
    provider = AlpacaData(creds=_creds())
    client = _FakeClient(pd.DataFrame(), exc_sequence=[ValueError("bad request")])
    _install_fake_client(provider, client)

    with pytest.raises(ValueError):
        provider.get_daily_bars_multi(["AAPL"])

    assert client.calls == 1
