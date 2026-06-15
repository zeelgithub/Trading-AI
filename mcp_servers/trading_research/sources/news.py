"""Recent news headlines/summaries for a ticker via Alpaca's News API.

Reuses the bot's read-only market-data credentials
(src.common.secrets.load_market_data_credentials) -- this server never holds
trading credentials.
"""

from __future__ import annotations

from typing import Any

import requests

from src.common.secrets import load_market_data_credentials


def fetch_recent_news(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
    creds = load_market_data_credentials()
    resp = requests.get(
        f"{creds.base_url}/v1beta1/news",
        params={"symbols": ticker.upper(), "limit": limit, "sort": "desc"},
        headers={
            "APCA-API-KEY-ID": creds.key_id,
            "APCA-API-SECRET-KEY": creds.secret_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    articles = resp.json().get("news", [])
    return [
        {
            "headline": a.get("headline"),
            "summary": a.get("summary"),
            "created_at": a.get("created_at"),
            "source": a.get("source"),
        }
        for a in articles
    ]
