"""
Entrypoint: re-attach a protective stop to any position the reconciler finds
UNPROTECTED -- i.e. genuinely detected via Reconciler.reconcile(), not a
hardcoded symbol list, so this stays correct if it's ever needed again.

Why this exists: entries used to be submitted as an atomic OTO/bracket, on the
theory that a GTC-requested parent would keep the attached stop leg alive.
Alpaca doesn't honor that -- confirmed against real account history -- so
every stop leg silently expired at the same day's close, leaving positions
naked with no error, no halt, nothing, until the reconciler happened to catch
one. `src/execution/order_manager.py` no longer submits entries that way (see
docs/SAFEGUARDS.md "How a protected entry actually works"), but that fix is
not retroactive: a position opened before it still has a dead stop_order_id
recorded locally, pointing at an order that's long since expired at the broker.

This reuses the SAME repair path a normal cycle uses -- OrderManager.settle()
-> _attach_protection() -- rather than a bespoke one-off: clear the local
stop_order_id for anything the reconciler confirms has no real resting stop,
then re-settle it, which re-derives the fill and submits a fresh standalone
GTC stop (or GTC OCO, if the position has a take-profit).

Usage:
    python -m scripts.reattach_missing_stops --dry-run   # report only, no orders
    python -m scripts.reattach_missing_stops             # actually repair
"""

from __future__ import annotations

import argparse

from src.common.config import load_config
from src.common.logging import get_logger
from src.core.state_store import StateStore
from src.execution.broker_alpaca import build_broker
from src.execution.order_manager import OrderManager
from src.execution.reconciler import Reconciler

log = get_logger("reattach_missing_stops")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-attach a stop to any UNPROTECTED position.")
    parser.add_argument("--dry-run", action="store_true", help="report only; place no orders")
    args = parser.parse_args()

    config = load_config()
    broker = build_broker(config)  # no allow_live: this is a paper-only remediation tool
    store = StateStore()
    reconciler = Reconciler(broker)
    om = OrderManager(broker)

    positions = store.load()
    report = reconciler.reconcile(positions)

    if not report.unprotected_positions:
        print("Nothing to do -- reconcile reports no UNPROTECTED positions.")
        return

    print(f"UNPROTECTED (confirmed by reconcile, not hardcoded): {report.unprotected_positions}")
    if args.dry_run:
        print("--dry-run: no orders placed.")
        return

    repaired, failed = [], []
    for symbol in report.unprotected_positions:
        pos = positions[symbol]
        pos.stop_order_id = None  # the recorded one is confirmed dead; force re-attachment
        try:
            om.settle(pos)
        except Exception as exc:  # a genuine per-symbol failure must not abort the rest
            log.error("failed to re-attach protection for %s: %s", symbol, exc)
            failed.append(symbol)
            continue
        repaired.append((symbol, pos.stop_order_id, pos.current_stop))
        print(f"  {symbol}: attached stop {pos.stop_order_id} @ {pos.current_stop:g}")

    store.save(positions)

    print(f"\nRepaired: {len(repaired)}  Failed: {len(failed)}")
    if failed:
        print(f"STILL UNPROTECTED, needs attention: {failed}")

    verify = reconciler.reconcile(store.load())
    print(f"\nPost-repair reconcile: unprotected={verify.unprotected_positions or 'none'}")


if __name__ == "__main__":
    main()
