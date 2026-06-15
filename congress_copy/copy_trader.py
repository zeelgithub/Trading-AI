"""
Congress copy-trading -- core logic (SHADOW).

Takes normalized DisclosedTrades and, for the chosen politician's *stock* trades,
mirrors them as intents:
  - a disclosed BUY  -> open/add a long, with a protective stop attached so we
    manage the exit our way (the ratchet), rather than waiting for their
    (also-delayed) sell disclosure.
  - a disclosed SELL -> close the mirrored position if we hold it.

Every mirrored buy is routed through the SAME risk gatekeeper the rest of the
bot uses. This module decides and logs; it does NOT place orders (shadow). When
execution is enabled, approved intents are handed to the order manager.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable

from congress_copy.models import DisclosedTrade
from src.common.logging import AuditLog
from src.common.models import Action, Decision, Intent, Side
from src.risk.risk_manager import AccountState, RiskManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CopyConfig:
    politician: str = "Ro Khanna"
    equities_only: bool = True
    max_disclosure_age_days: int = 60
    initial_stop_pct: float = 10.0
    strategy_name: str = "congress_copy"
    execute: bool = False  # shadow by default


@dataclass
class CopyReport:
    actions: list = field(default_factory=list)
    considered: int = 0
    skipped_not_politician: int = 0
    skipped_options: int = 0
    skipped_stale: int = 0
    skipped_seen: int = 0
    skipped_no_price: int = 0

    def summary(self) -> str:
        return (
            f"considered={self.considered} actions={len(self.actions)} "
            f"skipped[options={self.skipped_options} stale={self.skipped_stale} "
            f"seen={self.skipped_seen} other_pol={self.skipped_not_politician} "
            f"no_price={self.skipped_no_price}]"
        )


class SeenStore:
    """Persists the set of already-mirrored disclosure ids."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        with self.path.open("r", encoding="utf-8") as fh:
            return set(json.load(fh))

    def save(self, seen: set[str]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh, indent=2)


class CopyTrader:
    def __init__(
        self,
        config: CopyConfig,
        risk: RiskManager,
        price_fn: Callable[[str], float | None],
        seen_store: SeenStore,
        audit: AuditLog | None = None,
    ) -> None:
        self.config = config
        self.risk = risk
        self.price_fn = price_fn
        self.seen_store = seen_store
        self.audit = audit or AuditLog()

    def run(self, trades: list[DisclosedTrade], account: AccountState, as_of: date | None = None) -> CopyReport:
        as_of = as_of or date.today()
        seen = self.seen_store.load()
        report = CopyReport()

        for trade in sorted(trades, key=lambda t: t.disclosure_date):
            report.considered += 1
            if self.config.politician and trade.politician != self.config.politician:
                report.skipped_not_politician += 1
                continue
            if self.config.equities_only and not trade.is_stock:
                report.skipped_options += 1
                continue
            if (as_of - trade.disclosure_date).days > self.config.max_disclosure_age_days:
                report.skipped_stale += 1
                continue
            if trade.id in seen:
                report.skipped_seen += 1
                continue

            price = self.price_fn(trade.ticker)
            if price is None or price <= 0:
                report.skipped_no_price += 1
                continue

            action = self._mirror(trade, price, account)
            report.actions.append(action)
            self.audit.record("congress_copy", **action)
            seen.add(trade.id)

        self.seen_store.save(seen)
        return report

    def _mirror(self, trade: DisclosedTrade, price: float, account: AccountState) -> dict:
        base = {
            "politician": trade.politician,
            "ticker": trade.ticker,
            "disclosed": trade.tx_type,
            "lag_days": trade.disclosure_lag_days,
        }
        if trade.is_buy:
            intent = Intent(
                symbol=trade.ticker,
                strategy=self.config.strategy_name,
                side=Side.LONG,
                action=Action.BUY,
                confidence=0.6,
                entry_price=round(price, 2),
                stop_loss=round(price * (1 - self.config.initial_stop_pct / 100.0), 2),
            )
            decision = self.risk.evaluate(intent, replace(account, last_price=price))
            return {
                **base,
                "action": "buy",
                "decision": decision.decision.value,
                "qty": decision.approved_qty,
                "reason": decision.reason,
            }
        # Disclosed sell -> mirror an exit of the position if we hold it.
        return {**base, "action": "sell", "decision": "close_if_held", "qty": 0.0, "reason": "mirror exit"}
