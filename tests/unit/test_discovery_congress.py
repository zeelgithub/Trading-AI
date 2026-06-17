"""Congress source: filtering (stock buys, recency, politician), freshness +
multi-buyer scoring, and reason text."""

from __future__ import annotations

from datetime import date

from congress_copy.models import DisclosedTrade
from src.discovery.sources.congress import CongressSource

AS_OF = date(2026, 6, 16)


class FakeProvider:
    def __init__(self, rows):
        self._rows = rows

    def fetch(self):
        return [DisclosedTrade.from_dict(r) for r in self._rows]


def _row(politician, ticker, tx, disc, asset="stock", lo=1001, hi=15000, trade="2026-05-20"):
    return {
        "politician": politician, "ticker": ticker, "asset_type": asset, "tx_type": tx,
        "trade_date": trade, "disclosure_date": disc, "amount_low": lo, "amount_high": hi,
    }


def _source(rows, **kw):
    return CongressSource(provider=FakeProvider(rows), as_of=AS_OF, max_age_days=45, **kw)


def test_recent_stock_buy_becomes_candidate():
    out = _source([_row("Ro Khanna", "NVDA", "buy", "2026-06-11")]).gather()
    assert len(out) == 1
    c = out[0]
    assert c.symbol == "NVDA" and c.source == "congress"
    assert "Ro Khanna" in c.reason and "5d ago" in c.reason
    assert c.meta["disclosure_age_days"] == 5


def test_sells_options_and_stale_are_dropped():
    rows = [
        _row("Ro Khanna", "AAPL", "sell", "2026-06-11"),                 # sell
        _row("Ro Khanna", "TSLA", "buy", "2026-06-11", asset="option"),  # option
        _row("Ro Khanna", "OLD", "buy", "2026-01-20"),                   # stale
    ]
    assert _source(rows).gather() == []


def test_multiple_buyers_boost_score_and_reason():
    rows = [
        _row("Ro Khanna", "NVDA", "buy", "2026-06-11"),
        _row("Nancy Pelosi", "NVDA", "buy", "2026-06-10"),
    ]
    out = _source(rows).gather()
    assert len(out) == 1
    c = out[0]
    assert c.meta["n_buyers"] == 2
    assert "+1 more buyer" in c.reason
    # Two buyers must outscore the same freshest single-buyer disclosure.
    single = _source([_row("Ro Khanna", "NVDA", "buy", "2026-06-11")]).gather()[0]
    assert c.score > single.score


def test_fresher_disclosure_scores_higher():
    fresh = _source([_row("Ro Khanna", "NVDA", "buy", "2026-06-15")]).gather()[0]
    old = _source([_row("Ro Khanna", "NVDA", "buy", "2026-05-10")]).gather()[0]
    assert fresh.score > old.score


def test_politician_allowlist_filters():
    rows = [
        _row("Ro Khanna", "NVDA", "buy", "2026-06-11"),
        _row("Nancy Pelosi", "GOOGL", "buy", "2026-06-11"),
    ]
    out = _source(rows, politicians=("Nancy Pelosi",)).gather()
    assert [c.symbol for c in out] == ["GOOGL"]
