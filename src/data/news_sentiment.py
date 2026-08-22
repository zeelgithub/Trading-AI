"""
Headline tone lexicon -- data layer.

Deterministic, keyword-based tone scoring (a free, no-LLM lexicon count, not
NLP). Shared by src/discovery/sources/news.py (confluence ranking) and
src/strategy/news_sentiment_scorer.py (the live SentimentGate scorer) so the
two never drift into scoring the same headline differently.

Honest limitation: a lexicon can be fooled by sarcasm / context. Good enough
to nudge a ranking or haircut a confidence, never to carry a trade on its own
-- see docs/SAFEGUARDS.md / CLAUDE.md rule 2 (sentiment shrinks or blocks, never
originates).

Boundary: pure text scoring, no I/O.
"""

from __future__ import annotations

_POSITIVE = {
    "beat", "beats", "surge", "surges", "upgrade", "upgraded", "record", "growth",
    "rally", "rallies", "jump", "jumps", "soar", "soars", "outperform", "raises",
    "raised", "tops", "strong", "bullish", "partnership", "wins", "win", "approval",
    "approved", "expansion", "buyback", "profit", "gains", "gain", "high", "boost",
}
_NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "downgrade", "downgraded", "lawsuit",
    "probe", "cut", "cuts", "falls", "fall", "drop", "drops", "weak", "bearish",
    "recall", "fraud", "investigation", "warns", "warning", "slump", "layoffs",
    "loss", "losses", "decline", "sinks", "halts", "delay", "delays", "bankruptcy",
}


def headline_tone(text: str) -> int:
    """+1 net-positive words win, -1 net-negative, 0 tied/no signal."""
    words = {w.strip(".,!?:;\"'()").lower() for w in text.split()}
    return len(words & _POSITIVE) - len(words & _NEGATIVE)
