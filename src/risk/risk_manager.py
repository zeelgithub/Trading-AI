"""
Risk Gatekeeper -- risk layer.

The single checkpoint between a strategy Intent and the Execution layer. It runs
every intent through the deterministic guards (kill switch, exposure, sizing,
PDT, rate limits) and returns a RiskDecision: APPROVE (with the final quantity),
RESIZE (approved but smaller than suggested), or VETO.

Pure logic, no network I/O. The ONLY path to execution.

Boundary: places orders NO (veto power, not order power).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.common.config import Config, load_config
from src.common.models import Decision, Intent, RiskDecision, Side
from src.risk import position_sizer
from src.risk.circuit_breakers import CircuitBreakers
from src.risk.pdt_tracker import PdtTracker
from src.risk.ratchet_stop import build_ratchet


@dataclass
class AccountState:
    equity: float
    start_of_day_equity: float
    buying_power: float
    last_price: float
    open_positions: int = 0
    gross_exposure_value: float = 0.0
    is_intraday: bool = False
    as_of: date = field(default_factory=date.today)
    day_trade_count: int | None = None  # broker-reported, for reconciliation


def _veto(intent: Intent, reason: str) -> RiskDecision:
    return RiskDecision(intent=intent, decision=Decision.VETO, approved_qty=0.0, reason=reason)


class RiskManager:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        rl = self.config.risk_limits
        cb = rl.get("circuit_breakers", {})
        self.breakers = CircuitBreakers(
            max_daily_loss_pct=float(rl.get("account", {}).get("max_daily_loss_pct", 4.0)),
            max_orders_per_minute=int(cb.get("max_orders_per_minute", 10)),
            max_orders_per_day=int(cb.get("max_orders_per_day", 50)),
            max_consecutive_errors=int(cb.get("max_consecutive_errors", 5)),
            fat_finger_price_band_pct=float(cb.get("fat_finger_price_band_pct", 20.0)),
        )
        pdt = rl.get("pdt", {})
        self.pdt = PdtTracker(
            min_equity_for_daytrading=float(pdt.get("min_equity_for_daytrading", 25000.0)),
            max_day_trades_rolling_5d=int(pdt.get("max_day_trades_rolling_5d", 3)),
        )

    # --- limits helpers ---
    def _ratchet_params(self, strategy: str) -> dict:
        return self.config.risk_limits.get("ratchet_stop", {}).get(strategy, {})

    def _initial_stop(self, intent: Intent, entry: float) -> float | None:
        if intent.stop_loss is not None:
            return intent.stop_loss
        params = self._ratchet_params(intent.strategy)
        if not params:
            return None
        atr = intent.meta.get("atr")
        try:
            ratchet = build_ratchet(intent.strategy, params, entry, intent.side, atr=atr)
        except ValueError:
            return None
        return ratchet.stop

    # --- the gate ---
    def evaluate(self, intent: Intent, account: AccountState) -> RiskDecision:
        rl = self.config.risk_limits
        acct = rl.get("account", {})
        pos = rl.get("position", {})
        alloc = rl.get("allocation", {})

        # 1. Daily-loss kill switch -- vetoes everything until manual reset.
        ks = self.breakers.check_daily_loss(account.start_of_day_equity, account.equity)
        if not ks.ok:
            return _veto(intent, f"KILL SWITCH: {ks.reason}")

        # 2. Max open positions.
        if account.open_positions >= int(acct.get("max_open_positions", 10)):
            return _veto(intent, "max_open_positions reached")

        # 3. Rate limits.
        rate = self.breakers.check_rate_limits()
        if not rate.ok:
            return _veto(intent, rate.reason)

        # 4. PDT (only relevant for intraday round-trips).
        if account.is_intraday:
            if account.day_trade_count is not None:
                self.pdt.sync_broker_count(account.day_trade_count, account.as_of)
            if not self.pdt.can_day_trade(account.equity, account.as_of):
                return _veto(intent, "PDT limit: day-trade cap reached for the window")

        # 5. Need an entry and a stop to size the trade. Prefer the strategy's
        #    own entry_price; fall back to the latest market price.
        entry = intent.entry_price if intent.entry_price else account.last_price
        if entry <= 0:
            return _veto(intent, "no valid last price to size against")
        stop = self._initial_stop(intent, entry)
        if stop is None:
            return _veto(intent, f"no stop available for strategy {intent.strategy!r}")

        # 6. Fat-finger band (when the intent carries its own price).
        order_price = intent.meta.get("limit_price", entry)
        ff = self.breakers.check_fat_finger(order_price, entry)
        if not ff.ok:
            return _veto(intent, ff.reason)

        # 7. Risk-based sizing. Equal-risk budget uses the per-strategy slice.
        per_trade_risk = float(
            alloc.get("per_strategy_risk_pct", pos.get("per_trade_risk_pct", 1.0))
        )
        max_position_pct = float(pos.get("max_position_pct", 10.0))
        sizing = position_sizer.size_position(
            equity=account.equity,
            entry_price=entry,
            stop_price=stop,
            per_trade_risk_pct=per_trade_risk,
            max_position_pct=max_position_pct,
            buying_power=account.buying_power,
        )
        qty = sizing.qty
        if qty <= 0:
            return _veto(intent, f"sized to zero (capped by {sizing.capped_by})")

        # 8. Gross-exposure cap -- shrink to fit, else veto.
        max_gross_pct = float(acct.get("max_gross_exposure_pct", 175.0))
        max_gross_value = account.equity * (max_gross_pct / 100.0)
        room = max_gross_value - account.gross_exposure_value
        if room <= 0:
            return _veto(intent, "gross exposure limit reached")
        max_qty_by_gross = int(room // entry)
        resized = False
        if qty > max_qty_by_gross:
            qty = max_qty_by_gross
            resized = True
        if qty <= 0:
            return _veto(intent, "gross exposure leaves no room for one share")

        # 9. RESIZE if we cut below the strategy's suggestion; else APPROVE.
        if intent.suggested_qty is not None and qty < intent.suggested_qty:
            resized = True
        decision = Decision.RESIZE if resized else Decision.APPROVE
        return RiskDecision(
            intent=intent,
            decision=decision,
            approved_qty=float(qty),
            reason=f"sized by {sizing.capped_by}; stop={stop:.2f}",
        )

    # --- post-execution registration (called by orchestrator on a real fill) ---
    def on_order_submitted(self) -> None:
        self.breakers.register_order()

    def on_day_trade(self, trade_date: date) -> None:
        self.pdt.register_day_trade(trade_date)
