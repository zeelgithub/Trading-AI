"""Tests for the congress copy-trading shadow logic."""

from __future__ import annotations

from datetime import date

from congress_copy.copy_trader import CopyConfig, CopyTrader, SeenStore
from congress_copy.models import DisclosedTrade
from src.common.logging import AuditLog
from src.risk.risk_manager import AccountState, RiskManager

AS_OF = date(2026, 6, 13)

ROWS = [
    {"politician": "Ro Khanna", "ticker": "NVDA", "asset_type": "stock", "tx_type": "buy",
     "trade_date": "2026-05-20", "disclosure_date": "2026-06-05"},
    {"politician": "Ro Khanna", "ticker": "AAPL", "asset_type": "stock", "tx_type": "sell",
     "trade_date": "2026-05-22", "disclosure_date": "2026-06-06"},
    {"politician": "Ro Khanna", "ticker": "TSLA", "asset_type": "option", "tx_type": "buy",
     "trade_date": "2026-05-25", "disclosure_date": "2026-06-07"},
    {"politician": "Ro Khanna", "ticker": "MSFT", "asset_type": "stock", "tx_type": "buy",
     "trade_date": "2026-01-02", "disclosure_date": "2026-01-20"},   # stale
    {"politician": "Nancy Pelosi", "ticker": "GOOGL", "asset_type": "stock", "tx_type": "buy",
     "trade_date": "2026-05-28", "disclosure_date": "2026-06-08"},   # other politician
]


def _trades():
    return [DisclosedTrade.from_dict(r) for r in ROWS]


def _account():
    return AccountState(equity=50000, start_of_day_equity=50000, buying_power=200000,
                        last_price=0.0, open_positions=0, gross_exposure_value=0.0)


def _trader(tmp_path):
    return CopyTrader(
        config=CopyConfig(politician="Ro Khanna"),
        risk=RiskManager(),
        price_fn=lambda t: 100.0,
        seen_store=SeenStore(tmp_path / "seen.json"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )


def test_disclosed_trade_properties():
    t = DisclosedTrade.from_dict(ROWS[0])
    assert t.is_stock and t.is_buy
    assert t.disclosure_lag_days == 16
    assert t.id == DisclosedTrade.from_dict(ROWS[0]).id  # stable


def test_filters_and_mirrors(tmp_path):
    report = _trader(tmp_path).run(_trades(), _account(), as_of=AS_OF)
    assert report.considered == 5
    assert report.skipped_options == 1
    assert report.skipped_stale == 1
    assert report.skipped_not_politician == 1
    assert len(report.actions) == 2

    buy = next(a for a in report.actions if a["ticker"] == "NVDA")
    assert buy["action"] == "buy"
    assert buy["decision"] in ("approve", "resize")
    assert buy["qty"] == 50.0   # 10% max-position cap at $100

    sell = next(a for a in report.actions if a["ticker"] == "AAPL")
    assert sell["action"] == "sell"
    assert sell["decision"] == "close_if_held"


def test_dedupe_across_runs(tmp_path):
    trader = _trader(tmp_path)
    first = trader.run(_trades(), _account(), as_of=AS_OF)
    assert len(first.actions) == 2
    second = trader.run(_trades(), _account(), as_of=AS_OF)
    assert len(second.actions) == 0
    assert second.skipped_seen == 2
