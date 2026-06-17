"""Tests for the human-in-the-loop decision briefs (paste-into-Claude.ai)."""

from __future__ import annotations

from src.notify.briefs import incident_brief, strategy_review_brief, symbol_brief


def test_strategy_review_brief_includes_scoreboard_and_positions():
    scoreboard = {"strategies": [
        {"strategy": "breakout", "verdict": "noise", "num_trades": 8, "sharpe": 0.06,
         "psr": 0.57, "p_value": 0.46, "total_pnl": 30.0, "live_num_trades": 0, "live_total_pnl": 0.0},
    ]}
    positions = {"positions": [
        {"symbol": "AAPL", "side": "long", "qty": 50, "entry": 100.0, "stop": 90.0,
         "strategy": "trend_following", "status": "open"},
    ], "count": 1}

    brief = strategy_review_brief(scoreboard, positions, benchmark={"total_return": 0.355, "sharpe": 2.27})
    assert "Claude.ai" in brief
    assert "breakout: NOISE" in brief
    assert "AAPL long 50" in brief
    assert "SPY buy & hold +35.5%" in brief
    assert "/rotate" in brief


def test_strategy_review_brief_handles_empty():
    brief = strategy_review_brief({"strategies": []}, {"positions": []})
    assert "empty" in brief
    assert "(none)" in brief


def test_incident_brief_includes_class_and_events():
    halt = {"class": "reconcile_mismatch", "reason": "TSLA qty differs", "ts": "2026-06-16T20:00:00+00:00"}
    events = [
        {"ts": "2026-06-16T19:59:00+00:00", "event": "reconcile_mismatch", "detail": "TSLA"},
        {"ts": "2026-06-16T20:00:00+00:00", "event": "halt", "reason": "reconcile"},
    ]
    brief = incident_brief(halt, events)
    assert "reconcile_mismatch" in brief
    assert "TSLA qty differs" in brief
    assert "halt" in brief
    assert "/reset" in brief


def test_incident_brief_handles_no_events():
    assert "(none)" in incident_brief({"class": "stale_data", "reason": "x", "ts": "t"}, [])


def test_symbol_brief_includes_indicators_and_verdict():
    ind = {"symbol": "NVDA", "date": "2026-06-15", "close": 120.0, "ema50": 110.0,
           "rsi": 58.0, "adx": 27.0, "regime": "trending", "ema200": None}
    score = {"strategy": "trend_following", "verdict": "promising", "psr": 0.9}
    brief = symbol_brief("nvda", ind, score)
    assert "NVDA" in brief
    assert "close: 120.0" in brief
    assert "trend_following" in brief and "PROMISING" in brief
    assert "ema200" not in brief          # None values are omitted
    assert "/buy" in brief
