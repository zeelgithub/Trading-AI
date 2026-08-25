"""
Reddit provider -- data layer.

Read-only, "app-only" OAuth (client_credentials grant) access to public
subreddit listings. Deliberately NOT the `password` grant some Reddit "script"
app examples use -- that would require the user's actual Reddit account
password wired into this bot for zero extra benefit, since all we need is
public, unauthenticated-in-spirit read access to public posts. client_credentials
gives exactly that: read-only, no account password, no write scope, via just
the app's client ID + secret (`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`,
created at reddit.com/prefs/apps as a "script" app).

Free: Reddit's standard API rate limit for app-only auth is generous (~100
requests / 10 minutes), far more than a once- or few-times-daily discovery
cycle needs.

Boundary: read-only; places orders NO, holds trading credentials NO.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.common.secrets import RedditCredentials, load_reddit_credentials

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"


@dataclass(frozen=True)
class RedditPost:
    subreddit: str
    title: str
    selftext: str
    score: int
    num_comments: int
    created_utc: float
    permalink: str


@runtime_checkable
class RedditProvider(Protocol):
    def fetch_posts(self, subreddit: str, limit: int = 50) -> list[RedditPost]:
        ...


class RedditAppOnly:
    """Lazily authenticates (and re-authenticates on expiry) via app-only
    OAuth. The HTTP client (`requests`) is imported lazily so this module can
    be imported without it installed unless actually used."""

    def __init__(self, creds: RedditCredentials | None = None) -> None:
        self._creds = creds or load_reddit_credentials()
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        import requests

        resp = requests.post(
            _TOKEN_URL,
            auth=(self._creds.client_id, self._creds.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self._creds.user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def fetch_posts(self, subreddit: str, limit: int = 50) -> list[RedditPost]:
        import requests

        if not self._creds.configured:
            raise RuntimeError(
                "Reddit credentials not configured (REDDIT_CLIENT_ID / "
                "REDDIT_CLIENT_SECRET missing from .env)."
            )
        token = self._get_token()
        resp = requests.get(
            f"{_API_BASE}/r/{subreddit}/hot",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {token}", "User-Agent": self._creds.user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        out: list[RedditPost] = []
        for child in payload.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("stickied"):  # pinned mod/rules posts, not organic discussion
                continue
            out.append(RedditPost(
                subreddit=subreddit,
                title=d.get("title", "") or "",
                selftext=d.get("selftext", "") or "",
                score=int(d.get("score", 0) or 0),
                num_comments=int(d.get("num_comments", 0) or 0),
                created_utc=float(d.get("created_utc", 0) or 0),
                permalink=d.get("permalink", "") or "",
            ))
        return out
