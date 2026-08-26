"""
Proposal store -- core layer.

In propose-and-approve mode the daily cycle decides but places NOTHING; each
risk-approved entry is written here as a pending Proposal and pushed to the phone
with Approve / Deny buttons. The Telegram listener loads a proposal on approval
and runs it back through the risk gate before any order is placed.

A Proposal is a *record of intent*, never an order. It carries the canonical
Intent wire dict (docs/CONTRACTS.md) plus enough context (ratchet params, ATR)
to rebuild the protective stop at execution time.

Boundary: places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.record_store import JsonRecordStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROPOSALS_PATH = PROJECT_ROOT / "state" / "proposals.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Proposal:
    """One risk-approved entry awaiting human approval."""

    id: str
    intent: dict                       # Intent.to_dict() wire format
    approved_qty: float                # qty the risk gate approved at propose time
    strategy: str
    ratchet_params: dict = field(default_factory=dict)
    atr: float | None = None
    status: str = "pending"            # pending | approved | denied | expired
    created_ts: str = ""
    expiry_ts: str = ""

    @classmethod
    def create(
        cls,
        intent: dict,
        approved_qty: float,
        strategy: str,
        ratchet_params: dict | None = None,
        atr: float | None = None,
        expiry_minutes: int = 1080,
    ) -> Proposal:
        now = _utcnow()
        symbol = intent.get("symbol", "SYM")
        return cls(
            id=f"{symbol}-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            intent=intent,
            approved_qty=float(approved_qty),
            strategy=strategy,
            ratchet_params=ratchet_params or {},
            atr=atr,
            status="pending",
            created_ts=now.isoformat(),
            expiry_ts=(now + timedelta(minutes=expiry_minutes)).isoformat(),
        )

    @property
    def symbol(self) -> str:
        return self.intent.get("symbol", "")

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expiry_ts:
            return False
        ref = now or _utcnow()
        try:
            return ref >= datetime.fromisoformat(self.expiry_ts)
        except ValueError:
            return False

    def summary(self) -> str:
        i = self.intent
        entry = i.get("entry_price")
        stop = i.get("stop_loss")
        entry_s = f"${entry:,.2f}" if entry else "mkt"
        stop_s = f"${stop:,.2f}" if stop else "n/a"
        return (
            f"{i.get('signal', 'BUY')} {self.approved_qty:g} {self.symbol} "
            f"@ ~{entry_s}  stop {stop_s}  [{self.strategy}]"
        )


class ProposalStore(JsonRecordStore[Proposal]):
    """JSON-backed pending-proposal queue (state/proposals.json). Shared by the
    run_paper cron (writes) and the Telegram listener (reads / marks)."""

    def __init__(self, path: str | Path = DEFAULT_PROPOSALS_PATH) -> None:
        super().__init__(path, Proposal, log_name="proposals")

    def purge_expired(self) -> int:
        """Mark still-pending but expired proposals as expired. Returns count."""
        payload = self._load_raw()
        n = 0
        for d in payload.values():
            if d.get("status") == "pending" and Proposal(**d).is_expired():
                d["status"] = "expired"
                n += 1
        if n:
            self._save_raw(payload)
        return n
