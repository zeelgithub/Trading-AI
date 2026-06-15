"""Congressional trading disclosures, sourced from the free House/Senate Stock
Watcher data dumps (CapitolTrades itself 429s non-browser requests).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import requests

from .. import cache

HOUSE_URL = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"

_DATASET_TTL = 12 * 3600


def _fetch_dataset(url: str, chamber: str) -> list[dict[str, Any]]:
    cache_key = f"capitol_trades::{chamber}"
    cached = cache.get(cache_key, _DATASET_TTL)
    if cached is not None:
        return cached
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cache.set(cache_key, data)
    return data


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def fetch_recent_trades(ticker: str, days: int, limit: int = 30) -> list[dict[str, Any]]:
    """Raw disclosure records for `ticker` in the last `days` days, most recent
    first, capped at `limit` -- this is what gets handed to Haiku to compress.
    """
    ticker = ticker.upper()
    cutoff = dt.date.today() - dt.timedelta(days=days)
    records: list[dict[str, Any]] = []

    for url, chamber, name_field in (
        (HOUSE_URL, "house", "representative"),
        (SENATE_URL, "senate", "senator"),
    ):
        try:
            dataset = _fetch_dataset(url, chamber)
        except (requests.RequestException, ValueError):
            continue
        for row in dataset:
            if (row.get("ticker") or "").upper() != ticker:
                continue
            tx_date = _parse_date(row.get("transaction_date"))
            if tx_date is None or tx_date < cutoff:
                continue
            records.append(
                {
                    "politician": row.get(name_field) or "unknown",
                    "chamber": chamber,
                    "action": row.get("type"),
                    "amount_range": row.get("amount"),
                    "transaction_date": row.get("transaction_date"),
                    "disclosure_date": row.get("disclosure_date"),
                }
            )

    records.sort(key=lambda r: r["transaction_date"] or "", reverse=True)
    return records[:limit]
