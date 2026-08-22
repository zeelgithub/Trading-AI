"""
Entrypoint: run one orchestrator cycle (paper).

Modes (safest first):
    python -m scripts.run_paper            # SHADOW: decide + log, submit NOTHING
    python -m scripts.run_paper --propose  # decide + push proposals to phone (no orders)
    python -m scripts.run_paper --execute  # place paper orders directly

PROPOSE is the default for autonomous runs: with `approval.require_approval: true`
in config (the default), `--execute` is converted to `--propose` so the bot asks
before it trades. Approve from the phone (Telegram) to actually place an order.
Set `approval.require_approval: false` to restore direct auto-execution.

Live trading still additionally requires `mode: live` in config AND --allow-live.
"""

from __future__ import annotations

import argparse

from src.common.config import load_config
from src.core.orchestrator import Orchestrator
from src.core.proposals import ProposalStore
from src.data.providers.news import AlpacaNews
from src.execution.broker_alpaca import build_broker
from src.notify.telegram import build_notifier
from src.strategy.news_sentiment_scorer import NewsSentimentScorer


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one orchestrator cycle.")
    parser.add_argument("--execute", action="store_true", help="place orders directly (default: shadow)")
    parser.add_argument("--propose", action="store_true", help="push proposals to phone, place nothing")
    parser.add_argument("--allow-live", action="store_true", help="permit live-mode execution")
    parser.add_argument("--reset", action="store_true", help="clear a persisted HALT and exit")
    args = parser.parse_args()

    config = load_config()
    notifier = build_notifier(config)

    # Resolve the effective mode. Approval gating turns --execute into --propose.
    require_approval = bool(config.get("settings.approval.require_approval", True))
    propose = args.propose or (args.execute and require_approval)
    execute = args.execute and not propose

    # Real sentiment scorer (2026-08-22): recent headline tone, same free
    # Alpaca news feed + deterministic lexicon discovery's NewsSource uses.
    # SentimentGate.apply() already isolates a raised exception as
    # on_feed_unavailable=skip_gate, so a feed outage here degrades to the
    # prior scorer=None behavior for that cycle, not a halt.
    news_days = int(config.get("strategies.sentiment_gate.news_scorer_lookback_days", 3))
    scorer = NewsSentimentScorer(AlpacaNews(), days=news_days)

    orch = Orchestrator(
        broker=build_broker(config, allow_live=args.allow_live),
        config=config,
        execute=execute,
        propose=propose,
        allow_live=args.allow_live,
        notifier=notifier,
        scorer=scorer,
    )

    if args.reset:
        orch.reset()
        print("Halt cleared -- bot reset to IDLE.")
        return

    if args.execute and propose:
        print("approval required (config) -- proposing instead of executing.\n")

    mode = "PROPOSE" if propose else ("EXECUTE" if execute else "SHADOW")
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

    if propose:
        _emit_proposals(report, notifier)
    else:
        verb = "OPENED" if execute else "WOULD OPEN"
        for sym in report.opened:
            print(f"  {verb}: {sym}")
        if not execute:
            print("\n(shadow mode -- no orders placed)")


def _emit_proposals(report, notifier) -> None:
    """Persist proposals and push each to the phone with Approve/Deny buttons."""
    store = ProposalStore()
    store.purge_expired()
    if not report.proposals:
        print("\n(propose mode -- no setups to propose)")
        notifier.alert("daily_summary", "Cycle complete — no trade setups today.")
        return
    for proposal in report.proposals:
        store.add(proposal)
        notifier.proposal(proposal)
        print(f"  PROPOSED: {proposal.summary()}  (id {proposal.id})")
    print(f"\n{len(report.proposals)} proposal(s) sent to phone -- awaiting approval.")
    notifier.alert("daily_summary", f"{len(report.proposals)} trade proposal(s) awaiting approval.")


if __name__ == "__main__":
    main()
