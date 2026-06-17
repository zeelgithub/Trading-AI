"""Tests for the natural-language command parser (nl_router agent + fallback)."""

from __future__ import annotations

from src.agents.nl import NLCommandParser, _normalize
from tests.unit.agent_fakes import ScriptedModel, text_response


def parser_with(model_json: str, fallback=None) -> NLCommandParser:
    """An NLCommandParser whose agent returns `model_json`, forced available."""
    return NLCommandParser(
        fallback=fallback,
        model_factory=lambda model_id: ScriptedModel([text_response(model_json)]),
        available=True,
    )


# --- smart path ---

def test_parses_buy_with_company_name_and_stop():
    p = parser_with('{"cmd":"buy","sym":"nvda","qty":10,"stop":5}')
    assert p.parse("grab me 10 nvidia, tight 5% stop") == {
        "cmd": "buy", "sym": "NVDA", "qty": 10, "stop": 5.0,
    }


def test_parses_status():
    assert parser_with('{"cmd":"status"}').parse("how's my book?") == {"cmd": "status"}


def test_parses_close():
    assert parser_with('{"cmd":"close","sym":"AAPL"}').parse("dump my apple") == {
        "cmd": "close", "sym": "AAPL",
    }


# --- normalization ---

def test_normalize_positions_to_status():
    assert _normalize({"cmd": "positions"}) == {"cmd": "status"}


def test_normalize_buy_defaults_stop_and_coerces_qty():
    out = _normalize({"cmd": "buy", "sym": "tsla", "qty": "15"})
    assert out == {"cmd": "buy", "sym": "TSLA", "qty": 15, "stop": 10.0}


def test_normalize_unknown_cmd_becomes_unknown():
    out = _normalize({"cmd": "teleport", "reply": "nope"})
    assert out["cmd"] == "unknown" and out["reply"] == "nope"


# --- fallback paths ---

def test_falls_back_when_unavailable():
    calls = {}

    def fallback(text):
        calls["text"] = text
        return {"cmd": "status"}

    p = NLCommandParser(fallback=fallback, available=False)
    assert p.parse("show me everything") == {"cmd": "status"}
    assert calls["text"] == "show me everything"


def test_falls_back_when_agent_returns_no_json():
    def fallback(text):
        return {"cmd": "help"}

    # Model returns prose, not JSON -> agent result not ok -> fallback used.
    p = NLCommandParser(
        fallback=fallback,
        model_factory=lambda model_id: ScriptedModel([text_response("I'm not sure.")]),
        available=True,
    )
    assert p.parse("blah blah") == {"cmd": "help"}


def test_no_fallback_returns_none_when_unavailable():
    assert NLCommandParser(available=False).parse("anything") is None
