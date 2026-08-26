"""Unit tests for src/data/providers/news.py's AlpacaNews.

The real alpaca-py client is never hit -- `_get_client()` is monkeypatched to
a fake object exposing only `get_news`, mirroring test_alpaca_data.py's
pattern for the bar-fetch provider. Credentials are stubbed so these tests
need no network and no .env.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.common.secrets import MarketDataCredentials
from src.data.providers.news import AlpacaNews


def _creds() -> MarketDataCredentials:
    return MarketDataCredentials(key_id="k", secret_key="s", base_url="https://data.alpaca.markets", feed="iex")


class _FakeClient:
    def __init__(self, items: list, exc_sequence: list[Exception] | None = None) -> None:
        self._items = items
        self._exc_sequence = list(exc_sequence or [])
        self.calls = 0

    def get_news(self, request):
        self.calls += 1
        if self._exc_sequence:
            raise self._exc_sequence.pop(0)
        return SimpleNamespace(news=self._items)


def _install_fake_client(provider: AlpacaNews, client: _FakeClient) -> None:
    provider._client = client


def _headline(text: str) -> SimpleNamespace:
    return SimpleNamespace(headline=text, summary="", created_at=None, symbols=("AAPL",))


def test_fetch_headlines_maps_response_items():
    provider = AlpacaNews(creds=_creds())
    _install_fake_client(provider, _FakeClient([_headline("AAPL beats earnings")]))

    out = provider.fetch_headlines("AAPL")

    assert len(out) == 1
    assert out[0].headline == "AAPL beats earnings"
    assert out[0].symbol == "AAPL"


def test_fetch_headlines_retries_transient_error_then_succeeds(monkeypatch):
    """Regression guard: this call used to have no retry at all, unlike
    src/data/providers/alpaca_data.py's equivalent bar fetch -- a single
    dropped connection would fail the whole symbol's news fetch instead of
    recovering."""
    monkeypatch.setattr("src.common.errors.time.sleep", lambda _: None)
    provider = AlpacaNews(creds=_creds())
    client = _FakeClient([_headline("AAPL beats earnings")], exc_sequence=[ConnectionError("blip")])
    _install_fake_client(provider, client)

    out = provider.fetch_headlines("AAPL")

    assert client.calls == 2  # first attempt failed transiently, retry succeeded
    assert len(out) == 1
