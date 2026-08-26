"""
Discovery pipeline -- discovery layer.

Wires the whole idea flow:

    gather (every source) -> group by symbol -> score -> drop held/low-score
    -> rank -> for the top N: size through the RISK GATE -> emit a Proposal.

A Proposal here is identical to one the orchestrator produces, so the existing
phone approve/deny path (ProposalStore -> TradeService.execute_approved -> risk
gate -> execution) handles it with zero changes. Discovery decides WHAT to
suggest; it never decides to trade.

Boundary: places orders NO (emits Proposals only), holds trading credentials NO.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.common.config import Config
from src.common.logging import get_logger
from src.common.models import Action, Decision, Intent, Side
from src.core.proposals import Proposal
from src.discovery.candidate import Candidate
from src.discovery.scorer import Scorer
from src.discovery.sources.base import CandidateSource
from src.execution.order_manager import PositionStatus
from src.risk.exposure import ExposureSnapshot, compute_exposure
from src.risk.risk_manager import AccountState, RiskManager

log = get_logger("discovery")

PriceFn = Callable[[str], float | None]


@dataclass
class Account:
    """The bare account numbers the pipeline needs to size against."""

    equity: float
    last_equity: float
    buying_power: float


@dataclass
class DiscoveryReport:
    candidates: list[Candidate]                  # scored + ranked, above min_score
    proposals: list[Proposal]                    # the top N that cleared the gate
    screened: int = 0                            # distinct symbols considered
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (f"screened={self.screened} above_floor={len(self.candidates)} "
                f"proposed={len(self.proposals)} skipped={len(self.skipped)}")


class DiscoveryPipeline:
    def __init__(
        self,
        sources: list[CandidateSource],
        scorer: Scorer,
        risk: RiskManager,
        config: Config,
        price_fn: PriceFn,
        *,
        top_n: int = 4,
        min_score: float = 25.0,
        default_stop_pct: float = 10.0,
        expiry_minutes: int = 1080,
        min_price: float = 5.0,
        source_timeout_seconds: float = 300.0,
    ) -> None:
        self.sources = sources
        self.scorer = scorer
        self.risk = risk
        self.config = config
        self.price_fn = price_fn
        self.top_n = int(top_n)
        self.min_score = float(min_score)
        self.default_stop_pct = float(default_stop_pct)
        self.expiry_minutes = int(expiry_minutes)
        # Bounds how long _gather() waits on ALL sources combined (they all
        # start at once, so this is equivalent to a per-source timeout from
        # each one's own start), not how long a slow source's thread actually
        # runs -- a plain Python thread can't be force-cancelled once
        # started, so a source that blows through this keeps running in the
        # background (its result is simply discarded) instead of stalling
        # the whole discovery cycle. This is what makes a slow/throttled
        # source (e.g. the documented `fundamentals` yfinance throttling in
        # config/settings.yaml) fail soft on TIME the same way every source
        # already fails soft on exceptions, rather than blowing out the
        # daily schedule the way it did before this existed.
        self.source_timeout_seconds = float(source_timeout_seconds)
        # A deliberate floor, not an oversight: below this, an overnight gap
        # (dilution, reverse split, delisting news -- all disproportionately
        # a penny-stock phenomenon) can jump clean over a resting stop order,
        # which is the one thing this project's entire risk architecture
        # assumes still works. $5 is the conventional penny-stock line (the
        # SEC's own working definition). Enforced once, centrally, in
        # _size_and_propose() -- covers every source (congress, technical,
        # news, fundamentals, volatility), not just a specific ticker list.
        self.min_price = float(min_price)

    # --- public ---
    def run(self, account: Account, positions: dict, *, exclude: set[str] | None = None) -> DiscoveryReport:
        candidates_by_symbol = self._gather()
        screened = len(candidates_by_symbol)

        held = {s for s, p in positions.items() if p.status != PositionStatus.CLOSED}
        block = {s.upper() for s in (exclude or set())} | held

        scored: list[Candidate] = []
        skipped: list[tuple[str, str]] = []
        for symbol, cand in candidates_by_symbol.items():
            if symbol in block:
                skipped.append((symbol, "already held or pending"))
                continue
            cand.score = self.scorer.score(cand)
            if cand.score < self.min_score:
                skipped.append((symbol, f"score {cand.score:g} < {self.min_score:g}"))
                continue
            scored.append(cand)

        scored.sort(key=lambda c: c.score, reverse=True)

        proposals: list[Proposal] = []
        exposure = _exposure(positions)
        for cand in scored:
            if len(proposals) >= self.top_n:
                break
            proposal = self._size_and_propose(cand, account, exposure, skipped)
            if proposal is not None:
                proposals.append(proposal)

        return DiscoveryReport(candidates=scored, proposals=proposals,
                               screened=screened, skipped=skipped)

    # --- internals ---
    def _gather(self) -> dict[str, Candidate]:
        """Runs every source concurrently (previously sequential, so one slow
        source -- e.g. yfinance throttling under `fundamentals` -- blew out
        the whole cycle's runtime; see `source_timeout_seconds`'s docstring).
        A source that raises, or that exceeds `source_timeout_seconds`, is
        skipped for this cycle exactly like the old sequential try/except did
        -- one bad or slow source must never sink the run, and never blocks
        another source's result from counting.

        Plain daemon threads, not ThreadPoolExecutor: a ThreadPoolExecutor's
        worker threads are non-daemon and `concurrent.futures.thread`
        registers a process-wide atexit hook that joins EVERY thread it ever
        created, from EVERY executor, even ones already shut down with
        wait=False -- confirmed live, a single hung source blocked the whole
        Python process at exit for as long as that source kept running, not
        just this method. This is called from more than a one-shot script
        (the always-on Telegram listener builds a pipeline for `/ideas` too),
        so that isn't just an annoyance, it's a real hang. Daemon threads are
        killed outright at interpreter shutdown instead of joined, which is
        exactly the "abandon it, don't wait" behavior a timed-out source
        needs."""
        by_symbol: dict[str, Candidate] = {}
        if not self.sources:
            return by_symbol

        results: queue.Queue = queue.Queue()

        def _run(source):
            try:
                results.put((source, source.gather(), None))
            except BaseException as exc:  # noqa: BLE001 - one bad source must
                # never sink the run; catches BaseException (not just
                # Exception) so this always posts a result, matching
                # ThreadPoolExecutor's own _WorkItem.run().
                results.put((source, None, exc))

        for source in self.sources:
            threading.Thread(target=_run, args=(source,), daemon=True).start()

        # All sources started together, so one shared deadline IS a per-source
        # timeout measured from each one's own start.
        deadline = time.monotonic() + self.source_timeout_seconds
        pending = len(self.sources)
        while pending > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                source, contributions, exc = results.get(timeout=remaining)
            except queue.Empty:
                break
            pending -= 1
            name = getattr(source, "name", "?")
            if exc is not None:
                log.warning("discovery source %s failed: %s", name, exc)
                continue
            for c in contributions:
                cand = by_symbol.setdefault(c.symbol, Candidate(symbol=c.symbol))
                cand.contributions.append(c)

        if pending > 0:
            log.warning("%d discovery source(s) exceeded %.0fs timeout; skipping this cycle",
                        pending, self.source_timeout_seconds)
        return by_symbol

    def _size_and_propose(
        self, cand: Candidate, account: Account, exposure: ExposureSnapshot, skipped: list
    ) -> Proposal | None:
        strategy, entry, stop, side = self._levels(cand)
        if entry is None or entry <= 0:
            skipped.append((cand.symbol, "no price"))
            return None
        if entry < self.min_price:
            skipped.append((cand.symbol, f"below min price floor (${self.min_price:g})"))
            return None
        # A long's stop sits below entry; a short's sits above -- side-aware,
        # not hardcoded long, so a genuine short technical setup (side
        # already validated by the strategy that generated it, see
        # _levels()) isn't silently dropped here as "invalid".
        invalid_stop = stop <= 0 or (stop >= entry if side == Side.LONG else stop <= entry)
        if invalid_stop:
            skipped.append((cand.symbol, "invalid stop"))
            return None

        intent = Intent(
            symbol=cand.symbol, strategy=strategy, side=side,
            action=Action.BUY if side == Side.LONG else Action.SHORT,
            confidence=min(1.0, cand.score / 100.0),
            entry_price=round(entry, 2), stop_loss=round(stop, 2),
        )
        try:
            intent.validate()
        except ValueError as exc:
            skipped.append((cand.symbol, f"bad intent: {exc}"))
            return None

        acct_state = AccountState(
            equity=account.equity, start_of_day_equity=account.last_equity,
            buying_power=account.buying_power, last_price=entry,
            open_positions=exposure.open_count, gross_exposure_value=exposure.gross_value,
            open_risk_dollars=exposure.open_risk_dollars,
        )
        decision = self.risk.evaluate(intent, acct_state)
        if decision.decision == Decision.VETO or decision.approved_qty <= 0:
            skipped.append((cand.symbol, f"risk: {decision.reason}"))
            return None

        cand.entry_price = round(entry, 2)
        cand.stop_loss = round(stop, 2)
        cand.suggested_qty = float(decision.approved_qty)
        cand.strategy = strategy

        ratchet_params = self.config.risk_limits.get("ratchet_stop", {}).get(strategy, {})
        tech = cand.contribution("technical")
        atr = tech.meta.get("atr") if tech else None
        wire = intent.to_dict()
        wire["entry_price"] = round(entry, 2)
        wire["stop_loss"] = round(stop, 2)
        return Proposal.create(
            intent=wire, approved_qty=decision.approved_qty, strategy=strategy,
            ratchet_params=ratchet_params, atr=atr, expiry_minutes=self.expiry_minutes,
        )

    def _levels(self, cand: Candidate) -> tuple[str, float | None, float, Side]:
        """Resolve (strategy, entry, stop, side). A technical contribution
        brings its own plan -- including which side it's actually trading
        (`shorts_allowed()` is already enforced inside the strategy that
        generated it, so a short contribution here was already validated as
        short-eligible, not a new bypass). Congress-only ideas track
        disclosed BUYs, so they're always long, priced live."""
        tech = cand.contribution("technical")
        if tech is not None:
            entry = tech.meta.get("entry_price")
            stop = tech.meta.get("stop_loss")
            strategy = tech.meta.get("strategy", "discovery")
            side = Side(tech.meta.get("side", Side.LONG.value))
            if entry and stop:
                return strategy, float(entry), float(stop), side
            if entry:
                entry = float(entry)
                pct = self.default_stop_pct / 100.0
                default_stop = entry * (1 - pct) if side == Side.LONG else entry * (1 + pct)
                return strategy, entry, default_stop, side
        # Congress-only (or technical without levels): price it live.
        price = self.price_fn(cand.symbol)
        if price is None or price <= 0:
            return "congress_copy", None, 0.0, Side.LONG
        return ("congress_copy", float(price),
               float(price) * (1 - self.default_stop_pct / 100.0), Side.LONG)


def _exposure(positions: dict) -> ExposureSnapshot:
    open_positions = (p for p in positions.values() if p.status != PositionStatus.CLOSED)
    return compute_exposure(open_positions)
