"""
News source -- discovery layer.

Deterministic, keyword-based headline sentiment over the discovery universe.
This is intentionally shallow (a free, no-LLM lexicon count, not NLP): it is a
confluence signal -- "the tape is talking positively about this name" -- not a
standalone thesis. It only contributes when recent coverage is net positive
(discovery suggests longs only), so negative news simply withholds a boost.

Honest limitation: a lexicon can be fooled by sarcasm / context. Good enough to
nudge ranking, never to carry a trade on its own.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.providers.news import NewsProvider
from src.discovery.candidate import SignalContribution

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


@dataclass
class NewsSource:
    provider: NewsProvider
    universe: list[str]
    days: int = 7
    limit: int = 50
    name: str = "news"

    def gather(self) -> list[SignalContribution]:
        out: list[SignalContribution] = []
        for symbol in dict.fromkeys(s.upper() for s in self.universe):
            try:
                headlines = self.provider.fetch_headlines(symbol, days=self.days, limit=self.limit)
            except Exception:
                continue
            contribution = _score_symbol(symbol, headlines, self.name, self.days)
            if contribution is not None:
                out.append(contribution)
        return out


def _score_symbol(symbol, headlines, source_name, days) -> SignalContribution | None:
    pos = neg = 0
    example = ""
    for h in headlines:
        tone = _tone(f"{h.headline} {h.summary}")
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
        reason += f" (e.g. “{_clip(example)}”)"
    return SignalContribution(symbol=symbol, source=source_name, score=score, reason=reason,
                              meta={"positive": pos, "negative": neg})


def _tone(text: str) -> int:
    words = {w.strip(".,!?:;\"'()").lower() for w in text.split()}
    return len(words & _POSITIVE) - len(words & _NEGATIVE)


def _clip(text: str, n: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"
