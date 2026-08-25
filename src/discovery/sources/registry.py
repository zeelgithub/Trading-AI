"""
Source registry -- discovery layer.

Composition root's source table. Before this existed, `builder.py` wired each
source through a hand-written `if enabled.get("x"): sources.append(XSource(...))`
chain, and adding a 7th source meant touching builder.py's chain, this
module's canonical `candidate.SOURCES` tuple, and `scorer.py`'s
`DEFAULT_WEIGHTS` by hand, with nothing enforcing they stayed in sync. Now
adding a source is "write the source + one `@register(name)` factory here" --
`builder.py` just loops over whatever's registered and enabled.

`candidate.SOURCES` stays the hand-maintained canonical name list (weights in
config are keyed by it) rather than being derived from this registry, to
avoid `candidate.py` importing the `sources` package (candidate.py is a pure
data model with no I/O; sources have real provider dependencies). Instead
this module asserts its own keys match `SOURCES` at import time, so any drift
between the two fails loudly and immediately rather than silently at runtime.

Boundary: builds read-only sources; no orders, no credentials of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from src.common.config import Config
from src.discovery.candidate import SOURCES
from src.discovery.sources.base import CandidateSource
from src.research.scoreboard import Scoreboard

FeatureProvider = Callable[[str], pd.DataFrame]


@dataclass
class SourceContext:
    """Everything a source factory might need. `feature_provider` is a
    zero-arg callable (not the provider itself) so it can stay lazy and
    memoized -- technical and volatility share one warmed provider, built at
    most once, only if one of them is actually enabled."""

    config: Config
    discovery: dict                                  # config.get("settings.discovery", {})
    universe: list[str]
    feature_provider: Callable[[], FeatureProvider]
    scoreboard: Scoreboard | None = None


REGISTRY: dict[str, Callable[[SourceContext], CandidateSource]] = {}


def register(name: str) -> Callable:
    def deco(factory: Callable[[SourceContext], CandidateSource]) -> Callable:
        REGISTRY[name] = factory
        return factory
    return deco


@register("congress")
def _build_congress(ctx: SourceContext) -> CandidateSource:
    from congress_copy.providers import JSONFileProvider
    from src.discovery.sources.congress import CongressSource
    from src.discovery.universe import DEFAULT_DISCLOSURES

    cfg = ctx.discovery.get("congress", {}) or {}
    return CongressSource(
        provider=JSONFileProvider(DEFAULT_DISCLOSURES),
        politicians=tuple(cfg.get("politicians", []) or ()),
        max_age_days=int(cfg.get("max_disclosure_age_days", 45)),
    )


@register("technical")
def _build_technical(ctx: SourceContext) -> CandidateSource:
    from src.discovery.sources.technical import TechnicalSource

    return TechnicalSource(
        config=ctx.config,
        feature_provider=ctx.feature_provider(),
        universe=ctx.universe,
        scoreboard=ctx.scoreboard or Scoreboard(),
    )


@register("news")
def _build_news(ctx: SourceContext) -> CandidateSource:
    from src.data.providers.news import AlpacaNews
    from src.discovery.sources.news import NewsSource

    ncfg = ctx.discovery.get("news", {}) or {}
    return NewsSource(
        provider=AlpacaNews(),
        universe=ctx.universe,
        days=int(ncfg.get("lookback_days", 7)),
    )


@register("fundamentals")
def _build_fundamentals(ctx: SourceContext) -> CandidateSource:
    from src.data.providers.fundamentals import YFinanceFundamentals
    from src.discovery.sources.fundamentals import FundamentalsSource

    return FundamentalsSource(provider=YFinanceFundamentals(), universe=ctx.universe)


@register("volatility")
def _build_volatility(ctx: SourceContext) -> CandidateSource:
    from src.discovery.sources.volatility import VolatilitySource

    return VolatilitySource(feature_provider=ctx.feature_provider(), universe=ctx.universe)


@register("social")
def _build_social(ctx: SourceContext) -> CandidateSource:
    from src.data.providers.reddit import RedditAppOnly
    from src.discovery.sources.social import SocialBuzzSource

    scfg = ctx.discovery.get("social", {}) or {}
    return SocialBuzzSource(
        provider=RedditAppOnly(),
        universe=ctx.universe,
        subreddits=tuple(scfg.get("subreddits", ["wallstreetbets", "stocks"]) or ()),
        limit_per_subreddit=int(scfg.get("limit_per_subreddit", 100)),
    )


if set(REGISTRY) != set(SOURCES):
    raise ImportError(
        f"src.discovery.sources.registry's REGISTRY {sorted(REGISTRY)} has drifted "
        f"from src.discovery.candidate.SOURCES {sorted(SOURCES)} -- a source was "
        f"added/renamed in one place but not the other."
    )
