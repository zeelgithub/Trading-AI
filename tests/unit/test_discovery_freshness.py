"""Unit tests for src/discovery/freshness.py -- the staleness check for the
static/data-derived universe-widening lists (sp500.py, sp400.py, sp600.py,
smallcap.py, volatile.py), each of which carries a SOURCED_DATE that nothing
read before this module existed."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from src.common.config import load_config
from src.discovery.freshness import stale_universe_lists, staleness_detail


def _config(*, max_staleness_days: int = 45, **flags):
    base = load_config()
    settings = {
        **base.settings,
        "discovery": {
            **base.settings.get("discovery", {}),
            "universe": {**base.settings.get("discovery", {}).get("universe", {}),
                        "max_staleness_days": max_staleness_days, **flags},
        },
    }
    return replace(base, settings=settings)


def test_disabled_lists_are_never_flagged_regardless_of_age():
    config = _config(sp500=False, sp400=False, sp600=False, smallcap=False, volatile=False)
    assert stale_universe_lists(config, today=date(2030, 1, 1)) == []


def test_enabled_list_within_threshold_is_not_flagged():
    config = _config(sp500=True, max_staleness_days=45)
    # sp500.py's SOURCED_DATE is 2026-08-24 -- one day later is well inside 45.
    assert stale_universe_lists(config, today=date(2026, 8, 25)) == []


def test_enabled_list_past_threshold_is_flagged():
    config = _config(sp500=True, sp400=False, sp600=False, smallcap=False, volatile=False,
                     max_staleness_days=45)
    stale = stale_universe_lists(config, today=date(2027, 1, 1))
    assert len(stale) == 1
    flag, age, max_age = stale[0]
    assert flag == "sp500"
    assert age > max_age == 45


def test_only_enabled_lists_are_checked():
    config = _config(sp500=True, sp400=False, sp600=False, smallcap=False, volatile=False,
                     max_staleness_days=45)
    stale = stale_universe_lists(config, today=date(2030, 1, 1))
    assert [flag for flag, _, _ in stale] == ["sp500"]


def test_staleness_detail_mentions_build_script_for_generated_lists():
    detail = staleness_detail([("smallcap", 100, 45)])
    assert "build_smallcap_universe" in detail


def test_staleness_detail_mentions_manual_resource_for_index_lists():
    detail = staleness_detail([("sp500", 100, 45)])
    assert "build_" not in detail
    assert "sp500.py" in detail
