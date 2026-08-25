"""Unit tests for src/discovery/universe.py -- discovery_universe() and the
S&P 500/400/600 opt-in widening (src/discovery/sp500.py, sp400.py, sp600.py)
plus the data-derived small/micro-cap and volatile screens
(src/discovery/smallcap.py, src/discovery/volatile.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.common.config import load_config
from src.discovery.smallcap import SMALLCAP_TICKERS
from src.discovery.sp400 import SP400_TICKERS
from src.discovery.sp500 import SP500_TICKERS
from src.discovery.sp600 import SP600_TICKERS
from src.discovery.universe import discovery_universe
from src.discovery.volatile import VOLATILE_TICKERS

_NO_DISCLOSURES = "does/not/exist.json"  # congress_buy_tickers() -> [] for a missing path


def _config(sp500: bool = False, sp400: bool = False, sp600: bool = False,
            smallcap: bool = False, volatile: bool = False, extra: list[str] | None = None):
    base = load_config()
    settings = {
        **base.settings,
        "discovery": {
            **base.settings.get("discovery", {}),
            "universe": {"extra": extra or [], "sp500": sp500, "sp400": sp400,
                         "sp600": sp600, "smallcap": smallcap, "volatile": volatile},
        },
    }
    symbols = {"watchlist": [{"symbol": "AAPL", "enabled": True}], "defaults": {}}
    return replace(base, settings=settings, symbols=symbols)


def test_sp500_off_by_default_behavior_unaffected():
    config = _config(sp500=False, extra=["MSFT"])
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe == ["AAPL", "MSFT"]
    for t in SP500_TICKERS:
        assert t not in universe or t in ("AAPL", "MSFT")


def test_sp500_on_adds_the_full_list():
    config = _config(sp500=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    for t in SP500_TICKERS:
        assert t in universe


def test_sp500_on_does_not_duplicate_watchlist_or_extra_overlap():
    """AAPL is both the watchlist symbol AND an S&P 500 constituent -- must
    appear exactly once."""
    config = _config(sp500=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe.count("AAPL") == 1


def test_sp500_list_has_no_internal_duplicates():
    assert len(SP500_TICKERS) == len(set(SP500_TICKERS))


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "BRK.B", "GOOGL", "JPM"])
def test_sp500_list_contains_well_known_names(ticker):
    assert ticker in SP500_TICKERS


def test_sp400_and_sp600_off_by_default_behavior_unaffected():
    config = _config()
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe == ["AAPL"]


def test_sp400_on_adds_the_full_list():
    config = _config(sp400=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    for t in SP400_TICKERS:
        assert t in universe


def test_sp600_on_adds_the_full_list():
    config = _config(sp600=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    for t in SP600_TICKERS:
        assert t in universe


def test_all_three_together_deduplicate_cross_index_overlap():
    """Several tickers appear in more than one of these lists in practice
    (index reconstitutions move companies between them) -- must not produce
    duplicates in the final universe."""
    config = _config(sp500=True, sp400=True, sp600=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert len(universe) == len(set(universe))


def test_sp400_list_has_no_internal_duplicates():
    assert len(SP400_TICKERS) == len(set(SP400_TICKERS))


def test_sp600_list_has_no_internal_duplicates():
    assert len(SP600_TICKERS) == len(set(SP600_TICKERS))


@pytest.mark.parametrize("ticker", ["AAON", "BURL", "CHWY", "GME", "PINS"])
def test_sp400_list_contains_well_known_names(ticker):
    assert ticker in SP400_TICKERS


@pytest.mark.parametrize("ticker", ["CAKE", "ETSY", "KSS", "LYFT", "PZZA"])
def test_sp600_list_contains_well_known_names(ticker):
    assert ticker in SP600_TICKERS


@pytest.mark.parametrize("ticker", ["WHR", "WING", "XRAY", "YETI", "ZION"])
def test_sp400_list_contains_the_recovered_vz_tail(ticker):
    """Originally missing (content-length wall in the source fetch, see
    sp400.py's docstring) -- recovered via a real-browser re-fetch."""
    assert ticker in SP400_TICKERS


@pytest.mark.parametrize("ticker", ["TRIP", "UNFI", "URBN", "WU", "YELP"])
def test_sp600_list_contains_the_recovered_tz_tail(ticker):
    """Originally missing (content-length wall in the source fetch, see
    sp600.py's docstring) -- recovered via a real-browser re-fetch."""
    assert ticker in SP600_TICKERS


def test_smallcap_off_by_default_behavior_unaffected():
    config = _config(smallcap=False, extra=["MSFT"])
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe == ["AAPL", "MSFT"]


def test_smallcap_on_adds_the_full_list():
    config = _config(smallcap=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    for t in SMALLCAP_TICKERS:
        assert t in universe


def test_smallcap_list_has_no_internal_duplicates():
    assert len(SMALLCAP_TICKERS) == len(set(SMALLCAP_TICKERS))


def test_smallcap_list_does_not_overlap_sp_lists():
    """smallcap.py exists to ADD to the S&P lists, not duplicate them --
    scripts/build_smallcap_universe.py explicitly excludes anything already
    covered, so there should be zero overlap."""
    sp_all = set(SP500_TICKERS) | set(SP400_TICKERS) | set(SP600_TICKERS)
    assert not (set(SMALLCAP_TICKERS) & sp_all)


def test_all_five_together_deduplicate_cross_list_overlap():
    config = _config(sp500=True, sp400=True, sp600=True, smallcap=True, volatile=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert len(universe) == len(set(universe))


def test_all_five_together_preserves_first_seen_order():
    """The O(n) dedup (a side `seen` set) must produce the exact same
    order as the original O(n^2) `t not in syms` scan: watchlist, then
    congress, then extra, then sp500/400/600/smallcap/volatile in that
    stage order, first occurrence wins."""
    config = _config(sp500=True, sp400=True, sp600=True, smallcap=True, volatile=True,
                      extra=["MSFT"])
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe[0] == "AAPL"
    assert universe[1] == "MSFT"
    expected_len = len({
        "AAPL", "MSFT", *SP500_TICKERS, *SP400_TICKERS, *SP600_TICKERS,
        *SMALLCAP_TICKERS, *VOLATILE_TICKERS,
    })
    assert len(universe) == expected_len


@pytest.mark.parametrize("ticker", ["CLOV", "CHGG", "BYND", "AMC"])
def test_smallcap_list_contains_well_known_names(ticker):
    """ASAN/ACHR/SOUN/WOLF (this test's original spot-check names) moved to
    volatile.py after the 2026-08-24 threshold rebuild -- their market cap
    sits outside the $50M-$6B band, so they were always destined for
    volatile.py once real price/liquidity data stopped masking that (see
    test_volatile_list_contains_well_known_names below, and
    build_smallcap_universe.py's stage2_exclude_existing_universe docstring
    for the self-exclusion bug that first rebuild caught)."""
    assert ticker in SMALLCAP_TICKERS


def test_volatile_off_by_default_behavior_unaffected():
    config = _config(volatile=False, extra=["MSFT"])
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    assert universe == ["AAPL", "MSFT"]


def test_volatile_on_adds_the_full_list():
    config = _config(volatile=True)
    universe = discovery_universe(config, disclosures=_NO_DISCLOSURES)
    for t in VOLATILE_TICKERS:
        assert t in universe


def test_volatile_list_has_no_internal_duplicates():
    assert len(VOLATILE_TICKERS) == len(set(VOLATILE_TICKERS))


def test_volatile_list_does_not_overlap_other_lists():
    """volatile.py exists to ADD names too large for smallcap.py's cap band
    (or not yet in an S&P index) but still genuinely volatile --
    scripts/build_volatile_universe.py reuses smallcap.py's existing-universe
    exclusion, so there should be zero overlap with any other list."""
    others = set(SP500_TICKERS) | set(SP400_TICKERS) | set(SP600_TICKERS) | set(SMALLCAP_TICKERS)
    assert not (set(VOLATILE_TICKERS) & others)


@pytest.mark.parametrize("ticker", ["RIOT", "CIFR", "WULF", "RKLB", "ASTS", "OKLO"])
def test_volatile_list_contains_well_known_names(ticker):
    """These are the exact names that motivated building this list: real,
    well-known, genuinely volatile companies that fell through both the S&P
    lists (not yet index members) and smallcap.py (several are multi-billion-
    dollar, above its $6B cap-band ceiling)."""
    assert ticker in VOLATILE_TICKERS
