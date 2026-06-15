"""
Entrypoint: run one orchestrator cycle (paper).

By default this runs in SHADOW mode -- it decides and logs but submits NOTHING.
Pass --execute to actually place orders (paper account). Live trading further
requires `mode: live` in config AND --allow-live, by design.

Usage:
    python -m scripts.run_paper            # shadow (no orders)
    python -m scripts.run_paper --execute  # place paper orders
"""

from __future__ import annotations

import argparse

from src.core.orchestrator import Orchestrator
from src.execution.broker_alpaca import AlpacaBroker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one orchestrator cycle.")
    parser.add_argument("--execute", action="store_true", help="place orders (default: shadow)")
    parser.add_argument("--allow-live", action="store_true", help="permit live-mode execution")
    parser.add_argument("--reset", action="store_true", help="clear a persisted HALT and exit")
    args = parser.parse_args()

    orch = Orchestrator(
        broker=AlpacaBroker(),
        execute=args.execute,
        allow_live=args.allow_live,
    )

    if args.reset:
        orch.reset()
        print("Halt cleared -- bot reset to IDLE.")
        return

    mode = "EXECUTE" if args.execute else "SHADOW"
    print(f"Running orchestrator cycle in {mode} mode...\n")

    report = orch.run_cycle()

    print(report.summary())
    if report.halted:
        print(f"  halt reason: {report.halt_reason}")
        return
    for sym, dec, qty in report.decisions:
        print(f"  {sym}: {dec} qty={qty:g}")
    for sym, stop in report.stops_raised:
        print(f"  {sym}: stop raised -> {stop}")
    verb = "OPENED" if args.execute else "WOULD OPEN"
    for sym in report.opened:
        print(f"  {verb}: {sym}")
    if not args.execute:
        print("\n(shadow mode -- no orders placed)")


if __name__ == "__main__":
    main()
