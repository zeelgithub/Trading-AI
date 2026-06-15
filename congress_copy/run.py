"""
Entrypoint: congress copy-trading SHADOW run.

Reads disclosures from the JSON file (populated by the ingestion job), mirrors
the chosen politician's recent STOCK trades through the risk gate, and logs what
it would buy/sell. Read-only on the broker (account snapshot only); places NO
orders.

Usage:
    python -m congress_copy.run
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from congress_copy.copy_trader import CopyConfig, CopyTrader, SeenStore
from congress_copy.providers import JSONFileProvider
from src.data.providers.alpaca_data import AlpacaData
from src.execution.broker_alpaca import AlpacaBroker
from src.risk.risk_manager import AccountState, RiskManager

ROOT = Path(__file__).resolve().parents[1]


def _load_config() -> CopyConfig:
    with (ROOT / "congress_copy" / "config.yaml").open(encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    return CopyConfig(
        politician=d.get("politician", "Ro Khanna"),
        equities_only=d.get("equities_only", True),
        max_disclosure_age_days=int(d.get("max_disclosure_age_days", 60)),
        initial_stop_pct=float(d.get("initial_stop_pct", 10.0)),
        strategy_name=d.get("strategy_name", "congress_copy"),
        execute=bool(d.get("execute", False)),
    )


def _price_fn():
    data = AlpacaData()

    @lru_cache(maxsize=256)
    def price(ticker: str):
        try:
            bars = data.get_daily_bars(ticker, lookback_days=10)
            return None if bars.empty else float(bars.iloc[-1].close)
        except Exception:
            return None

    return price


def main() -> None:
    config = _load_config()
    provider = JSONFileProvider(ROOT / "congress_copy" / "data" / "disclosures.json")
    trades = provider.fetch()

    broker = AlpacaBroker()
    acct = broker.get_account()
    positions = broker.list_positions()
    account = AccountState(
        equity=acct.equity,
        start_of_day_equity=acct.last_equity,
        buying_power=acct.buying_power,
        last_price=0.0,
        open_positions=len(positions),
        gross_exposure_value=sum(p.qty * p.avg_entry_price for p in positions),
        is_intraday=False,
        day_trade_count=acct.daytrade_count,
    )

    trader = CopyTrader(
        config=config,
        risk=RiskManager(),
        price_fn=_price_fn(),
        seen_store=SeenStore(ROOT / "congress_copy" / "state" / "seen.json"),
    )
    report = trader.run(trades, account)

    print(f"Congress copy SHADOW | mirroring {config.politician!r}")
    print(report.summary() + "\n")
    for a in report.actions:
        print(
            f"  {a['ticker']:<6} disclosed={a['disclosed']:<4} lag={a['lag_days']}d "
            f"-> {a['action'].upper()} {a['decision']} qty={a['qty']:g}  ({a['reason']})"
        )
    print("\n(shadow mode -- no orders placed)")


if __name__ == "__main__":
    main()
