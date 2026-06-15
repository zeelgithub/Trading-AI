"""
Congress copy-trading -- disclosure providers.

The ingestion (a scheduled Chrome job reading CapitolTrades, or an official-feed
job) WRITES normalized rows to a JSON file. This module READS that file. Keeping
ingestion and consumption decoupled means the fragile, source-specific scraping
lives in the schedule prompt, while the copy logic stays clean and testable.

JSON file format: a list of objects, each matching DisclosedTrade.from_dict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from congress_copy.models import DisclosedTrade


class DisclosureProvider(Protocol):
    def fetch(self) -> list[DisclosedTrade]: ...


class JSONFileProvider:
    """Reads disclosures from a JSON file populated by the ingestion job."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> list[DisclosedTrade]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            rows = json.load(fh)
        return [DisclosedTrade.from_dict(r) for r in rows]
