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

from dataclasses import dataclass, field
from typing import Callable

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
    daytrade_count: int | None = None


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
        by_symbol: dict[str, Candidate] = {}
        for source in self.sources:
            try:
                contributions = source.gather()
            except Exception as exc:  # one bad source must never sink the run
                log.warning("discovery source %s failed: %s", getattr(source, "name", "?"), exc)
                continue
            for c in contributions:
                cand = by_symbol.setdefault(c.symbol, Candidate(symbol=c.symbol))
                cand.contributions.append(c)
        return by_symbol

    def _size_and_propose(
        self, cand: Candidate, account: Account, exposure: ExposureSnapshot, skipped: list
    ) -> Proposal | None:
        strategy, entry, stop = self._levels(cand)
        if entry is None or entry <= 0:
            skipped.append((cand.symbol, "no price"))
            return None
        if stop <= 0 or stop >= entry:
            skipped.append((cand.symbol, "invalid stop"))
            return None

        intent = Intent(
            symbol=cand.symbol, strategy=strategy, side=Side.LONG, action=Action.BUY,
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
            is_intraday=False, day_trade_count=account.daytrade_count,
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

    def _levels(self, cand: Candidate) -> tuple[str, float | None, float]:
        """Resolve (strategy, entry, stop). A technical contribution brings its
        own plan; a congress-only idea uses last price + the default stop."""
        tech = cand.contribution("technical")
        if tech is not None:
            entry = tech.meta.get("entry_price")
            stop = tech.meta.get("stop_loss")
            strategy = tech.meta.get("strategy", "discovery")
            if entry and stop:
                return strategy, float(entry), float(stop)
            if entry:
                return strategy, float(entry), float(entry) * (1 - self.default_stop_pct / 100.0)
        # Congress-only (or technical without levels): price it live.
        price = self.price_fn(cand.symbol)
        if price is None or price <= 0:
            return "congress_copy", None, 0.0
        return "congress_copy", float(price), float(price) * (1 - self.default_stop_pct / 100.0)


def _exposure(positions: dict) -> ExposureSnapshot:
    open_positions = (p for p in positions.values() if p.status != PositionStatus.CLOSED)
    return compute_exposure(open_positions)
