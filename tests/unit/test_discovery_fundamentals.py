"""Fundamentals source: quality scoring, penalties, no-data skip."""

from __future__ import annotations

from src.data.providers.fundamentals import Fundamentals
from src.discovery.sources.fundamentals import FundamentalsSource


class FakeFund:
    def __init__(self, by_symbol):
        self._by_symbol = by_symbol

    def fetch(self, symbol):
        return self._by_symbol.get(symbol, Fundamentals(symbol=symbol))


def _source(by_symbol):
    return FundamentalsSource(provider=FakeFund(by_symbol), universe=list(by_symbol))


def test_quality_company_scores_high():
    f = Fundamentals("NVDA", profitable=True, revenue_growth=0.30, market_cap=3e12)
    out = _source({"NVDA": f}).gather()
    assert len(out) == 1
    c = out[0]
    assert c.score > 0.8
    assert "profitable" in c.reason and "rev +30% YoY" in c.reason


def test_weak_company_scores_low():
    good = _source({"AAA": Fundamentals("AAA", profitable=True, revenue_growth=0.25, market_cap=5e11)}).gather()[0]
    weak = _source({"BBB": Fundamentals("BBB", profitable=False, revenue_growth=-0.10, market_cap=1e8)}).gather()[0]
    assert weak.score < good.score
    assert "unprofitable" in weak.reason


def test_no_data_is_skipped():
    assert _source({"GHOST": Fundamentals("GHOST")}).gather() == []
