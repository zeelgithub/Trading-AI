"""
Backtester -- research layer (offline).

Event-driven, point-in-time-correct portfolio backtest. It reuses the SAME
components the live bot uses -- regime filter, strategies, sentiment gate, risk
gatekeeper, ratchet stop -- so the backtest measures the real trading logic, not
a re-implementation.

No look-ahead: a signal computed from bars up to and including day t is filled
at day t+1's OPEN. Exits (ratchet stop / take-profit) are checked against each
day's high/low. Costs: per-share commission + slippage in bps on every fill.

Never touches the live broker.

Boundary: places live orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.common.config import Config, load_config
from src.common.models import Side
from src.research.metrics import compute_metrics
from src.risk.exposure import compute_exposure
from src.risk.ratchet_stop import build_ratchet
from src.risk.risk_manager import AccountState, RiskManager
from src.strategy.regime_filter import RegimeFilter
from src.strategy.registry import build_strategies
from src.strategy.sentiment_gate import SentimentGate


@dataclass
class Trade:
    symbol: str
    side: str
    strategy: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    return_pct: float
    reason: str


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list
    metrics: dict


class _Position:
    __slots__ = ("symbol", "side", "qty", "entry_price", "entry_date", "ratchet",
                 "take_profit", "strategy")

    def __init__(self, symbol, side, qty, entry_price, entry_date, ratchet, take_profit, strategy):
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.ratchet = ratchet
        self.take_profit = take_profit
        self.strategy = strategy


class Backtester:
    def __init__(
        self,
        config: Config | None = None,
        initial_equity: float = 100_000.0,
        commission_per_share: float | None = None,
        slippage_bps: float | None = None,
    ) -> None:
        self.config = config or load_config()
        self.initial_equity = initial_equity
        # None (the default) reads settings.backtest.* so cost assumptions live
        # in one reviewable place instead of a silent code default -- explicit
        # args still override, for one-off cost-sensitivity experiments.
        if commission_per_share is None:
            commission_per_share = float(
                self.config.get("settings.backtest.commission_per_share", 0.0))
        if slippage_bps is None:
            slippage_bps = float(self.config.get("settings.backtest.slippage_bps", 5.0))
        self.commission = commission_per_share
        self.slip = slippage_bps / 10_000.0

        self.regime = RegimeFilter(self.config)
        self.sentiment = SentimentGate(self.config)
        self.strategies = build_strategies(self.config)
        self.risk = RiskManager(self.config)
        self.max_open = int(self.config.risk_limits.get("account", {}).get("max_open_positions", 10))

    def run(self, features_by_symbol: dict[str, pd.DataFrame]) -> BacktestResult:
        # Unified, sorted date index across all symbols.
        all_dates = sorted(set().union(*[df.index for df in features_by_symbol.values()]))

        positions: dict[str, _Position] = {}
        pending: dict[str, dict] = {}  # symbol -> entry order to fill next open
        realized = 0.0
        trades: list[Trade] = []
        equity_points: list[tuple[object, float]] = []

        for today in all_dates:
            # 1. Fill yesterday's pending entries at today's open.
            for symbol, order in list(pending.items()):
                df = features_by_symbol[symbol]
                if today not in df.index:
                    continue
                bar = df.loc[today]
                positions[symbol] = self._fill_entry(symbol, order, bar, today)
                del pending[symbol]

            # 2. Manage open positions: exits first, else raise the stop.
            for symbol in list(positions.keys()):
                df = features_by_symbol[symbol]
                if today not in df.index:
                    continue
                bar = df.loc[today]
                pos = positions[symbol]
                strat = self.strategies.get(pos.strategy)
                exit_sig = bool(strat.should_exit(symbol, df.loc[:today], pos.side)) if strat else False
                trade = self._manage(pos, bar, today, exit_sig)
                if trade is not None:
                    realized += trade.pnl
                    trades.append(trade)
                    del positions[symbol]

            # 3. Generate new entries (signal at today's close -> fill next open).
            equity = self.initial_equity + realized + self._unrealized(positions, features_by_symbol, today)
            for symbol, df in features_by_symbol.items():
                if symbol in positions or symbol in pending:
                    continue
                if today not in df.index:
                    continue
                if len(positions) + len(pending) >= self.max_open:
                    break
                order = self._consider(symbol, df.loc[:today], equity, positions, features_by_symbol, today)
                if order is not None:
                    pending[symbol] = order

            equity_points.append((today, equity))

        curve = pd.Series(dict(equity_points)).sort_index()
        return BacktestResult(curve, trades, compute_metrics(curve, trades))

    # --- internals ---
    def _consider(self, symbol, hist, equity, positions, feats_by_sym, today) -> dict | None:
        active = self.regime.active_strategy(hist)
        if active is None:
            return None
        intent = self.strategies[active].generate(symbol, hist)
        if intent is None:
            return None
        gated = self.sentiment.apply(intent)
        if gated is None:
            return None

        last_price = float(hist.iloc[-1].close)
        # Gross exposure here is live mark-to-market (today's close), deliberately
        # different from the cost-basis gross the other RiskManager.evaluate()
        # callers use -- see src/risk/exposure.py's module docstring. open_risk is
        # NOT mark-to-market in any caller (it's always entry-vs-stop), so it comes
        # from the shared helper like everywhere else.
        gross = self._unrealized(positions, feats_by_sym, today, market_value=True)
        open_risk = compute_exposure(positions.values()).open_risk_dollars
        acct = AccountState(
            equity=equity,
            start_of_day_equity=equity,
            buying_power=equity * 2,
            last_price=last_price,
            open_positions=len(positions),
            gross_exposure_value=gross,
            open_risk_dollars=open_risk,
            is_intraday=False,
        )
        decision = self.risk.evaluate(gated, acct)
        if decision.approved_qty <= 0:
            return None
        atr = float(hist.iloc[-1].atr) if "atr" in hist.columns else None
        return {"side": gated.side, "qty": decision.approved_qty, "strategy": gated.strategy, "atr": atr}

    def _fill_entry(self, symbol, order, bar, today) -> _Position:
        side = order["side"]
        raw = float(bar.open)
        fill = raw * (1 + self.slip) if side == Side.LONG else raw * (1 - self.slip)
        params = self.config.risk_limits.get("ratchet_stop", {}).get(order["strategy"], {})
        atr = order["atr"] if "atr_multiple_initial" in params else None
        ratchet = build_ratchet(order["strategy"], params, fill, side, atr=atr)
        take_profit = None
        if "profit_target_pct" in params:
            tp = float(params["profit_target_pct"]) / 100.0
            take_profit = fill * (1 + tp) if side == Side.LONG else fill * (1 - tp)
        return _Position(symbol, side, order["qty"], fill, today, ratchet, take_profit, order["strategy"])

    def _manage(self, pos: _Position, bar, today, exit_sig: bool = False) -> Trade | None:
        high, low, close = float(bar.high), float(bar.low), float(bar.close)
        stop = pos.ratchet.stop

        if pos.side == Side.LONG:
            if low <= stop:
                return self._close(pos, stop, today, "stop")
            if pos.take_profit and high >= pos.take_profit:
                return self._close(pos, pos.take_profit, today, "target")
            if exit_sig:
                return self._close(pos, close, today, "signal_exit")
            pos.ratchet.update(close)
        else:
            if high >= stop:
                return self._close(pos, stop, today, "stop")
            if pos.take_profit and low <= pos.take_profit:
                return self._close(pos, pos.take_profit, today, "target")
            if exit_sig:
                return self._close(pos, close, today, "signal_exit")
            pos.ratchet.update(close)
        return None

    def _close(self, pos: _Position, raw_exit, today, reason) -> Trade:
        exit_fill = raw_exit * (1 - self.slip) if pos.side == Side.LONG else raw_exit * (1 + self.slip)
        if pos.side == Side.LONG:
            pnl = (exit_fill - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - exit_fill) * pos.qty
        pnl -= self.commission * pos.qty * 2
        return Trade(
            symbol=pos.symbol, side=pos.side.value, strategy=pos.strategy,
            entry_date=pos.entry_date, exit_date=today,
            entry_price=round(pos.entry_price, 4), exit_price=round(exit_fill, 4),
            qty=pos.qty, pnl=round(pnl, 2),
            return_pct=round(pnl / (pos.entry_price * pos.qty), 4), reason=reason,
        )

    def _unrealized(self, positions, feats_by_sym, today, market_value=False) -> float:
        total = 0.0
        for symbol, pos in positions.items():
            df = feats_by_sym[symbol]
            if today not in df.index:
                continue
            close = float(df.loc[today].close)
            if market_value:
                total += pos.qty * close
            elif pos.side == Side.LONG:
                total += (close - pos.entry_price) * pos.qty
            else:
                total += (pos.entry_price - close) * pos.qty
        return total
