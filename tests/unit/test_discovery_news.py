"""News source: keyword sentiment, net-positive gating, reason text."""

from __future__ import annotations

from src.data.providers.news import Headline
from src.discovery.sources.news import NewsSource


class FakeNews:
    def __init__(self, by_symbol):
        self._by_symbol = by_symbol

    def fetch_headlines(self, symbol, days=7, limit=50):
        return [Headline(symbol=symbol, headline=h) for h in self._by_symbol.get(symbol, [])]


def _source(by_symbol):
    return NewsSource(provider=FakeNews(by_symbol), universe=list(by_symbol), days=7)


def test_net_positive_coverage_contributes():
    out = _source({"NVDA": ["NVDA beats estimates and raises guidance",
                            "Analysts upgrade NVDA to strong buy"]}).gather()
    assert len(out) == 1
    c = out[0]
    assert c.symbol == "NVDA" and c.source == "news"
    assert c.meta["positive"] == 2 and c.meta["negative"] == 0
    assert "positive headline" in c.reason


def test_net_negative_is_withheld():
    out = _source({"XYZ": ["XYZ plunges on fraud probe", "XYZ downgraded after weak guidance"]}).gather()
    assert out == []


def test_no_keyword_signal_is_skipped():
    out = _source({"ABC": ["ABC holds annual shareholder meeting"]}).gather()
    assert out == []


def test_more_positive_headlines_scores_higher():
    strong = _source({"AAA": ["AAA surges record profit", "AAA beats", "AAA upgrade rally"]}).gather()[0]
    weak = _source({"BBB": ["BBB beats slightly", "BBB cut on weak outlook"]}).gather()
    # weak is net-zero (1 pos, 1 neg) -> withheld entirely.
    assert weak == []
    assert strong.score > 0.6
