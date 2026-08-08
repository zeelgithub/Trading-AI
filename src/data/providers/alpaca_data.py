"""
Alpaca market-data client -- data layer.

Read-only daily bars from the Alpaca data API. Uses market-data credentials
ONLY and cannot construct an order client.

Boundary: places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from src.common.secrets import MarketDataCredentials, load_market_data_credentials

_OHLCV = ["open", "high", "low", "close", "volume"]


class AlpacaData:
    """Thin read-only wrapper over alpaca-py's StockHistoricalDataClient."""

    def __init__(self, creds: MarketDataCredentials | None = None) -> None:
        self._creds = creds or load_market_data_credentials()
        self._client = None  # lazily constructed so import never needs alpaca-py

    def _get_client(self):
        if self._client is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
            except ImportError as exc:  # pragma: no cover - env guard
                raise RuntimeError(
                    "alpaca-py is not installed. Run: pip install alpaca-py"
                ) from exc
            self._client = StockHistoricalDataClient(
                api_key=self._creds.key_id,
                secret_key=self._creds.secret_key,
            )
        return self._client

    def get_daily_bars(
        self,
        symbol: str,
        lookback_days: int = 400,
        end: datetime | None = None,
        start: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch adjusted daily bars as a UTC time-indexed OHLCV frame.

        `start` (when given) wins over `lookback_days` -- used by incremental
        ingest to fetch only what the local cache is missing.
        """
        result = self.get_daily_bars_multi([symbol], lookback_days, end=end, start=start)
        return result.get(symbol, pd.DataFrame(columns=_OHLCV))

    def get_daily_bars_multi(
        self,
        symbols: list[str],
        lookback_days: int = 400,
        end: datetime | None = None,
        start: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        """One batched request for many symbols -> {symbol: OHLCV frame}.

        Alpaca accepts a symbol list natively, so screening a large universe
        costs one HTTP round-trip instead of N.
        """
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        if not symbols:
            return {}
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=lookback_days))

        request = StockBarsRequest(
            symbol_or_symbols=list(symbols),
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment=Adjustment.ALL,  # split + dividend adjusted
            feed=self._creds.feed,
        )
        bars = self._get_client().get_stock_bars(request)
        df = bars.df
        empty = pd.DataFrame(columns=_OHLCV)
        if df.empty:
            return {s: empty.copy() for s in symbols}

        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                sub = df.xs(sym, level="symbol") if isinstance(df.index, pd.MultiIndex) else df
            except KeyError:
                out[sym] = empty.copy()
                continue
            sub = sub[_OHLCV].copy()
            sub.index = pd.to_datetime(sub.index, utc=True)
            sub.index.name = "ts"
            out[sym] = sub
        return out
