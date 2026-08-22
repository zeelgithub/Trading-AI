"""
News sentiment scorer -- strategy layer.

The first real (non-None) scorer for `SentimentGate` (src/strategy/
sentiment_gate.py) -- until now every deployment ran with `scorer=None`,
meaning the gate was permanently neutral: same fixed confidence haircut on
every trade, and its block condition (`s < long_requires_min`) never once
fired. This wires a real one: recent headline tone over Alpaca's free news
feed (src.data.providers.news.AlpacaNews), scored with the same deterministic
lexicon discovery's NewsSource uses (src/data/news_sentiment.py) -- no LLM,
no new credentials, same market-data key already loaded for bars.

Net tone across the lookback window: more positive headlines than negative ->
+1 (bullish), more negative -> -1 (bearish), tied or no coverage -> 0
(neutral, same as before this existed). A genuinely bearish score can now
BLOCK a long entry (and a bullish one a short) -- see SentimentGate.apply()
and CLAUDE.md rule 2 ("shrink or block, never originate"); this was already
the gate's designed behavior, just never reachable with scorer=None.

`SentimentGate.apply()` already isolates a raised exception (network error,
feed outage) as `on_feed_unavailable: skip_gate` -- this class deliberately
does not swallow its own errors, that handling is the gate's job, not the
scorer's.

Boundary: read-only; places orders NO, holds trading credentials NO (reuses
the market-data key AlpacaNews already loads).
"""

from __future__ import annotations

from src.data.news_sentiment import headline_tone
from src.data.providers.news import NewsProvider


class NewsSentimentScorer:
    """Callable[[str], int] -- the shape SentimentGate expects."""

    def __init__(self, provider: NewsProvider, days: int = 3, limit: int = 50) -> None:
        self.provider = provider
        self.days = days
        self.limit = limit

    def __call__(self, symbol: str) -> int:
        headlines = self.provider.fetch_headlines(symbol, days=self.days, limit=self.limit)
        pos = neg = 0
        for h in headlines:
            tone = headline_tone(f"{h.headline} {h.summary}")
            if tone > 0:
                pos += 1
            elif tone < 0:
                neg += 1
        if pos == neg:
            return 0
        return 1 if pos > neg else -1
