"""
Entrypoint: paper-shadow signal scan.

For each watchlist symbol: refresh daily bars -> build features -> classify
regime -> run the active strategy -> apply the sentiment gate -> run the risk
gatekeeper. Prints the regime, the candidate trade signal (JSON contract), and
the risk decision. READ-ONLY: it reads the account/positions but NEVER places,
modifies, or cancels an order. This is the "shadow" mode -- it shows what the
bot *would* do.

Usage:
    python -m scripts.scan_signals
"""

from __future__ import annotations

import json

from src.common.config import load_config
from src.data import store
from src.data.features import build_features
from src.data.ingest import ingest_symbol
from src.execution.broker_alpaca import AlpacaBroker
from src.risk.risk_manager import AccountState, RiskManager
from src.strategy.regime_filter import RegimeFilter
from src.strategy.registry import build_strategies
from src.strategy.sentiment_gate import SentimentGate


def main() -> None:
    config = load_config()
    lookback = int(config.get("settings.data.lookback_days", 400))
    symbols = config.enabled_symbols()

    regime_filter = RegimeFilter(config)
    sentiment = SentimentGate(config)  # neutral (no live scorer wired yet)
    strategies = build_strategies(config)
    risk = RiskManager(config)

    # Read-only account snapshot (NEVER places orders).
    broker = AlpacaBroker()
    acct = broker.get_account()
    positions = broker.list_positions()
    gross = sum(p.qty * p.avg_entry_price for p in positions)

    conn = store.connect()
    print(f"Paper-shadow scan | equity=${acct.equity:,.0f} | open positions={len(positions)}\n")

    for symbol in symbols:
        ingest_symbol(conn, symbol, lookback_days=lookback)
        feats = build_features(store.load_bars(conn, symbol), config)
        if feats.empty:
            print(f"{symbol:<6} no data")
            continue

        regime = regime_filter.classify(feats)
        active = regime_filter.active_strategy(feats)
        last_price = float(feats.iloc[-1].close)

        line = f"{symbol:<6} regime={regime.value:<9} strategy={active or '-':<16}"
        if active is None:
            print(line + "stand aside")
            continue

        intent = strategies[active].generate(symbol, feats)
        if intent is None:
            print(line + "no setup")
            continue

        gated = sentiment.apply(intent)
        if gated is None:
            print(line + "blocked by sentiment")
            continue

        account_state = AccountState(
            equity=acct.equity,
            start_of_day_equity=acct.last_equity,
            buying_power=acct.buying_power,
            last_price=last_price,
            open_positions=len(positions),
            gross_exposure_value=gross,
            is_intraday=False,
            day_trade_count=acct.daytrade_count,
        )
        decision = risk.evaluate(gated, account_state)
        print(line + f"-> {decision.decision.value.upper()} qty={decision.approved_qty:g}")
        print("       signal " + json.dumps(gated.to_dict()))
        print(f"       risk:  {decision.reason}")

    conn.close()
    print("\n(shadow mode -- no orders placed)")


if __name__ == "__main__":
    main()
