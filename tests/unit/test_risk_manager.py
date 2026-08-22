"""Tests for the Risk Gatekeeper composing the guards end-to-end.

Uses the real config/*.yaml (loads offline, no credentials needed).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from src.common.config import load_config
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
    # trend stop = entry * 0.9 = 90, risk/share = 10.
    # per_strategy_risk_pct 1.33% * confidence 0.7 = 0.931% of 50k = $465.5
    # risk budget -> 46 shares (tighter than the 50-share max-position cap).
    assert decision.approved_qty == 46.0
    assert decision.reason.startswith("sized by risk")


def test_full_confidence_sizes_by_max_position_cap():
    """A confidence=1.0 intent (e.g. a manual/phone buy) is unaffected by the
    confidence scaling -- same numbers as the pre-confidence-sizing behavior."""
    rm = RiskManager()
    intent = Intent(symbol="AAPL", strategy="trend_following", side=Side.LONG, confidence=1.0)
    decision = rm.evaluate(intent, base_account())
    assert decision.decision in (Decision.APPROVE, Decision.RESIZE)
    # trend stop = entry * 0.9 = 90; max position 10% of 50k / 100 = 50 shares cap.
    assert decision.approved_qty == 50.0


def test_per_symbol_max_position_override_shrinks_sizing():
    # AAPL gets a tighter cap than the global default (10%); confidence=1.0 so
    # the risk cap alone would allow far more than either max-position figure,
    # isolating the override's effect the same way test_full_confidence_... does.
    base = load_config()
    symbols = {
        **base.symbols,
        "watchlist": [
            {"symbol": "AAPL", "enabled": True, "allow_short": False,
             "overrides": {"max_position_pct": 4.0}},
            *[w for w in base.symbols["watchlist"] if w["symbol"] != "AAPL"],
        ],
    }
    rm = RiskManager(replace(base, symbols=symbols))
    intent = Intent(symbol="AAPL", strategy="trend_following", side=Side.LONG, confidence=1.0)
    decision = rm.evaluate(intent, base_account())
    assert decision.decision in (Decision.APPROVE, Decision.RESIZE)
    # trend stop = entry * 0.9 = 90; overridden max position 4% of 50k / 100 = 20 shares.
    assert decision.approved_qty == 20.0


def test_symbol_without_override_uses_global_default():
    rm = RiskManager()  # MSFT has no overrides block in config/symbols.yaml
    intent = Intent(symbol="MSFT", strategy="trend_following", side=Side.LONG, confidence=1.0)
    decision = rm.evaluate(intent, base_account())
    assert decision.approved_qty == 50.0  # unchanged: global max_position_pct (10%)


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
    assert decision.reason.startswith("sized by gross_exposure")


def test_aggregate_open_risk_resizes_down():
    """Held positions already risking most of the max_open_risk_pct budget
    (4% of 50k = $2000) shrink a new trade to whatever room is left, even
    though the trade's own risk-based sizing (46 shares) and the max-position
    cap (50 shares) would otherwise allow more. `reason` must name the cap
    that actually bound -- not whichever cap ran first (see RiskManager
    step 7.5's `capped_by` tracking)."""
    rm = RiskManager()
    acct = base_account(open_risk_dollars=50000 * 0.04 - 100)  # $100 of room left
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.RESIZE
    assert decision.approved_qty == 10.0  # $100 room / $10 risk-per-share
    assert decision.reason.startswith("sized by aggregate_open_risk")


def test_aggregate_open_risk_vetoes_when_no_room():
    rm = RiskManager()
    acct = base_account(open_risk_dollars=50000 * 0.04)  # budget already fully used
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.VETO
    assert "aggregate open-risk" in decision.reason


def test_aggregate_open_risk_defaults_to_a_no_op_when_unset():
    """Callers that don't populate open_risk_dollars (default 0.0) see the
    exact pre-existing behavior -- this guard never activates by surprise."""
    rm = RiskManager()
    decision = rm.evaluate(trend_intent(), base_account())
    assert decision.approved_qty == 46.0


def test_reason_names_whichever_cap_is_tightest_when_both_apply():
    """Aggregate open-risk shrinks to 20 shares, then gross exposure shrinks
    further to 8 -- the LAST (tightest, actually-binding) cap must be the one
    named in `reason`, not the first one that happened to trigger."""
    rm = RiskManager()
    acct = base_account(
        open_risk_dollars=50000 * 0.04 - 200,        # room for 20 shares @ $10 risk/share
        gross_exposure_value=50000 * 1.75 - 800,      # room for 8 shares @ $100/share
    )
    decision = rm.evaluate(trend_intent(), acct)
    assert decision.decision == Decision.RESIZE
    assert decision.approved_qty == 8.0
    assert decision.reason.startswith("sized by gross_exposure")
