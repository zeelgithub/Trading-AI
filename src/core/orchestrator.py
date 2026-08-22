"""
Orchestrator -- core layer.

Owns one decision cycle and wires the layers together:
  settle fills -> reconcile -> kill-switch -> signal exits -> raise stops
  -> generate/size/execute entries.

Safety posture:
  - Default `execute=False` (SHADOW): computes and logs everything, submits NOTHING.
  - `execute=True` places orders ONLY for risk-approved intents, via the order
    manager / broker. Entries are always protected (stop attached atomically).
  - Live mode (`mode: live`) additionally requires `allow_live=True`, else HALT.
  - Any reconciliation mismatch, kill-switch trip, or unhandled exception HALTS
    the bot (manual reset). Per-symbol errors are isolated and counted; enough
    consecutive errors HALT.

Boundary: drives execution only via approved intents through the risk gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd

from src.common.config import Config, load_config
from src.common.errors import is_transient_error, retry_transient
from src.common.logging import AuditLog, get_logger
from src.common.models import Decision, Side
from src.common.proclock import DEFAULT_LOCK_PATH, BotBusy, bot_lock
from src.core.proposals import Proposal
from src.core.rotation import RotationStateStore
from src.core.state_machine import State, StateMachine
from src.core.state_store import HaltClass, HaltStore, StateStore
from src.execution.broker_alpaca import BrokerInterface
from src.execution.order_manager import (
    OrderManager,
    PositionStatus,
    is_dead_entry,
    realized_pnl,
)
from src.execution.reconciler import Reconciler
from src.notify.telegram import NullNotifier
from src.research.scoreboard import Scoreboard
from src.risk.exposure import ExposureSnapshot, compute_exposure
from src.risk.ratchet_stop import build_ratchet
from src.risk.risk_manager import AccountState, RiskManager
from src.strategy.regime_filter import RegimeFilter
from src.strategy.registry import build_strategies
from src.strategy.sentiment_gate import SentimentGate

FeatureProvider = Callable[[str], pd.DataFrame]


@dataclass
class CycleReport:
    state: str
    executed: bool
    reconciled: bool = True
    halted: bool = False
    halt_reason: str | None = None
    stops_raised: list = field(default_factory=list)   # (symbol, new_stop)
    stops_refreshed: list = field(default_factory=list) # symbols (90-day GTC clock reset)
    opened: list = field(default_factory=list)         # symbols
    proposed: list = field(default_factory=list)        # symbols (propose mode)
    proposals: list = field(default_factory=list)       # Proposal objects (propose mode)
    exited: list = field(default_factory=list)          # (symbol, reason)
    decisions: list = field(default_factory=list)       # (symbol, decision, qty)
    skipped: list = field(default_factory=list)         # (symbol, why)
    stale: list = field(default_factory=list)           # (symbol, age_days)
    data_checked: int = 0                               # symbols whose bars were age-checked

    def summary(self) -> str:
        if self.halted:
            return f"HALTED: {self.halt_reason}"
        return (
            f"state={self.state} executed={self.executed} raised={len(self.stops_raised)} "
            f"opened={len(self.opened)} proposed={len(self.proposed)} "
            f"exited={len(self.exited)} decisions={len(self.decisions)}"
        )


class Orchestrator:
    def __init__(
        self,
        broker: BrokerInterface,
        config: Config | None = None,
        feature_provider: FeatureProvider | None = None,
        execute: bool = False,
        allow_live: bool = False,
        propose: bool = False,
        scorer: Callable[[str], int] | None = None,
        state_store: StateStore | None = None,
        halt_store: HaltStore | None = None,
        audit: AuditLog | None = None,
        notifier=None,
        rotation_store: RotationStateStore | None = None,
        scoreboard: Scoreboard | None = None,
        lock_path=None,
    ) -> None:
        self.config = config or load_config()
        self.broker = broker
        # propose mode decides and emits proposals but places NOTHING (the human
        # approves later from the phone); it is mutually exclusive with execute.
        self.propose = propose
        self.execute = execute and not propose
        self.allow_live = allow_live
        self.notifier = notifier or NullNotifier()
        self.feature_provider = feature_provider or self._default_feature_provider()

        # Never trade on stale data (rule 3): bars older than this are unusable.
        self.max_bar_age_days = int(self.config.get("settings.data.max_bar_age_days", 4))
        # Stops are stop-market, not stop-limit (see docs/SAFEGUARDS.md "Stop
        # order type (gap-down risk)") -- a gap can still fill worse than
        # intended, so auto-close alerts distinctly when it does.
        self.gap_slippage_alert_pct = float(
            self.config.get("settings.alerts.gap_slippage_alert_pct", 2.0))
        # Proactively resets a resting stop's Alpaca-side 90-day GTC
        # aged-order clock before it ever gets close to expiring -- see
        # _refresh_stale_stops and OrderManager.refresh_stale_stop.
        self.stop_refresh_min_days_remaining = int(
            self.config.get("settings.execution.stop_refresh_min_days_remaining", 15))
        # How long to wait for the cross-process state lock (vs a concurrent
        # phone action) before skipping this cycle quietly.
        self.cycle_lock_timeout = float(
            self.config.get("settings.concurrency.cycle_lock_timeout_seconds", 60))
        # Injectable (like state_store/halt_store) so tests never contend with
        # a real bot's lock file; defaults to the real state/.bot.lock.
        self.lock_path = lock_path or DEFAULT_LOCK_PATH
        self.regime = RegimeFilter(self.config)
        self.sentiment = SentimentGate(self.config, scorer=scorer)
        # Config-driven: every registered strategy with a strategies.yaml block.
        # Adding a strategy = new module + @register + config; no edits here.
        self.strategies = build_strategies(self.config)
        self.risk = RiskManager(self.config)
        self.order_manager = OrderManager(broker)
        self.reconciler = Reconciler(broker)
        self.state = StateMachine()
        self.store = state_store or StateStore()
        self.halt_store = halt_store or HaltStore()
        self.audit = audit or AuditLog()
        self.log = get_logger("orchestrator")
        self.positions = self.store.load()
        # Applied strategy rotation (propose-only; default = all enabled, so this
        # is a no-op until a rotation has been approved). Read each cycle so an
        # approval between runs takes effect without a restart.
        self.rotation_store = rotation_store or RotationStateStore()
        # Live per-strategy attribution: realized PnL of real closes feeds the
        # scoreboard, so its "live" columns reflect reality, not just backtests.
        self.scoreboard = scoreboard or Scoreboard()

        # A HALT persisted by a prior (cold) run blocks trading until manual reset.
        persisted_halt = self.halt_store.is_halted()
        if persisted_halt:
            self.state.halt(persisted_halt)

    # --- public API ---
    def run_cycle(self) -> CycleReport:
        report = CycleReport(state=self.state.state.value, executed=self.execute)

        if self.state.is_halted:
            report.halted = True
            report.halt_reason = self.state.halt_reason
            self.log.warning("cycle skipped -- bot is HALTED: %s", self.state.halt_reason)
            return report

        if self.config.is_live and self.execute and not self.allow_live:
            self._halt(report, "live execution requested without allow_live=True",
                       HaltClass.CONFIG)
            return report

        try:
            # The always-on listener (phone actions) and this scheduled cycle
            # both read-modify-write state/positions.json and halt.json; hold
            # the cross-process lock for the whole cycle so neither can clobber
            # the other's save. BotBusy (below) means a phone action is mid-
            # flight -- skip this run quietly rather than race it.
            with bot_lock(timeout=self.cycle_lock_timeout, path=self.lock_path):
                self.state.to(State.RUNNING)
                # Ride out brief network blips; a persistent outage still raises
                # and halts as DISCONNECT below (auto-resumable once verified back).
                account = retry_transient(self.broker.get_account)

                # 0. Settle pending fills so reconcile + sizing act on filled_qty.
                if self.execute:
                    self._settle_pending()

                # 1. Reconcile -- broker is source of truth (pending-aware).
                rec = self.reconciler.reconcile(self.positions)
                # Sync positions closed at broker (stop fired / external close) so
                # they don't trigger a halt-level mismatch on the next check.
                for sym in rec.auto_closed:
                    if sym in self.positions:
                        pos = self.positions[sym]
                        exit_price, slippage_pct = self._resolve_auto_close_exit(pos)
                        if self.execute:
                            self._record_attribution(pos, exit_price)
                        pos.status = PositionStatus.CLOSED
                        self.audit.record("auto_closed", symbol=sym,
                                          detail="position gone from broker (stop fired or external close)",
                                          exit_price=exit_price, slippage_pct=slippage_pct)
                        if slippage_pct is not None and slippage_pct >= self.gap_slippage_alert_pct:
                            self._notify(
                                "incident",
                                f"{sym} stop filled with {slippage_pct:.1f}% gap slippage "
                                f"(intended ${pos.current_stop:,.2f}, filled ${exit_price:,.2f})",
                            )
                        else:
                            self._notify("fill", f"{sym} closed at broker (stop fired or external close)")
                        self.log.info("auto-closed %s: gone from broker", sym)
                if not rec.ok:
                    self.audit.record("reconcile_mismatch", detail=rec.summary())
                    self._halt(report, f"reconcile mismatch: {rec.summary()}",
                               HaltClass.RECONCILE_MISMATCH)
                    return report
                report.reconciled = True

                # 2. Kill switch.
                ks = self.risk.breakers.check_daily_loss(account.last_equity, account.equity)
                if not ks.ok:
                    self.audit.record("kill_switch", detail=ks.reason)
                    if self.execute:
                        self._flatten()
                    self._halt(report, f"kill switch: {ks.reason}", HaltClass.KILL_SWITCH)
                    return report

                market_open = retry_transient(self.broker.is_market_open)

                # 3. Raise resting stops (safe even when closed -- trails the stop).
                self._raise_stops(report)

                # 3b. Proactively refresh any resting stop nearing Alpaca's
                # 90-day GTC aged-order auto-cancel -- raise_stop above only
                # touches the order when the ratchet actually advances, so a
                # flat/losing position could otherwise go naked before the
                # next cycle's reconcile would notice. Real order mutation,
                # so execute-mode only (shadow/propose place nothing).
                if self.execute:
                    self._refresh_stale_stops(report)

                # 4. Signal exits + new entries only place orders when the market
                #    is open (market entries/closes are rejected outside RTH).
                if not market_open:
                    self.log.info("market closed -- stops managed, no entries/exits")
                    report.skipped.append(("*", "market closed"))
                else:
                    # Signal-driven exits (e.g. opposite-EMA break).
                    self._evaluate_exits(report)

                    # Generate / size / (optionally) execute new entries.
                    for symbol in self.config.enabled_symbols():
                        held = self.positions.get(symbol)
                        if held is not None and held.status != PositionStatus.CLOSED:
                            continue
                        try:
                            self._consider_entry(symbol, account, report)
                        except Exception as exc:  # isolate a bad symbol; count it
                            self.audit.record("symbol_error", symbol=symbol, error=str(exc))
                            report.skipped.append((symbol, f"error: {exc}"))
                            if not self.risk.breakers.register_error().ok:
                                raise RuntimeError("too many consecutive errors") from exc

                    # Every symbol we could age-check came back stale -> the feed
                    # itself has stalled. Default-to-halt rather than idle silently.
                    if report.data_checked > 0 and len(report.stale) == report.data_checked:
                        self.audit.record("stale_data", detail=str(report.stale))
                        self._halt(report,
                                   f"stale data on all {report.data_checked} checked symbol(s): "
                                   f"{report.stale}", HaltClass.STALE_DATA)
                        return report

                if self.execute:
                    self.store.save(self.positions)
                self.risk.breakers.reset_errors()
                self.state.to(State.IDLE)
                report.state = self.state.state.value
                # Ops heartbeat: lets the watchdog / MCP ops view answer "did the
                # last cycle actually run, and what did it do?"
                self.audit.record(
                    "cycle_complete",
                    mode=("execute" if self.execute else "propose" if self.propose else "shadow"),
                    summary=report.summary(),
                    opened=len(report.opened), proposed=len(report.proposed),
                    exited=len(report.exited), stops_raised=len(report.stops_raised),
                    stale=len(report.stale),
                )
                self.log.info(report.summary())
                return report

        except BotBusy as exc:
            # A phone action is mid-flight; not a fault worth halting over --
            # the next scheduled invocation will pick this up.
            self.audit.record("cycle_skipped_busy", detail=str(exc))
            self.log.info("cycle skipped -- %s", exc)
            report.reconciled = True
            return report

        except Exception as exc:  # any unhandled error -> halt, never keep trading
            self.audit.record("cycle_exception", error=str(exc))
            if self.execute:
                try:
                    self.store.save(self.positions)
                except Exception:  # pragma: no cover - best-effort persist
                    pass
            # Connectivity faults halt as DISCONNECT so the verified self-healer
            # may resume them; logic errors stay EXCEPTION (manual reset only).
            halt_class = (HaltClass.DISCONNECT if is_transient_error(exc)
                          else HaltClass.EXCEPTION)
            self._halt(report, f"cycle exception: {exc}", halt_class)
            return report

    def reset(self) -> None:
        """Manual reset: clear the persisted halt and return to IDLE."""
        self.halt_store.clear()
        if self.state.is_halted:
            self.state.reset()

    # --- phases ---
    def _settle_pending(self) -> None:
        for symbol, pos in list(self.positions.items()):
            if pos.status != PositionStatus.PENDING_ENTRY:
                continue
            self.order_manager.settle(pos)
            if is_dead_entry(pos):
                self.audit.record("order_rejected", symbol=symbol,
                                  broker_status=pos.last_order_status)
                self._notify("fill", f"{symbol} entry never filled "
                                     f"(broker status: {pos.last_order_status})")
                self.log.warning("entry for %s was %s -- dropping", symbol, pos.last_order_status)
                del self.positions[symbol]

    def _bar_age(self, feats) -> int | None:
        from src.data.ingest import last_bar_age_days  # lazy: keep tests offline-light

        return last_bar_age_days(feats)

    def _is_stale(self, symbol: str, feats, action: str) -> bool:
        """True (and audited) when the newest bar is too old to act on."""
        age = self._bar_age(feats)
        if age is not None and age > self.max_bar_age_days:
            self.audit.record("stale_symbol", symbol=symbol, age_days=age, during=action)
            return True
        return False

    def _evaluate_exits(self, report: CycleReport) -> None:
        for symbol, pos in list(self.positions.items()):
            if pos.status != PositionStatus.OPEN:
                continue
            feats = self.feature_provider(symbol)
            if feats is None or feats.empty:
                continue
            if self._is_stale(symbol, feats, "exit_check"):
                continue  # never exit on an old signal; the resting stop protects
            strat = self.strategies.get(pos.strategy)
            reason = strat.should_exit(symbol, feats, pos.side) if strat else None
            if not reason:
                continue
            report.exited.append((symbol, reason))
            self.audit.record("signal_exit", symbol=symbol, reason=reason)
            if self.execute:
                self.order_manager.close(pos, reason)
                self._record_attribution(pos, float(feats.iloc[-1].close))
                del self.positions[symbol]

    def _raise_stops(self, report: CycleReport) -> None:
        for symbol, pos in self.positions.items():
            if pos.status != PositionStatus.OPEN:
                continue
            feats = self.feature_provider(symbol)
            if feats is None or feats.empty:
                continue
            if self._is_stale(symbol, feats, "raise_stops"):
                continue  # don't feed an old close into the ratchet
            price = float(feats.iloc[-1].close)
            atr = float(feats.iloc[-1].atr) if "atr" in feats.columns else None
            try:
                if self.execute:
                    if self.order_manager.raise_stop(pos, price, atr):
                        report.stops_raised.append((symbol, round(pos.current_stop, 2)))
                        self.audit.record("stop_raised", symbol=symbol, stop=pos.current_stop)
                else:
                    new_stop = pos.ratchet.update(price, atr)
                    more = (new_stop > pos.current_stop) if pos.side == Side.LONG else (new_stop < pos.current_stop)
                    if more:
                        report.stops_raised.append((symbol, round(new_stop, 2)))
            except Exception as exc:
                # One rejected replace must not halt the whole bot: the existing
                # resting stop still protects this position. Isolate + count it;
                # persistent failures trip the consecutive-error breaker.
                self.audit.record("stop_raise_error", symbol=symbol, error=str(exc))
                self.log.warning("stop raise failed for %s (resting stop unchanged): %s",
                                 symbol, exc)
                if not self.risk.breakers.register_error().ok:
                    raise RuntimeError("too many consecutive errors raising stops") from exc

    def _refresh_stale_stops(self, report: CycleReport) -> None:
        now = datetime.now(timezone.utc)
        for symbol, pos in self.positions.items():
            if pos.status != PositionStatus.OPEN:
                continue
            try:
                if self.order_manager.refresh_stale_stop(
                    pos, now, min_days_remaining=self.stop_refresh_min_days_remaining
                ):
                    report.stops_refreshed.append(symbol)
                    self.audit.record("stop_refreshed", symbol=symbol,
                                      detail="reset Alpaca's 90-day GTC aged-order clock")
            except Exception as exc:
                # Isolate: a failed refresh must not halt the cycle -- the
                # existing resting stop still protects the position (just
                # with its aging clock unchanged); same posture as a failed
                # raise_stop, and the reconciler is the eventual backstop.
                self.audit.record("stop_refresh_error", symbol=symbol, error=str(exc))
                self.log.warning("stop refresh failed for %s (resting stop unchanged): %s",
                                 symbol, exc)
                if not self.risk.breakers.register_error().ok:
                    raise RuntimeError("too many consecutive errors refreshing stops") from exc

    def _consider_entry(self, symbol, account, report: CycleReport) -> None:
        feats = self.feature_provider(symbol)
        if feats is None or feats.empty:
            report.skipped.append((symbol, "no data"))
            return
        report.data_checked += 1
        age = self._bar_age(feats)
        if age is not None and age > self.max_bar_age_days:
            report.stale.append((symbol, age))
            report.skipped.append((symbol, f"stale data ({age}d old)"))
            self.audit.record("stale_symbol", symbol=symbol, age_days=age)
            return
        active = self.regime.active_strategy(feats)
        if active is None:
            report.skipped.append((symbol, "no regime"))
            return
        # No rotation.json entry yet => fall back to config/strategies.yaml's
        # own enabled: flag (e.g. a strategy your own validation rejected can
        # ship disabled) rather than always defaulting to on.
        default_enabled = self.config.get(f"strategies.strategies.{active}.enabled", True)
        if not self.rotation_store.load().is_enabled(active, default=default_enabled):
            report.skipped.append((symbol, f"{active} disabled by rotation"))
            return
        intent = self.strategies[active].generate(symbol, feats)
        if intent is None:
            report.skipped.append((symbol, "no setup"))
            return
        gated = self.sentiment.apply(intent)
        if gated is None:
            report.skipped.append((symbol, "sentiment block"))
            return

        exposure = self._exposure()
        last_price = float(feats.iloc[-1].close)
        acct_state = AccountState(
            equity=account.equity,
            start_of_day_equity=account.last_equity,
            buying_power=account.buying_power,
            last_price=last_price,
            open_positions=exposure.open_count,
            gross_exposure_value=exposure.gross_value,
            open_risk_dollars=exposure.open_risk_dollars,
        )
        decision = self.risk.evaluate(gated, acct_state)
        report.decisions.append((symbol, decision.decision.value, decision.approved_qty))
        self.audit.record(
            "risk_decision", symbol=symbol, signal=gated.to_dict(),
            decision=decision.decision.value, qty=decision.approved_qty, reason=decision.reason,
        )

        if decision.decision in (Decision.APPROVE, Decision.RESIZE) and decision.approved_qty > 0:
            if self.propose:
                self._propose(decision, gated, feats, report)
            elif self.execute:
                self._open(decision, gated, feats, report)
            else:
                report.opened.append(symbol)  # would open (shadow)

    def _open(self, decision, intent, feats, report: CycleReport) -> None:
        ratchet_params = self.config.risk_limits.get("ratchet_stop", {}).get(intent.strategy, {})
        entry = intent.entry_price or float(feats.iloc[-1].close)
        atr = float(feats.iloc[-1].atr) if "atr_multiple_initial" in ratchet_params else None
        ratchet = build_ratchet(intent.strategy, ratchet_params, entry, intent.side, atr=atr)
        pos = self.order_manager.open_position(decision, ratchet, tag=date.today().isoformat())
        self.order_manager.settle(pos)  # confirm the fill (market entry)
        self.risk.on_order_submitted()
        self.positions[pos.symbol] = pos
        report.opened.append(pos.symbol)
        self.audit.record("position_opened", symbol=pos.symbol, qty=pos.qty, stop=pos.current_stop)
        self._notify("fill", f"BUY {pos.qty:g} {pos.symbol} @ ~${entry:,.2f}, stop ${pos.current_stop:,.2f}")

    def _propose(self, decision, intent, feats, report: CycleReport) -> None:
        """Propose-and-approve: record a risk-approved entry as a Proposal and
        emit it (no order placed). The human approves later from the phone."""
        ratchet_params = self.config.risk_limits.get("ratchet_stop", {}).get(intent.strategy, {})
        entry = intent.entry_price or float(feats.iloc[-1].close)
        atr = float(feats.iloc[-1].atr) if "atr_multiple_initial" in ratchet_params else None
        ratchet = build_ratchet(intent.strategy, ratchet_params, entry, intent.side, atr=atr)
        wire = intent.to_dict()
        wire["entry_price"] = round(entry, 2)
        wire["stop_loss"] = round(ratchet.stop, 2)
        proposal = Proposal.create(
            intent=wire,
            approved_qty=decision.approved_qty,
            strategy=intent.strategy,
            ratchet_params=ratchet_params,
            atr=atr,
            expiry_minutes=int(self.config.get("settings.approval.proposal_expiry_minutes", 1080)),
        )
        report.proposals.append(proposal)
        report.proposed.append(proposal.symbol)
        self.audit.record("proposal_created", symbol=proposal.symbol,
                          qty=decision.approved_qty, stop=round(ratchet.stop, 2))

    # --- helpers ---
    def _notify(self, event: str, detail: str = "") -> None:
        """Best-effort alert to the phone. Never let a notify failure affect a
        trading cycle."""
        try:
            self.notifier.alert(event, detail)
        except Exception as exc:  # pragma: no cover - defensive
            self.log.warning("notify failed (%s): %s", event, exc)

    def _resolve_auto_close_exit(self, pos) -> tuple[float, float | None]:
        """Best-effort: fetch the stop leg's REAL fill price so attribution and
        gap-slippage detection use what actually happened, not the intended
        stop level. Falls back to the stop level (and no slippage reading) if
        the order lookup fails or the broker never reports a fill price --
        e.g. a manual close cancels the stop leg instead of filling it.

        Returns (exit_price, slippage_pct): slippage_pct is signed, positive
        meaning worse than intended (below the stop for a long, above it for
        a short), None when there's nothing real to compare against.
        """
        fill = None
        try:
            fill = self.broker.get_order(pos.stop_order_id).filled_avg_price
        except Exception as exc:  # pragma: no cover - defensive, never blocks a cycle
            self.log.warning("could not fetch stop fill price for %s: %s", pos.symbol, exc)
        if fill is None or not pos.current_stop:
            return pos.current_stop, None
        adverse = (pos.current_stop - fill) if pos.side == Side.LONG else (fill - pos.current_stop)
        return fill, round(adverse / pos.current_stop * 100.0, 2)

    def _record_attribution(self, pos, exit_price: float) -> None:
        """Record a real close's realized PnL against its strategy on the
        scoreboard. Best-effort: attribution must NEVER affect a trading cycle.
        `exit_price` is the stop leg's real fill price when the broker reports
        one (see `_resolve_auto_close_exit`); a signal exit still passes the
        latest close, and a stale/unreported fill falls back to the stop
        level -- good enough for relative strategy ranking even then."""
        try:
            entry = float(getattr(pos.ratchet, "entry", 0.0))
            qty = pos.filled_qty or pos.qty
            if not entry or not qty:
                return
            self.scoreboard.record_live_trade(pos.strategy, round(realized_pnl(pos.side, entry, qty, exit_price), 2))
        except Exception as exc:  # pragma: no cover - defensive
            self.log.warning("attribution record failed for %s: %s", pos.symbol, exc)

    def _halt(self, report: CycleReport, reason: str,
              halt_class: str = HaltClass.UNKNOWN) -> None:
        self.state.halt(reason)
        self.halt_store.set(reason, halt_class)  # persist across cold runs -> no self-resume
        report.halted = True
        report.halt_reason = reason
        report.reconciled = "reconcile" not in reason
        report.state = self.state.state.value
        self.log.error("HALT: %s", reason)
        self._notify("halt", reason)

    def _flatten(self) -> None:
        for symbol, pos in list(self.positions.items()):
            if pos.status in (PositionStatus.OPEN, PositionStatus.PENDING_EXIT):
                try:
                    self.order_manager.close(pos, "kill_switch")
                except Exception as exc:  # best-effort; resting stops still protect
                    self.audit.record("flatten_error", symbol=symbol, error=str(exc))
        try:
            open_orders = retry_transient(self.broker.list_open_orders)
        except Exception:  # pragma: no cover
            open_orders = []
        for order in open_orders:
            try:
                # cancel is idempotent (cancelling twice is a no-op at the
                # broker), so retrying it during a kill-switch flatten is safe.
                retry_transient(lambda oid=order.id: self.broker.cancel_order(oid))
            except Exception:  # pragma: no cover
                pass

    def _exposure(self) -> ExposureSnapshot:
        open_positions = (p for p in self.positions.values() if p.status != PositionStatus.CLOSED)
        return compute_exposure(open_positions)

    def _default_feature_provider(self) -> FeatureProvider:
        from src.data.ingest import live_feature_provider

        return live_feature_provider(self.config)
