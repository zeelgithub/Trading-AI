"""
Entrypoint: manual order (you choose the trade).

Places a user-specified BUY through the SAME risk gate and execution path the
autonomous bot uses: an atomic protected entry (market buy + resting stop), and
the position is recorded in the bot's state so the autonomous cycle manages it
(raises the stop, reconciles) from then on.

The risk gate still applies as a CEILING: if your quantity exceeds the position
/ exposure caps it is trimmed, and a kill switch / halt blocks it entirely.

Usage:
    python -m scripts.manual_order NVDA 20            # 10% stop (default)
    python -m scripts.manual_order NVDA 20 --stop 8
"""

from __future__ import annotations

import argparse
from datetime import date

from src.common.config import load_config
from src.common.models import Action, Decision, Intent, RiskDecision, Side
from src.core.state_store import HaltStore, StateStore
from src.data.providers.alpaca_data import AlpacaData
from src.execution.broker_alpaca import AlpacaBroker
from src.execution.order_manager import OrderManager, PositionStatus
from src.risk.ratchet_stop import PercentRatchet
from src.risk.risk_manager import AccountState, RiskManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Place a manual risk-gated buy.")
    parser.add_argument("symbol")
    parser.add_argument("qty", type=int)
    parser.add_argument("--stop", type=float, default=10.0, help="stop-loss percent (default 10)")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    config = load_config()
    halt = HaltStore()
    halted = halt.is_halted()
    if halted:
        print(f"REFUSED: bot is HALTED ({halted}). Run `run_paper --reset` first.")
        return

    broker = AlpacaBroker()
    account = broker.get_account()
    store = StateStore()
    positions = store.load()
    if symbol in positions and positions[symbol].status != PositionStatus.CLOSED:
        print(f"REFUSED: already tracking a {symbol} position ({positions[symbol].status.value}).")
        return

    price = float(AlpacaData().get_daily_bars(symbol, lookback_days=10).iloc[-1].close)
    stop = round(price * (1 - args.stop / 100.0), 2)

    intent = Intent(
        symbol=symbol, strategy="manual", side=Side.LONG, action=Action.BUY,
        confidence=1.0, entry_price=round(price, 2), stop_loss=stop, suggested_qty=float(args.qty),
    )
    intent.validate()

    gross = sum((p.filled_qty or p.qty) * getattr(p.ratchet, "entry", 0.0)
                for p in positions.values() if p.status != PositionStatus.CLOSED)
    acct_state = AccountState(
        equity=account.equity, start_of_day_equity=account.last_equity,
        buying_power=account.buying_power, last_price=price,
        open_positions=sum(1 for p in positions.values() if p.status != PositionStatus.CLOSED),
        gross_exposure_value=gross, is_intraday=False, day_trade_count=account.daytrade_count,
    )

    decision = RiskManager(config).evaluate(intent, acct_state)
    if decision.decision == Decision.VETO:
        print(f"VETOED by risk gate: {decision.reason}")
        return

    final_qty = min(args.qty, int(decision.approved_qty))
    if final_qty <= 0:
        print(f"VETOED: sized to zero ({decision.reason})")
        return
    if final_qty < args.qty:
        print(f"NOTE: requested {args.qty} trimmed to {final_qty} by risk caps ({decision.reason})")

    dec = RiskDecision(
        intent=intent,
        decision=Decision.APPROVE if final_qty == args.qty else Decision.RESIZE,
        approved_qty=float(final_qty),
    )
    ratchet = PercentRatchet(entry=price, side=Side.LONG, initial_stop_pct=args.stop)

    if not broker.is_market_open():
        print("Market is CLOSED -- submitting; the entry will queue for the next open.\n")

    try:
        pos = OrderManager(broker).open_position(dec, ratchet, tag=f"manual-{date.today().isoformat()}")
        OrderManager(broker).settle(pos)
    except Exception as exc:
        print(f"ORDER FAILED at broker: {exc}")
        return

    positions[symbol] = pos
    store.save(positions)

    print(f"PLACED (paper): BUY {final_qty} {symbol} @ ~${price:,.2f}")
    print(f"  protective stop: ${stop:,.2f} (-{args.stop:g}%)   order: {pos.entry_order_id}  status: {pos.status.value}")
    print(f"  recorded in bot state -- the autonomous cycle will manage it (reconcile + trail stop).")


if __name__ == "__main__":
    main()
