"""NewsSentimentScorer: the live SentimentGate scorer -- net headline tone -> -1/0/+1."""

from __future__ import annotations

from src.data.providers.news import Headline
from src.strategy.news_sentiment_scorer import NewsSentimentScorer


class FakeNews:
    def __init__(self, by_symbol):
        self._by_symbol = by_symbol

    def fetch_headlines(self, symbol, days=7, limit=50):
        return [Headline(symbol=symbol, headline=h) for h in self._by_symbol.get(symbol, [])]


def test_net_positive_headlines_score_bullish():
    scorer = NewsSentimentScorer(FakeNews({
        "NVDA": ["NVDA beats estimates and raises guidance", "Analysts upgrade NVDA to strong buy"],
    }))
    assert scorer("NVDA") == 1


def test_net_negative_headlines_score_bearish():
    scorer = NewsSentimentScorer(FakeNews({
        "XYZ": ["XYZ plunges on fraud probe", "XYZ downgraded after weak guidance"],
    }))
    assert scorer("XYZ") == -1


def test_tied_or_no_signal_scores_neutral():
    scorer = NewsSentimentScorer(FakeNews({
        "AAA": ["AAA beats estimates", "AAA cut after weak guidance"],  # 1 pos, 1 neg -> tied
        "BBB": ["BBB holds annual shareholder meeting"],                # no keyword signal
    }))
    assert scorer("AAA") == 0
    assert scorer("BBB") == 0


def test_no_headlines_scores_neutral():
    assert NewsSentimentScorer(FakeNews({}))("UNKNOWN") == 0


def test_days_and_limit_are_forwarded_to_the_provider():
    seen = {}

    class RecordingProvider:
        def fetch_headlines(self, symbol, days=7, limit=50):
            seen["days"] = days
            seen["limit"] = limit
            return []

    NewsSentimentScorer(RecordingProvider(), days=3, limit=20)("AAPL")
    assert seen == {"days": 3, "limit": 20}
