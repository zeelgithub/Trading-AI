"""
Trade service -- core layer.

The single gated entry point shared by the CLI (manual_order) and the phone
(Telegram listener). It coordinates risk -> execution exactly as the
orchestrator does -- it holds NO trading credentials and places orders only
through OrderManager / the broker, never directly.

Every order-placing path here:
  1. refuses if the bot is HALTED (default-to-halt),
  2. runs a fresh intent through the risk gate (veto / resize is a hard ceiling),
  3. opens only protected entries (stop attached atomically), and
  4. records the position in StateStore so the autonomous cycle then manages it.

Boundary: places orders via execution YES (through OrderManager), holds trading
credentials NO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from src.common.config import Config, load_config
from src.common.logging import AuditLog, get_logger
from src.common.models import Action, Decision, Intent, RiskDecision, Side
from src.core.proposals import Proposal
from src.core.state_store import HaltClass, HaltStore, StateStore
from src.execution.broker_alpaca import BrokerInterface
from src.execution.order_manager import OrderManager, PositionStatus
from src.risk.ratchet_stop import PercentRatchet, build_ratchet
from src.risk.risk_manager import AccountState, RiskManager

PriceFn = Callable[[str], float]


@dataclass
class TradeResult:
    ok: bool
    status: str           # placed | vetoed | halted | exists | error | closed | none | ok
    message: str
    symbol: str | None = None
    qty: float | None = None
    stop: float | None = None


@dataclass
class StatusReport:
    equity: float
    last_equity: float
    buying_power: float
    halted: str | None
    market_open: bool
    positions: list[dict]   # symbol, side, qty, avg_entry, stop, strategy

    @property
    def day_pl(self) -> float:
        return self.equity - self.last_equity

    def text(self) -> str:
        lines = ["📊 STATUS"]
        if self.halted:
            lines.append(f"🛑 HALTED: {self.halted}")
        lines.append(f"equity ${self.equity:,.2f}  (day {self.day_pl:+,.2f})")
        lines.append(f"buying power ${self.buying_power:,.2f}")
        lines.append("market: " + ("OPEN" if self.market_open else "closed"))
        if not self.positions:
            lines.append("\nno open positions")
        else:
            lines.append("\npositions:")
            for p in self.positions:
                stop = f"${p['stop']:,.2f}" if p.get("stop") is not None else "n/a"
                lines.append(
                    f"  {p['symbol']} {p['side']} {p['qty']:g} @ ${p['avg_entry']:,.2f}"
                    f"  stop {stop}  [{p['strategy']}]"
                )
        return "\n".join(lines)


def _gross_and_count(positions: dict) -> tuple[float, int]:
    gross = sum(
        (p.filled_qty or p.qty) * getattr(p.ratchet, "entry", 0.0)
        for p in positions.values()
        if p.status != PositionStatus.CLOSED
    )
    count = sum(1 for p in positions.values() if p.status != PositionStatus.CLOSED)
    return gross, count


class TradeService:
    def __init__(
        self,
        broker: BrokerInterface,
        config: Config | None = None,
        state_store: StateStore | None = None,
        halt_store: HaltStore | None = None,
        price_fn: PriceFn | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.broker = broker
        self.config = config or load_config()
        self.store = state_store or StateStore()
        self.halt_store = halt_store or HaltStore()
        self.risk = RiskManager(self.config)
        self.orders = OrderManager(broker)
        self._price_fn = price_fn
        self.audit = audit or AuditLog()
        self.log = get_logger("trade_service")

    # --- read / control ---
    def status(self) -> StatusReport:
        account = self.broker.get_account()
        positions = self.store.load()
        rows = []
        for sym, p in positions.items():
            if p.status == PositionStatus.CLOSED:
                continue
            rows.append({
                "symbol": sym,
                "side": p.side.value,
                "qty": p.filled_qty or p.qty,
                "avg_entry": getattr(p.ratchet, "entry", 0.0),
                "stop": p.current_stop,
                "strategy": p.strategy,
            })
        return StatusReport(
            equity=account.equity,
            last_equity=account.last_equity,
            buying_power=account.buying_power,
            halted=self.halt_store.is_halted(),
            market_open=self.broker.is_market_open(),
            positions=rows,
        )

    def halt(self, reason: str = "manual halt") -> TradeResult:
        self.halt_store.set(reason, HaltClass.MANUAL)
        self.audit.record("manual_halt", reason=reason)
        return TradeResult(True, "ok", f"HALTED: {reason}")

    def reset(self) -> TradeResult:
        self.halt_store.clear()
        self.audit.record("manual_reset")
        return TradeResult(True, "ok", "Halt cleared -- bot reset to IDLE.")

    def close(self, symbol: str) -> TradeResult:
        symbol = symbol.upper()
        positions = self.store.load()
        pos = positions.get(symbol)
        if pos is None or pos.status == PositionStatus.CLOSED:
            return TradeResult(False, "none", f"No managed {symbol} position to close.", symbol)
        try:
            self.orders.close(pos, "manual_close")
        except Exception as exc:
            return TradeResult(False, "error", f"Close failed at broker: {exc}", symbol)
        del positions[symbol]
        self.store.save(positions)
        self.audit.record("manual_close", symbol=symbol)
        return TradeResult(True, "closed", f"Closed {symbol}.", symbol)

    def flatten(self) -> TradeResult:
        positions = self.store.load()
        live = [s for s, p in positions.items() if p.status != PositionStatus.CLOSED]
        if not live:
            return TradeResult(True, "none", "No open positions to flatten.")
        errors = []
        for symbol in list(live):
            try:
                self.orders.close(positions[symbol], "manual_flatten")
                del positions[symbol]
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        self.store.save(positions)
        self.audit.record("manual_flatten", closed=len(live) - len(errors), errors=errors)
        if errors:
            return TradeResult(False, "error", "Flatten partial: " + "; ".join(errors))
        return TradeResult(True, "closed", f"Flattened {len(live)} position(s).")

    # --- order paths (gated) ---
    def place_manual(self, symbol: str, qty: int, stop_pct: float = 10.0) -> TradeResult:
        symbol = symbol.upper()
        halted = self.halt_store.is_halted()
        if halted:
            return TradeResult(False, "halted", f"Bot is HALTED ({halted}). /reset first.", symbol)

        positions = self.store.load()
        if symbol in positions and positions[symbol].status != PositionStatus.CLOSED:
            return TradeResult(False, "exists", f"Already tracking {symbol} "
                               f"({positions[symbol].status.value}).", symbol)

        try:
            price = self._last_price(symbol)
        except Exception as exc:
            return TradeResult(False, "error", str(exc), symbol)
        stop = round(price * (1 - stop_pct / 100.0), 2)
        intent = Intent(
            symbol=symbol, strategy="manual", side=Side.LONG, action=Action.BUY,
            confidence=1.0, entry_price=round(price, 2), stop_loss=stop,
            suggested_qty=float(qty),
        )
        intent.validate()
        ratchet = PercentRatchet(entry=price, side=Side.LONG, initial_stop_pct=stop_pct)
        return self._gate_and_open(intent, positions, price, requested_qty=qty,
                                   ratchet=ratchet, tag=f"manual-{date.today().isoformat()}")

    def execute_approved(self, proposal: Proposal) -> TradeResult:
        """Place a previously-proposed trade -- only after re-running the risk
        gate with fresh account state."""
        symbol = proposal.symbol
        halted = self.halt_store.is_halted()
        if halted:
            return TradeResult(False, "halted", f"Bot is HALTED ({halted}). /reset first.", symbol)

        positions = self.store.load()
        if symbol in positions and positions[symbol].status != PositionStatus.CLOSED:
            return TradeResult(False, "exists", f"Already tracking {symbol}.", symbol)

        try:
            intent = Intent.from_dict(proposal.intent)
        except ValueError as exc:
            return TradeResult(False, "error", f"Bad proposal: {exc}", symbol)

        try:
            entry = self._last_price(symbol)  # always current price, not stale proposal price
        except Exception as exc:
            return TradeResult(False, "error", str(exc), symbol)
        ratchet = self._rebuild_ratchet(intent, proposal, entry)
        return self._gate_and_open(
            intent, positions, entry,
            requested_qty=int(proposal.approved_qty),
            ratchet=ratchet, tag=f"approved-{proposal.id}",
        )

    # --- internals ---
    def _gate_and_open(self, intent, positions, price, requested_qty, ratchet, tag) -> TradeResult:
        account = self.broker.get_account()
        gross, open_count = _gross_and_count(positions)
        acct_state = AccountState(
            equity=account.equity, start_of_day_equity=account.last_equity,
            buying_power=account.buying_power, last_price=price,
            open_positions=open_count, gross_exposure_value=gross,
            is_intraday=False, day_trade_count=account.daytrade_count,
        )
        decision = self.risk.evaluate(intent, acct_state)
        self.audit.record("trade_service_risk", symbol=intent.symbol,
                          decision=decision.decision.value, qty=decision.approved_qty,
                          reason=decision.reason)
        if decision.decision == Decision.VETO:
            return TradeResult(False, "vetoed", f"Vetoed by risk gate: {decision.reason}",
                               intent.symbol)

        final_qty = min(int(requested_qty), int(decision.approved_qty))
        if final_qty <= 0:
            return TradeResult(False, "vetoed", f"Sized to zero ({decision.reason}).",
                               intent.symbol)

        note = ""
        if final_qty < int(requested_qty):
            note = f" (trimmed from {int(requested_qty)} by risk caps)"

        dec = RiskDecision(
            intent=intent,
            decision=Decision.APPROVE if final_qty == int(requested_qty) else Decision.RESIZE,
            approved_qty=float(final_qty),
        )
        try:
            pos = self.orders.open_position(dec, ratchet, tag=tag)
            self.orders.settle(pos)
        except Exception as exc:
            return TradeResult(False, "error", f"Order failed at broker: {exc}", intent.symbol)

        positions[intent.symbol] = pos
        self.store.save(positions)
        self.risk.on_order_submitted()
        self.audit.record("position_opened", symbol=intent.symbol, qty=pos.qty,
                          stop=pos.current_stop, via="trade_service")
        closed = "" if self.broker.is_market_open() else " [market closed -- queued for next open]"
        return TradeResult(
            True, "placed",
            f"Placed (paper): BUY {final_qty} {intent.symbol} @ ~${price:,.2f}, "
            f"stop ${pos.current_stop:,.2f}{note}{closed}",
            intent.symbol, qty=float(final_qty), stop=pos.current_stop,
        )

    def _rebuild_ratchet(self, intent: Intent, proposal: Proposal, entry: float):
        if proposal.ratchet_params:
            try:
                return build_ratchet(intent.strategy, proposal.ratchet_params,
                                     entry, intent.side, atr=proposal.atr)
            except ValueError:
                pass  # fall through to a stop derived from the proposal's own level
        stop = intent.stop_loss if intent.stop_loss is not None else round(entry * 0.9, 2)
        if intent.side == Side.LONG:
            pct = max((entry - stop) / entry * 100.0, 0.01)
        else:
            pct = max((stop - entry) / entry * 100.0, 0.01)
        return PercentRatchet(entry=entry, side=intent.side, initial_stop_pct=pct)

    def _last_price(self, symbol: str) -> float:
        if self._price_fn is not None:
            return float(self._price_fn(symbol))
        from src.data.providers.alpaca_data import AlpacaData

        # 30d window so a long weekend / holiday gap still yields a recent bar.
        bars = AlpacaData().get_daily_bars(symbol, lookback_days=30)
        if bars.empty:
            raise ValueError(f"no recent price data for {symbol} (check the symbol / data feed)")
        return float(bars.iloc[-1].close)
