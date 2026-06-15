"""Tests for the Risk Gatekeeper composing the guards end-to-end.

Uses the real config/*.yaml (loads offline, no credentials needed).
"""

from __future__ import annotations

from datetime import date

from src.common.models import Decision, Intent, Side
from src.risk.risk_manager import AccountState, RiskManager


def base_account(**over) -> AccountState:
    defaults = dict(
        equity=50000.0,
        start_of_day_equity=50000.0,
        buying_power=200000.0,
        last_price=100.0,
        open_positions=0,
        gross_exposure_value=0.0,
        is_intraday=False,
        as_of=date(2026, 6, 12),
    )
    defaults.update(over)
    return AccountState(**defaults)


def trend_intent() -> Intent:
    return Intent(symbol="AAPL", strategy="trend_following", side=Side.LONG, confidence=0.7)


def test_approves_and_sizes_a_clean_trend_trade():
    rm = RiskManager()
    decision = rm.evaluate(trend_intent(), base_account())
    assert decision.decision in (Decision.APPROVE, Decision.RESIZE)
    # trend stop = entry * 0.9 = 90; max position 10% of 50k / 100 = 50 shares cap.
    assert decision.approved_qty == 50.0


def test_kill_switch_vetoes_everything():
    rm = RiskManager()
    acct = base_account(equity=47000.0)  # 6% daily loss > 4% limit
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.VETO
    assert "KILL SWITCH" in decision.reason


def test_unknown_strategy_without_stop_is_vetoed():
    rm = RiskManager()
    intent = Intent(symbol="AAPL", strategy="mystery", side=Side.LONG, confidence=0.5)
    decision = rm.evaluate(intent, base_account())
    assert decision.decision == Decision.VETO
    assert "no stop" in decision.reason


def test_max_open_positions_blocks():
    rm = RiskManager()
    acct = base_account(open_positions=10)
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.VETO
    assert "max_open_positions" in decision.reason


def test_gross_exposure_resizes_down():
    rm = RiskManager()
    # Leave room for only ~5 shares at $100 before hitting the gross cap.
    acct = base_account(gross_exposure_value=50000 * 1.75 - 500)
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.RESIZE
    assert decision.approved_qty == 5.0
