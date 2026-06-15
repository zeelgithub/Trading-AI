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

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd

from src.common.config import Config, load_config
from src.common.logging import AuditLog, get_logger
from src.common.models import Decision, Side
from src.core.state_machine import State, StateMachine
from src.core.state_store import HaltStore, StateStore
from src.execution.broker_alpaca import BrokerInterface
from src.execution.order_manager import OrderManager, PositionStatus
from src.execution.reconciler import Reconciler
from src.risk.ratchet_stop import build_ratchet
from src.risk.risk_manager import AccountState, RiskManager
from src.strategy.breakout import Breakout
from src.strategy.mean_reversion import MeanReversion
from src.strategy.regime_filter import RegimeFilter
from src.strategy.sentiment_gate import SentimentGate
from src.strategy.trend_following import TrendFollowing

FeatureProvider = Callable[[str], pd.DataFrame]


@dataclass
class CycleReport:
    state: str
    executed: bool
    reconciled: bool = True
    halted: bool = False
    halt_reason: str | None = None
    stops_raised: list = field(default_factory=list)   # (symbol, new_stop)
    opened: list = field(default_factory=list)         # symbols
    exited: list = field(default_factory=list)          # (symbol, reason)
    decisions: list = field(default_factory=list)       # (symbol, decision, qty)
    skipped: list = field(default_factory=list)         # (symbol, why)

    def summary(self) -> str:
        if self.halted:
            return f"HALTED: {self.halt_reason}"
        return (
            f"state={self.state} executed={self.executed} raised={len(self.stops_raised)} "
            f"opened={len(self.opened)} exited={len(self.exited)} decisions={len(self.decisions)}"
        )


class Orchestrator:
    def __init__(
        self,
        broker: BrokerInterface,
        config: Config | None = None,
        feature_provider: FeatureProvider | None = None,
        execute: bool = False,
        allow_live: bool = False,
        scorer: Callable[[str], int] | None = None,
        state_store: StateStore | None = None,
        halt_store: HaltStore | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.config = config or load_config()
        self.broker = broker
        self.execute = execute
        self.allow_live = allow_live
        self.feature_provider = feature_provider or self._default_feature_provider()

        self.regime = RegimeFilter(self.config)
        self.sentiment = SentimentGate(self.config, scorer=scorer)
        self.strategies = {
            "trend_following": TrendFollowing(self.config),
            "mean_reversion": MeanReversion(self.config),
            "breakout": Breakout(self.config),
        }
        self.risk = RiskManager(self.config)
        self.order_manager = OrderManager(broker)
        self.reconciler = Reconciler(broker)
        self.state = StateMachine()
        self.store = state_store or StateStore()
        self.halt_store = halt_store or HaltStore()
        self.audit = audit or AuditLog()
        self.log = get_logger("orchestrator")
        self.positions = self.store.load()

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
            self._halt(report, "live execution requested without allow_live=True")
            return report

        try:
            self.state.to(State.RUNNING)
            account = self.broker.get_account()

            # 0. Settle pending fills so reconcile + sizing act on filled_qty.
            if self.execute:
                self._settle_pending()

            # 1. Reconcile -- broker is source of truth (pending-aware).
            rec = self.reconciler.reconcile(self.positions)
            if not rec.ok:
                self.audit.record("reconcile_mismatch", detail=rec.summary())
                self._halt(report, f"reconcile mismatch: {rec.summary()}")
                return report
            report.reconciled = True

            # 2. Kill switch.
            ks = self.risk.breakers.check_daily_loss(account.last_equity, account.equity)
            if not ks.ok:
                self.audit.record("kill_switch", detail=ks.reason)
                if self.execute:
                    self._flatten()
                self._halt(report, f"kill switch: {ks.reason}")
                return report

            market_open = self.broker.is_market_open()

            # 3. Raise resting stops (safe even when closed -- trails the stop).
            self._raise_stops(report)

            # 4. Signal exits + new entries only place orders when the market is
            #    open (market entries/closes are rejected outside RTH).
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

            if self.execute:
                self.store.save(self.positions)
            self.risk.breakers.reset_errors()
            self.state.to(State.IDLE)
            report.state = self.state.state.value
            self.log.info(report.summary())
            return report

        except Exception as exc:  # any unhandled error -> halt, never keep trading
            self.audit.record("cycle_exception", error=str(exc))
            if self.execute:
                try:
                    self.store.save(self.positions)
                except Exception:  # pragma: no cover - best-effort persist
                    pass
            self._halt(report, f"cycle exception: {exc}")
            return report

    def reset(self) -> None:
        """Manual reset: clear the persisted halt and return to IDLE."""
        self.halt_store.clear()
        if self.state.is_halted:
            self.state.reset()

    # --- phases ---
    def _settle_pending(self) -> None:
        for pos in self.positions.values():
            if pos.status == PositionStatus.PENDING_ENTRY:
                self.order_manager.settle(pos)

    def _evaluate_exits(self, report: CycleReport) -> None:
        for symbol, pos in list(self.positions.items()):
            if pos.status != PositionStatus.OPEN:
                continue
            feats = self.feature_provider(symbol)
            if feats is None or feats.empty:
                continue
            strat = self.strategies.get(pos.strategy)
            reason = strat.should_exit(symbol, feats, pos.side) if strat else None
            if not reason:
                continue
            report.exited.append((symbol, reason))
            self.audit.record("signal_exit", symbol=symbol, reason=reason)
            if self.execute:
                self.order_manager.close(pos, reason)
                del self.positions[symbol]

    def _raise_stops(self, report: CycleReport) -> None:
        for symbol, pos in self.positions.items():
            if pos.status != PositionStatus.OPEN:
                continue
            feats = self.feature_provider(symbol)
            if feats is None or feats.empty:
                continue
            price = float(feats.iloc[-1].close)
            atr = float(feats.iloc[-1].atr) if "atr" in feats.columns else None
            if self.execute:
                if self.order_manager.raise_stop(pos, price, atr):
                    report.stops_raised.append((symbol, round(pos.current_stop, 2)))
                    self.audit.record("stop_raised", symbol=symbol, stop=pos.current_stop)
            else:
                new_stop = pos.ratchet.update(price, atr)
                more = (new_stop > pos.current_stop) if pos.side == Side.LONG else (new_stop < pos.current_stop)
                if more:
                    report.stops_raised.append((symbol, round(new_stop, 2)))

    def _consider_entry(self, symbol, account, report: CycleReport) -> None:
        feats = self.feature_provider(symbol)
        if feats is None or feats.empty:
            report.skipped.append((symbol, "no data"))
            return
        active = self.regime.active_strategy(feats)
        if active is None:
            report.skipped.append((symbol, "no regime"))
            return
        intent = self.strategies[active].generate(symbol, feats)
        if intent is None:
            report.skipped.append((symbol, "no setup"))
            return
        gated = self.sentiment.apply(intent)
        if gated is None:
            report.skipped.append((symbol, "sentiment block"))
            return

        gross, open_count = self._exposure()
        last_price = float(feats.iloc[-1].close)
        acct_state = AccountState(
            equity=account.equity,
            start_of_day_equity=account.last_equity,
            buying_power=account.buying_power,
            last_price=last_price,
            open_positions=open_count,
            gross_exposure_value=gross,
            is_intraday=False,
            day_trade_count=account.daytrade_count,
        )
        decision = self.risk.evaluate(gated, acct_state)
        report.decisions.append((symbol, decision.decision.value, decision.approved_qty))
        self.audit.record(
            "risk_decision", symbol=symbol, signal=gated.to_dict(),
            decision=decision.decision.value, qty=decision.approved_qty, reason=decision.reason,
        )

        if decision.decision in (Decision.APPROVE, Decision.RESIZE) and decision.approved_qty > 0:
            if self.execute:
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

    # --- helpers ---
    def _halt(self, report: CycleReport, reason: str) -> None:
        self.state.halt(reason)
        self.halt_store.set(reason)  # persist across cold runs -> no self-resume
        report.halted = True
        report.halt_reason = reason
        report.reconciled = "reconcile" not in reason
        report.state = self.state.state.value
        self.log.error("HALT: %s", reason)

    def _flatten(self) -> None:
        for symbol, pos in list(self.positions.items()):
            if pos.status in (PositionStatus.OPEN, PositionStatus.PENDING_EXIT):
                try:
                    self.order_manager.close(pos, "kill_switch")
                except Exception as exc:  # best-effort; resting stops still protect
                    self.audit.record("flatten_error", symbol=symbol, error=str(exc))
        for order in self.broker.list_open_orders():
            try:
                self.broker.cancel_order(order.id)
            except Exception:  # pragma: no cover
                pass

    def _exposure(self):
        gross = 0.0
        count = 0
        for pos in self.positions.values():
            if pos.status == PositionStatus.CLOSED:
                continue
            qty = pos.filled_qty or pos.qty
            entry = getattr(pos.ratchet, "entry", 0.0)
            gross += qty * entry
            count += 1
        return gross, count

    def _default_feature_provider(self) -> FeatureProvider:
        from src.data import store
        from src.data.features import build_features
        from src.data.ingest import ingest_symbol

        config = self.config
        lookback = int(config.get("settings.data.lookback_days", 400))
        conn = store.connect()

        def provider(symbol: str) -> pd.DataFrame:
            ingest_symbol(conn, symbol, lookback_days=lookback)
            return build_features(store.load_bars(conn, symbol), config)

        return provider
