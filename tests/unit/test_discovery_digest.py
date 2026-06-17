"""Digest formatting: header variants + per-idea text."""

from __future__ import annotations

from datetime import date

from src.discovery.candidate import Candidate, SignalContribution
from src.notify.digest import idea_text, ideas_header

AS_OF = date(2026, 6, 16)


def test_header_with_ideas():
    h = ideas_header(shown=3, screened=11, as_of=AS_OF)
    assert "2026-06-16" in h and "top 3 of 11" in h


def test_header_empty():
    h = ideas_header(shown=0, screened=7, as_of=AS_OF)
    assert "Nothing to do" in h and "7" in h


def test_idea_text_has_stars_reasons_and_suggestion():
    c = Candidate(symbol="nvda")
    c.contributions = [
        SignalContribution("NVDA", "congress", 0.8, "Congress: Khanna bought $1k-15k, filed 5d ago"),
        SignalContribution("NVDA", "technical", 0.7, "Technical: trend_following setup (long), RSI 58"),
    ]
    c.score = 80
    c.entry_price = 200.0
    c.stop_loss = 180.0
    c.suggested_qty = 12
    c.strategy = "trend_following"

    text = idea_text(c)
    assert "NVDA" in text
    assert "★★★★★" in text
    assert "Congress: Khanna" in text and "Technical: trend_following" in text
    assert "BUY 12" in text and "200.00" in text and "180.00" in text and "-10%" in text
