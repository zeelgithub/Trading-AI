"""
Demo: show exactly what a TSLA buy would do, end-to-end, in SHADOW.

Uses the LIVE TSLA price and your REAL (read-only) account equity, runs the
intent through the real risk gatekeeper, then shows the exact protected order
the order manager would submit and how the ratchet stop would climb.

NO order is placed. Read-only.

Usage:
    python -m scripts.demo_trade            # TSLA, 15 shares requested
    python -m scripts.demo_trade NVDA 20
"""

from __future__ import annotations

import sys

from src.common.config import load_config
from src.common.models import Action, Intent, Side
from src.data.providers.alpaca_data import AlpacaData
from src.execution.broker_alpaca import AlpacaAccountReader, exit_side
from src.execution.order_manager import _coid
from src.risk.ratchet_stop import build_ratchet
from src.risk.risk_manager import AccountState, RiskManager


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    requested = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    strategy = "trend_following"

    config = load_config()
    price = float(AlpacaData().get_daily_bars(symbol, lookback_days=10).iloc[-1].close)
    acct = AlpacaAccountReader().get_account()  # read-only

    rp = config.risk_limits["ratchet_stop"][strategy]
    stop = round(price * (1 - rp["initial_stop_pct"] / 100.0), 2)

    intent = Intent(
        symbol=symbol, strategy=strategy, side=Side.LONG, action=Action.BUY,
        confidence=0.60, entry_price=round(price, 2), stop_loss=stop,
        take_profit=None, suggested_qty=float(requested),
    )

    print(f"=== 1. Trade signal (Intent) ===")
    print("   " + str(intent.to_dict()))

    print(f"\n=== 2. Risk gatekeeper ===")
    account = AccountState(
        equity=acct.equity, start_of_day_equity=acct.last_equity,
        buying_power=acct.buying_power, last_price=price, open_positions=0,
        gross_exposure_value=0.0, is_intraday=False, day_trade_count=acct.daytrade_count,
    )
    decision = RiskManager(config).evaluate(intent, account)
    risk_dollars = acct.equity * float(config.risk_limits["allocation"]["per_strategy_risk_pct"]) / 100
    max_pos_value = acct.equity * float(config.risk_limits["position"]["max_position_pct"]) / 100
    print(f"   equity=${acct.equity:,.0f}  TSLA last=${price:,.2f}  stop=${stop:,.2f} (-{rp['initial_stop_pct']}%)")
    print(f"   you requested:   {requested} shares  (${requested*price:,.0f} notional)")
    print(f"   risk budget:     ${risk_dollars:,.0f}/trade  ->  ~{int(risk_dollars/(price-stop))} sh by risk")
    print(f"   max-position cap: {config.risk_limits['position']['max_position_pct']}% = ${max_pos_value:,.0f} -> {int(max_pos_value/price)} sh")
    print(f"   --> DECISION: {decision.decision.value.upper()}  qty={decision.approved_qty:g}  ({decision.reason})")

    qty = decision.approved_qty
    if qty <= 0:
        print("\n   (risk gate vetoed -- no order)")
        return

    print(f"\n=== 3. Protected order the bot would submit (qty={qty:g}) ===")
    has_tp = intent.take_profit is not None
    klass = "bracket (OTOCO)" if has_tp else "OTO"
    print(f"   order_class = {klass}  client_order_id base = {_coid(symbol, strategy, 'YYYY-MM-DD', 'entry')}")
    print(f"   leg 1 ENTRY: market BUY  {qty:g} {symbol} @ ~${price:,.2f}")
    print(f"   leg 2 STOP:  stop {exit_side(Side.LONG).value.upper()}  {qty:g} {symbol} @ ${stop:,.2f}")
    if has_tp:
        print(f"   leg 3 TAKE-PROFIT: limit SELL {qty:g} {symbol} @ ${intent.take_profit:,.2f}")
    else:
        print(f"   (no take-profit leg -- trend rides the ratchet stop)")

    print(f"\n=== 4. Ratchet stop as TSLA rises (never moves down) ===")
    ratchet = build_ratchet(strategy, rp, price, Side.LONG)
    print(f"   entry ${price:,.2f}  ->  stop ${ratchet.stop:,.2f}")
    for pct in (10, 20, 40, 60):
        lvl = round(price * (1 + pct / 100.0), 2)
        ratchet.update(lvl)
        print(f"   price +{pct:>2}% (${lvl:,.2f})  ->  stop ${ratchet.stop:,.2f}{'  [profit locked]' if ratchet.armed else ''}")

    print("\n(shadow demo -- no order placed)")


if __name__ == "__main__":
    main()
