"""
Social buzz source -- discovery layer.

Counts `$TICKER` cashtag mentions across configured subreddits (see
discovery.social.subreddits), weighted by post score (upvotes), and turns
that into a percentile-ranked confluence signal -- same "relative to today's
screen, not a hardcoded threshold" design as
src/discovery/sources/volatility.py. This is explicitly a HYPE/ATTENTION
signal, not a quality or fundamentals signal: a stock mentioned often and
upvoted heavily says "people are talking about this," nothing more --
matches this project's rule that the sentiment/social layer can nudge
ranking, never originate or carry a trade on its own (same posture as
src/discovery/sources/news.py).

Only scores symbols already in the passed-in `universe` (the same
discovery_universe() every other source screens) -- deliberately NOT a path
to add brand-new, never-vetted tickers straight from Reddit text. Every
other source in this layer (news, fundamentals, volatility) works the same
way; only src/discovery/sources/congress.py discovers genuinely new tickers,
and it does so from structured SEC-disclosure data, not free-text scraping.

Ticker extraction requires an explicit `$TICKER` cashtag (the WSB
convention) -- deliberately NOT bare-word all-caps matching, which would
false-positive on "DD," "YOLO," "CEO," and dozens of other common all-caps
acronyms used in these communities.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from src.data.providers.reddit import RedditProvider
from src.discovery.candidate import SignalContribution
from src.discovery.sources._util import clip_text, percentile_rank, safe_call

_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")


@dataclass
class SocialBuzzSource:
    provider: RedditProvider
    universe: list[str]
    subreddits: tuple[str, ...] = ("wallstreetbets", "stocks")
    limit_per_subreddit: int = 100
    name: str = "social"

    def gather(self) -> list[SignalContribution]:
        known = set(s.upper() for s in self.universe)
        weight: dict[str, float] = defaultdict(float)
        mentions: dict[str, int] = defaultdict(int)
        example: dict[str, str] = {}

        for sub in self.subreddits:
            posts = safe_call(self.provider.fetch_posts, sub, limit=self.limit_per_subreddit)
            if posts is None:
                continue  # one bad/rate-limited subreddit shouldn't sink the cycle
            for post in posts:
                tickers = set(_CASHTAG_RE.findall(f"{post.title} {post.selftext}")) & known
                for t in tickers:
                    weight[t] += 1.0 + (max(post.score, 0) / 100.0)  # upvotes amplify, don't dominate
                    mentions[t] += 1
                    example.setdefault(t, post.title)

        if not weight:
            return []

        ranked = percentile_rank(weight)
        out: list[SignalContribution] = []
        for symbol, raw in weight.items():
            pct_rank = ranked[symbol]
            n = mentions[symbol]
            out.append(SignalContribution(
                symbol=symbol, source=self.name, score=pct_rank,
                reason=f"Social: {n} mention{'s' if n != 1 else ''} across "
                       f"{len(self.subreddits)} subreddit(s) (e.g. \"{clip_text(example[symbol])}\")",
                meta={"mentions": n, "raw_weight": raw, "percentile": pct_rank},
            ))
        return out
