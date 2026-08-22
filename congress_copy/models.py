"""
Congress copy-trading -- disclosure data model.

A DisclosedTrade is one normalized row from a congressional periodic transaction
report (PTR), regardless of source (CapitolTrades via browser, official feed,
etc). Note: disclosures are reported up to ~30-45 days AFTER the actual trade,
so a `trade_date` far behind `disclosure_date` is expected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class DisclosedTrade:
    politician: str
    ticker: str
    asset_type: str          # "stock" | "option" | "other"
    tx_type: str             # "buy" | "sell"
    trade_date: date
    disclosure_date: date
    amount_low: float | None = None
    amount_high: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Stable id for dedupe across runs."""
        key = f"{self.politician}|{self.ticker}|{self.asset_type}|{self.tx_type}|{self.trade_date}|{self.amount_low}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def is_stock(self) -> bool:
        return self.asset_type.lower() == "stock"

    @property
    def is_buy(self) -> bool:
        return self.tx_type.lower() in ("buy", "purchase", "p")

    @property
    def disclosure_lag_days(self) -> int:
        return (self.disclosure_date - self.trade_date).days

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DisclosedTrade:
        return cls(
            politician=d["politician"],
            ticker=d["ticker"].upper(),
            asset_type=d.get("asset_type", "stock"),
            tx_type=_norm_tx(d["tx_type"]),
            trade_date=_as_date(d["trade_date"]),
            disclosure_date=_as_date(d.get("disclosure_date", d["trade_date"])),
            amount_low=_optf(d, "amount_low"),
            amount_high=_optf(d, "amount_high"),
            raw=d,
        )


def _norm_tx(v: str) -> str:
    v = str(v).lower()
    if v in ("buy", "purchase", "p"):
        return "buy"
    if v in ("sell", "sale", "s", "sale_full", "sale_partial"):
        return "sell"
    return v


def _as_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _optf(d: dict, key: str) -> float | None:
    return float(d[key]) if d.get(key) is not None else None
