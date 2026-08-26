"""Unit tests for src/common/config.py's Config helpers."""

from __future__ import annotations

from dataclasses import replace

from src.common.config import load_config


def _config(watchlist):
    base = load_config()
    return replace(base, symbols={**base.symbols, "watchlist": watchlist})


def test_watchlist_entry_returns_matching_row():
    config = _config([{"symbol": "AAPL", "allow_short": True},
                      {"symbol": "MSFT", "allow_short": False}])
    assert config.watchlist_entry("AAPL") == {"symbol": "AAPL", "allow_short": True}


def test_watchlist_entry_none_when_not_found_or_watchlist_empty():
    assert _config([{"symbol": "AAPL"}]).watchlist_entry("TSLA") is None
    assert _config([]).watchlist_entry("AAPL") is None


def test_watchlist_entry_is_case_sensitive_exact_match():
    assert _config([{"symbol": "AAPL"}]).watchlist_entry("aapl") is None


def _settings_config(**settings_overrides):
    base = load_config()
    return replace(base, settings={**base.settings, **settings_overrides})


def test_research_universe_uses_backtest_universe_when_configured():
    config = _settings_config(research={"backtest_universe": ["AAPL", "MSFT"]})
    assert config.research_universe() == ["AAPL", "MSFT"]


def test_research_universe_falls_back_to_watchlist():
    config = _settings_config(research={})
    assert config.research_universe() == config.enabled_symbols()


def test_data_lookback_days_reads_config_with_400_default():
    assert _settings_config(data={"lookback_days": 250}).data_lookback_days() == 250
    assert _settings_config(data={}).data_lookback_days() == 400
