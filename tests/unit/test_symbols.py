"""Tests for the dynamic symbol resolver: broker asset catalog + cache,
aliases, ticker validation, name/prefix/fuzzy matching, honest ambiguous/none
answers, and the NL fallback's greeting + raw-buy handoff."""

from __future__ import annotations

from scripts.run_telegram import Listener
from src.core.symbols import SymbolResolver
from tests.unit.fakes import FakeBroker


def _resolver(tmp_path, **kw):
    return SymbolResolver(FakeBroker(), cache_path=tmp_path / "assets.json", **kw)


def test_exact_ticker_resolves_with_name(tmp_path):
    res = _resolver(tmp_path).resolve("tsla")
    assert res.ok and res.symbol == "TSLA" and "Tesla" in res.name


def test_company_name_resolves(tmp_path):
    res = _resolver(tmp_path).resolve("rocket lab")
    assert res.ok and res.symbol == "RKLB"


def test_alias_layer_wins(tmp_path):
    res = _resolver(tmp_path, aliases={"tesla": "TSLA"}).resolve("Tesla")
    assert res.ok and res.symbol == "TSLA"


def test_unknown_company_is_honest_none(tmp_path):
    res = _resolver(tmp_path).resolve("spacex")
    assert res.status == "none"
    assert "no tradable US listing" in res.note


def test_ambiguous_names_return_candidates(tmp_path):
    res = _resolver(tmp_path).resolve("bank")
    assert res.status == "ambiguous"
    symbols = {s for s, _ in res.candidates}
    assert {"BAC", "BK"} <= symbols


def test_typo_fuzzy_match(tmp_path):
    res = _resolver(tmp_path).resolve("nvidai")  # transposed
    assert res.status in ("ok", "ambiguous")
    if res.status == "ok":
        assert res.symbol == "NVDA"
    else:
        assert "NVDA" in {s for s, _ in res.candidates}


def test_non_tradable_assets_excluded(tmp_path):
    assert _resolver(tmp_path).resolve("HALT").status == "none"


def test_catalog_cached_one_broker_call(tmp_path):
    calls = {"n": 0}

    class CountingBroker(FakeBroker):
        def list_assets(self):
            calls["n"] += 1
            return super().list_assets()

    r = SymbolResolver(CountingBroker(), cache_path=tmp_path / "assets.json")
    r.resolve("TSLA")
    r.resolve("AAPL")
    assert calls["n"] == 1
    # A fresh resolver reads the cache file, no broker call at all.
    r2 = SymbolResolver(CountingBroker(), cache_path=tmp_path / "assets.json")
    assert r2.resolve("NVDA").ok
    assert calls["n"] == 1


def test_ticker_trusted_when_catalog_unavailable(tmp_path):
    class DeadBroker(FakeBroker):
        def list_assets(self):
            raise ConnectionError("api down")

    r = SymbolResolver(DeadBroker(), cache_path=tmp_path / "assets.json")
    res = r.resolve("TSLA")
    assert res.ok and res.symbol == "TSLA" and "unverified" in res.note


# --- NL fallback additions ---------------------------------------------------

def _parse(text: str):
    return object.__new__(Listener)._parse_nl(text)


def test_greeting_gets_friendly_reply_not_error():
    out = _parse("Hey")
    assert out["cmd"] == "unknown" and "👋" in out["reply"]


def test_unresolved_buy_hands_raw_words_up():
    out = _parse("Buy 5 spacex")
    assert out["cmd"] == "buy" and out["sym"] is None
    assert "spacex" in out["raw"] and out["qty"] == 5


def test_known_buy_still_resolves_statically():
    assert _parse("buy 15 Tesla with 8% stop") == {
        "cmd": "buy", "sym": "TSLA", "qty": 15, "stop": 8.0}
