"""
News provider -- data layer.

Read-only headline fetcher over Alpaca's news API (free with the same
market-data credentials; no extra feed cost). Returns lightweight `Headline`
rows; it does no sentiment scoring itself -- that lives in the discovery news
source, kept separate so the scoring stays pure and unit-testable.

Boundary: read-only; places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from src.common.errors import retry_transient
from src.common.secrets import MarketDataCredentials, load_market_data_credentials


@dataclass(frozen=True)
class Headline:
    symbol: str
    headline: str
    summary: str = ""
    created_at: datetime | None = None
    symbols: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class NewsProvider(Protocol):
    def fetch_headlines(self, symbol: str, days: int = 7, limit: int = 50) -> list[Headline]:
        ...


class AlpacaNews:
    """Thin read-only wrapper over alpaca-py's NewsClient (market-data creds)."""

    def __init__(self, creds: MarketDataCredentials | None = None) -> None:
        self._creds = creds or load_market_data_credentials()
        self._client = None  # lazily constructed so import never needs alpaca-py

    def _get_client(self):
        if self._client is None:
            try:
                from alpaca.data.historical.news import NewsClient
            except ImportError as exc:  # pragma: no cover - env guard
                raise RuntimeError("alpaca-py is not installed. Run: pip install alpaca-py") from exc
            self._client = NewsClient(api_key=self._creds.key_id, secret_key=self._creds.secret_key)
        return self._client

    def fetch_headlines(self, symbol: str, days: int = 7, limit: int = 50) -> list[Headline]:
        from alpaca.data.requests import NewsRequest

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        req = NewsRequest(symbols=symbol, start=start, end=end, limit=limit,
                          include_content=False)
        # Read-only: retry a transient network blip instead of letting one
        # dropped connection fail the whole symbol's headline fetch -- same
        # pattern as alpaca_data.py's bar fetch.
        resp = retry_transient(lambda: self._get_client().get_news(req))
        items = getattr(resp, "news", None) or getattr(resp, "data", {}).get("news", [])
        out: list[Headline] = []
        for it in items:
            out.append(Headline(
                symbol=symbol.upper(),
                headline=getattr(it, "headline", "") or "",
                summary=getattr(it, "summary", "") or "",
                created_at=getattr(it, "created_at", None),
                symbols=tuple(getattr(it, "symbols", ()) or ()),
            ))
        return out
