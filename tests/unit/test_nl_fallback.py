"""Tests for the local regex NL parser -- the keyless fallback used when no
ANTHROPIC_API_KEY is set. This is a safety-relevant path (it can map to flatten),
so its parsing is pinned here.
"""

from __future__ import annotations

import pytest

from scripts.run_telegram import Listener


def _parse(text: str):
    # _parse_nl uses no instance state; a bare instance exercises it without creds.
    return object.__new__(Listener)._parse_nl(text)


@pytest.mark.parametrize("text", [
    "sell all", "sell everything", "close everything", "close all",
    "exit everything", "dump everything", "liquidate", "flatten",
])
def test_flatten_phrasings(text):
    assert _parse(text) == {"cmd": "flatten"}


def test_close_single_symbol_is_not_flatten():
    assert _parse("close my Apple position") == {"cmd": "close", "sym": "AAPL"}


def test_status_and_control_words():
    assert _parse("show my positions") == {"cmd": "status"}
    assert _parse("halt the bot") == {"cmd": "halt"}
    assert _parse("run a cycle now") == {"cmd": "run"}


def test_buy_with_explicit_qty_and_stop():
    assert _parse("buy 10 TSLA with 8% stop") == {
        "cmd": "buy", "sym": "TSLA", "qty": 10, "stop": 8.0,
    }


def test_buy_without_qty_asks_for_it_instead_of_fabricating_one():
    """Regression guard: qty extraction used to grab the FIRST integer
    anywhere in the message, before the stop-loss phrase was identified --
    "buy tesla with a 15% stop" (no quantity ever given) matched "15" as
    BOTH qty and stop, silently producing a plausible-looking but fabricated
    "buy 15 TSLA" instead of asking for the missing quantity."""
    assert _parse("buy tesla with a 15% stop") == {
        "cmd": "unknown", "reply": 'How many shares? Try: "buy 10 TSLA".',
    }


def test_buy_qty_before_stop_percent_still_parses_correctly():
    """The percent-lookahead fix must not break the normal case where a real
    quantity legitimately appears before the stop percentage."""
    assert _parse("buy 15 TSLA with 8% stop") == {
        "cmd": "buy", "sym": "TSLA", "qty": 15, "stop": 8.0,
    }
