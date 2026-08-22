"""
Entrypoint: manual order (you choose the trade).

Places a user-specified BUY through the SAME risk-gated execution path the
autonomous bot and the phone use (`src/core/trade_service.place_manual`): an
atomic protected entry (market buy + resting stop), recorded in the bot's state
so the autonomous cycle then manages it (raises the stop, reconciles).

The risk gate still applies as a CEILING: an over-large quantity is trimmed, and
a kill switch / HALT blocks it entirely.

Usage:
    python -m scripts.manual_order NVDA 20            # 10% stop (default)
    python -m scripts.manual_order NVDA 20 --stop 8
"""

from __future__ import annotations

import argparse

from src.common.config import load_config
from src.core.trade_service import TradeService
from src.execution.broker_alpaca import build_broker
from src.notify.telegram import build_notifier


def main() -> None:
    parser = argparse.ArgumentParser(description="Place a manual risk-gated buy.")
    parser.add_argument("symbol")
    parser.add_argument("qty", type=int)
    parser.add_argument("--stop", type=float, default=10.0, help="stop-loss percent (default 10)")
    args = parser.parse_args()

    config = load_config()
    # No allow_live here: manual_order stays paper-only, matching its docs
    # ("risk-gated manual buy" -- no live-mode flag is documented for it).
    service = TradeService(broker=build_broker(config), config=config)
    result = service.place_manual(args.symbol, args.qty, stop_pct=args.stop)

    print(result.message)
    if result.ok:
        print("  recorded in bot state -- the autonomous cycle will manage it (reconcile + trail stop).")
        try:
            build_notifier(config).alert("fill", result.message)
        except Exception:
            pass


if __name__ == "__main__":
    main()
