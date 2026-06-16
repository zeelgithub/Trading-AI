"""Tests for the propose-and-approve proposal store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.core.proposals import Proposal, ProposalStore


def _intent(symbol="NVDA"):
    return {
        "symbol": symbol, "signal": "BUY", "confidence": 0.7,
        "strategy": "trend_following", "entry_price": 100.0,
        "stop_loss": 90.0, "take_profit": None,
    }


def test_create_sets_id_status_and_expiry():
    p = Proposal.create(_intent(), approved_qty=10, strategy="trend_following",
                        expiry_minutes=60)
    assert p.symbol == "NVDA" and p.status == "pending"
    assert p.id.startswith("NVDA-")
    assert not p.is_expired()


def test_expiry_detection():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    p = Proposal.create(_intent(), 10, "trend_following")
    p.expiry_ts = past
    assert p.is_expired()


def test_store_roundtrip_and_mark(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    p = Proposal.create(_intent(), 10, "trend_following")
    store.add(p)

    loaded = store.get(p.id)
    assert loaded is not None and loaded.symbol == "NVDA"
    assert loaded.intent["stop_loss"] == 90.0
    assert [x.id for x in store.list_pending()] == [p.id]

    store.mark(p.id, "approved")
    assert store.get(p.id).status == "approved"
    assert store.list_pending() == []


def test_purge_expired_marks_only_pending(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    fresh = Proposal.create(_intent("AAA"), 10, "trend_following")
    stale = Proposal.create(_intent("BBB"), 10, "trend_following")
    stale.expiry_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store.add(fresh)
    store.add(stale)

    assert store.purge_expired() == 1
    assert store.get("BBB" if False else stale.id).status == "expired"
    assert [x.id for x in store.list_pending()] == [fresh.id]
