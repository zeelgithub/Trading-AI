"""
News source -- discovery layer.

Deterministic, keyword-based headline sentiment over the discovery universe.
This is intentionally shallow (a free, no-LLM lexicon count, not NLP): it is a
confluence signal -- "the tape is talking positively about this name" -- not a
standalone thesis. It only contributes when recent coverage is net positive
(discovery suggests longs only), so negative news simply withholds a boost.
Tone scoring itself (the lexicon) lives in src/data/news_sentiment.py, shared
with the live SentimentGate scorer (src/strategy/news_sentiment_scorer.py) so
the two never drift into scoring the same headline differently.

Honest limitation: a lexicon can be fooled by sarcasm / context. Good enough to
nudge ranking, never to carry a trade on its own.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.news_sentiment import headline_tone
from src.data.providers.news import NewsProvider
from src.discovery.candidate import SignalContribution
from src.discovery.sources._util import clip_text, fetch_concurrent, safe_call


@dataclass
class NewsSource:
    provider: NewsProvider
    universe: list[str]
    days: int = 7
    limit: int = 50
    name: str = "news"
    max_workers: int = 16

    def gather(self) -> list[SignalContribution]:
        """Threaded across the universe -- one HTTP round-trip per symbol
        otherwise, which at this project's current ~4,000-symbol discovery
        universe adds up even on Alpaca's comparatively fast News API (see
        FundamentalsSource.gather()'s docstring for the yfinance case that
        motivated this same fix). Per-symbol failures still fail soft."""
        symbols = list(dict.fromkeys(s.upper() for s in self.universe))
        return fetch_concurrent(symbols, self._fetch_one, max_workers=self.max_workers)

    def _fetch_one(self, symbol: str) -> SignalContribution | None:
        headlines = safe_call(self.provider.fetch_headlines, symbol, days=self.days, limit=self.limit)
        if headlines is None:
            return None
        return _score_symbol(symbol, headlines, self.name, self.days)


def _score_symbol(symbol, headlines, source_name, days) -> SignalContribution | None:
    pos = neg = 0
    example = ""
    for h in headlines:
        tone = headline_tone(f"{h.headline} {h.summary}")
        if tone > 0:
            pos += 1
            example = example or h.headline
        elif tone < 0:
            neg += 1
    n = pos + neg
    if n == 0:
        return None
    net_ratio = (pos - neg) / n          # [-1, 1]
    if net_ratio <= 0:                    # not net positive -> no long boost
        return None
    score = min(1.0, 0.4 + 0.4 * net_ratio + 0.2 * min(pos, 3) / 3)
    reason = f"News: {pos} positive headline{'s' if pos != 1 else ''} in {days}d"
    if example:
        reason += f" (e.g. “{clip_text(example)}”)"
    return SignalContribution(symbol=symbol, source=source_name, score=score, reason=reason,
                              meta={"positive": pos, "negative": neg})
