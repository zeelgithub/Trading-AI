"""
Entrypoint: run one discovery pass and push the day's ideas to the phone.

    python -m scripts.run_discovery            # gather -> score -> propose top N
    python -m scripts.run_discovery --dry-run  # print ideas, push nothing, store nothing

Discovery is SUGGESTION-only: it scores candidates from the enabled free sources
(congress, technical, ...), risk-gates the top N, writes them to the shared
ProposalStore, and sends each as an Approve/Deny message. Nothing is placed until
you approve from Telegram -- the same gated path as every other proposal. Run it
on a daily schedule near the close, alongside (or instead of) run_paper.
"""

from __future__ import annotations

import argparse

from src.common.config import load_config
from src.common.logging import get_logger
from src.core.proposals import ProposalStore
from src.core.state_store import HaltStore, StateStore
from src.discovery.builder import build_discovery_pipeline
from src.discovery.ledger import DiscoveryLedger
from src.discovery.pipeline import Account
from src.execution.broker_alpaca import AlpacaAccountReader
from src.notify.digest import idea_text, ideas_header
from src.notify.telegram import build_notifier

log = get_logger("run_discovery")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one discovery pass.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print ideas only; store nothing, push nothing")
    args = parser.parse_args()

    config = load_config()
    broker = AlpacaAccountReader()
    account_raw = broker.get_account()
    account = Account(
        equity=account_raw.equity,
        last_equity=account_raw.last_equity,
        buying_power=account_raw.buying_power,
    )
    positions = StateStore().load()

    store = ProposalStore()
    store.purge_expired()
    pending_syms = {p.symbol for p in store.list_pending()}

    pipeline = build_discovery_pipeline(config)
    report = pipeline.run(account, positions, exclude=pending_syms)

    print(f"Discovery: {report.summary()}")
    for cand in report.candidates:
        print(f"  {cand.symbol}: score {cand.score:g}  sources={cand.sources}")
    for sym, why in report.skipped:
        log.info("skipped %s: %s", sym, why)

    if args.dry_run:
        print("\n--dry-run: nothing stored or pushed.")
        for p in report.proposals:
            print(f"  WOULD PROPOSE: {p.summary()}")
        return

    DiscoveryLedger().record_surface(report.candidates, report.proposals)

    notifier = build_notifier(config)
    halted = HaltStore().is_halted()
    header = ideas_header(len(report.proposals), report.screened)
    if halted:
        header += f"\n🛑 NOTE: bot is HALTED ({halted}); approvals are blocked until /reset."
    notifier.send(header)

    # Index candidates by symbol so each proposal message carries its rich text.
    cand_by_symbol = {c.symbol: c for c in report.candidates}
    for proposal in report.proposals:
        store.add(proposal)
        cand = cand_by_symbol.get(proposal.symbol)
        text = idea_text(cand) if cand else proposal.summary()
        notifier.send(text, buttons=[[("✅ Approve", f"approve:{proposal.id}"),
                                      ("❌ Deny", f"deny:{proposal.id}")]])
        print(f"  PROPOSED: {proposal.summary()}  (id {proposal.id})")

    if report.proposals:
        print(f"\n{len(report.proposals)} idea(s) pushed to phone -- awaiting approval.")
    else:
        print("\nNo ideas cleared the bar today.")


if __name__ == "__main__":
    main()
