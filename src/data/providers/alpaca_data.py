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
    ) -> pd.DataFrame:
        """Fetch adjusted daily bars as a UTC time-indexed OHLCV frame."""
        from alpaca.data.enums import Adjustment
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = end or datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment=Adjustment.ALL,  # split + dividend adjusted
            feed=self._creds.feed,
        )
        bars = self._get_client().get_stock_bars(request)
        df = bars.df
        if df.empty:
            return pd.DataFrame(columns=_OHLCV)

        # alpaca-py returns a MultiIndex (symbol, timestamp); flatten to time only.
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        df = df[_OHLCV].copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "ts"
        return df
