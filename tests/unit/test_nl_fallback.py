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


def test_buy_with_company_name_and_stop():
    assert _parse("buy 15 Tesla with 8% stop") == {"cmd": "buy", "sym": "TSLA", "qty": 15, "stop": 8.0}


def test_close_single_symbol_is_not_flatten():
    assert _parse("close my Apple position") == {"cmd": "close", "sym": "AAPL"}


def test_status_and_control_words():
    assert _parse("show my positions") == {"cmd": "status"}
    assert _parse("halt the bot") == {"cmd": "halt"}
    assert _parse("run a cycle now") == {"cmd": "run"}
