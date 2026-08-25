"""Unit tests for src/discovery/sources/social.py -- SocialBuzzSource's
$TICKER cashtag extraction, universe filtering, and upvote-weighted
percentile scoring."""

from __future__ import annotations

from dataclasses import dataclass

from src.discovery.sources.social import SocialBuzzSource


@dataclass
class _Post:
    subreddit: str
    title: str
    selftext: str = ""
    score: int = 0
    num_comments: int = 0
    created_utc: float = 0.0
    permalink: str = ""


class _FakeProvider:
    def __init__(self, by_subreddit: dict[str, list[_Post]]):
        self._by_subreddit = by_subreddit

    def fetch_posts(self, subreddit, limit=100):
        return self._by_subreddit.get(subreddit, [])


def test_only_known_universe_symbols_are_scored():
    provider = _FakeProvider({"wallstreetbets": [
        _Post("wallstreetbets", "YOLO into $AAPL and $ZZZZZ", score=10),
    ]})
    src = SocialBuzzSource(provider=provider, universe=["AAPL", "MSFT"],
                           subreddits=("wallstreetbets",))
    out = {c.symbol: c for c in src.gather()}
    assert "AAPL" in out
    assert "ZZZZZ" not in out  # not in the passed-in universe -- dropped, not guessed at


def test_bare_all_caps_words_are_not_treated_as_tickers():
    """DD/YOLO/CEO are common WSB acronyms, not cashtags -- only an explicit
    $TICKER counts."""
    provider = _FakeProvider({"wallstreetbets": [
        _Post("wallstreetbets", "DD on why the CEO is a YOLO king", score=5),
    ]})
    src = SocialBuzzSource(provider=provider, universe=["DD", "CEO", "YOLO"],
                           subreddits=("wallstreetbets",))
    assert src.gather() == []


def test_more_mentions_and_upvotes_score_higher():
    provider = _FakeProvider({"wallstreetbets": [
        _Post("wallstreetbets", "$AAPL to the moon", score=500),
        _Post("wallstreetbets", "$AAPL again", score=500),
        _Post("wallstreetbets", "small mention of $MSFT", score=1),
    ]})
    src = SocialBuzzSource(provider=provider, universe=["AAPL", "MSFT"],
                           subreddits=("wallstreetbets",))
    out = {c.symbol: c for c in src.gather()}
    assert out["AAPL"].score > out["MSFT"].score
    assert out["AAPL"].meta["mentions"] == 2
    assert out["AAPL"].score == 1.0  # top of the percentile ranking


def test_mentions_across_multiple_subreddits_combine():
    provider = _FakeProvider({
        "wallstreetbets": [_Post("wallstreetbets", "$RIOT calls", score=20)],
        "stocks": [_Post("stocks", "$RIOT thesis", score=5)],
    })
    src = SocialBuzzSource(provider=provider, universe=["RIOT"],
                           subreddits=("wallstreetbets", "stocks"))
    out = {c.symbol: c for c in src.gather()}
    assert out["RIOT"].meta["mentions"] == 2


def test_one_subreddit_failing_does_not_sink_the_others(monkeypatch):
    class _PartiallyBrokenProvider:
        def fetch_posts(self, subreddit, limit=100):
            if subreddit == "broken":
                raise RuntimeError("rate limited")
            return [_Post(subreddit, "$AAPL news", score=10)]

    src = SocialBuzzSource(provider=_PartiallyBrokenProvider(), universe=["AAPL"],
                           subreddits=("broken", "stocks"))
    out = {c.symbol: c for c in src.gather()}
    assert "AAPL" in out


def test_no_mentions_returns_empty():
    provider = _FakeProvider({"wallstreetbets": [_Post("wallstreetbets", "market thoughts")]})
    src = SocialBuzzSource(provider=provider, universe=["AAPL"], subreddits=("wallstreetbets",))
    assert src.gather() == []


def test_reason_includes_mention_count_and_example():
    provider = _FakeProvider({"wallstreetbets": [
        _Post("wallstreetbets", "$GME squeeze incoming", score=100),
    ]})
    src = SocialBuzzSource(provider=provider, universe=["GME"], subreddits=("wallstreetbets",))
    c = src.gather()[0]
    assert "1 mention" in c.reason
    assert "GME squeeze incoming" in c.reason
