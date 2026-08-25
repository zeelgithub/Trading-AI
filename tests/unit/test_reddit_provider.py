"""Unit tests for src/data/providers/reddit.py -- RedditAppOnly's app-only
OAuth flow and post parsing. No real network calls: requests.post/get are
monkeypatched."""

from __future__ import annotations

import pytest

from src.common.secrets import RedditCredentials
from src.data.providers.reddit import RedditAppOnly


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _creds():
    return RedditCredentials(client_id="cid", client_secret="csecret")


def _listing(posts):
    return {"data": {"children": [{"data": p} for p in posts]}}


def test_fetch_posts_requests_token_then_listing(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("post", url, kwargs))
        return _FakeResponse({"access_token": "tok123", "expires_in": 3600})

    def fake_get(url, **kwargs):
        calls.append(("get", url, kwargs))
        return _FakeResponse(_listing([
            {"title": "Buying $AAPL calls", "selftext": "", "score": 50,
             "num_comments": 3, "created_utc": 1.0, "permalink": "/r/x/1"},
        ]))

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)

    provider = RedditAppOnly(creds=_creds())
    posts = provider.fetch_posts("wallstreetbets", limit=10)

    assert len(posts) == 1
    assert posts[0].title == "Buying $AAPL calls"
    assert calls[0][0] == "post"  # token fetched before the listing
    assert calls[0][1] == "https://www.reddit.com/api/v1/access_token"
    assert calls[1][1] == "https://oauth.reddit.com/r/wallstreetbets/hot"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer tok123"


def test_token_is_cached_across_calls(monkeypatch):
    token_calls = []

    def fake_post(url, **kwargs):
        token_calls.append(1)
        return _FakeResponse({"access_token": "tok123", "expires_in": 3600})

    def fake_get(url, **kwargs):
        return _FakeResponse(_listing([]))

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)

    provider = RedditAppOnly(creds=_creds())
    provider.fetch_posts("stocks")
    provider.fetch_posts("stocks")

    assert len(token_calls) == 1  # second call reused the cached token


def test_stickied_posts_are_filtered_out(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResponse(
        {"access_token": "tok", "expires_in": 3600}))
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(_listing([
        {"title": "Daily Discussion Thread", "stickied": True, "score": 1,
         "num_comments": 0, "created_utc": 1.0, "permalink": "/r/x/pinned"},
        {"title": "Real post about $TSLA", "stickied": False, "score": 10,
         "num_comments": 2, "created_utc": 1.0, "permalink": "/r/x/2"},
    ])))

    provider = RedditAppOnly(creds=_creds())
    posts = provider.fetch_posts("wallstreetbets")

    assert [p.title for p in posts] == ["Real post about $TSLA"]


def test_fetch_posts_raises_when_not_configured():
    provider = RedditAppOnly(creds=RedditCredentials(client_id="", client_secret=""))
    with pytest.raises(RuntimeError, match="not configured"):
        provider.fetch_posts("wallstreetbets")
