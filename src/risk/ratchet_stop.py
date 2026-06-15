"""
Ratchet stop engine -- risk layer.

Universal monotonic stop: the stop only ever moves toward profit, never back.
Three modes driven by per-strategy config:

  - PercentRatchet : the breakeven-lock ladder (trend following). Initial stop at
    entry -/+ initial_stop_pct; once price gains `lock_trigger_pct`, the stop
    snaps to a locked profit floor (`profit_lock_pct`) and steps up every
    `step_pct` of further gain.
  - PercentRatchet (fixed) : omit the lock params -> a plain fixed-percent stop
    (mean reversion uses a tight 2pct stop; its profit target is the strategy's
    own exit, not the ratchet).
  - AtrRatchet : volatility-scaled trailing stop (breakout). Initial at
    entry -/+ initial*ATR; trails high-water -/+ trail*ATR.

Computes stop *levels* only. Execution rests/raises the actual order at broker.

Boundary: computes levels only, places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.models import Side


def _is_long(side: Side) -> bool:
    return side == Side.LONG


@dataclass
class PercentRatchet:
    entry: float
    side: Side
    initial_stop_pct: float
    lock_trigger_pct: float | None = None
    profit_lock_pct: float | None = None
    step_pct: float | None = None
    _stop: float = field(init=False)
    _extreme: float = field(init=False)  # high-water (long) / low-water (short)

    def __post_init__(self) -> None:
        self._extreme = self.entry
        if _is_long(self.side):
            self._stop = self.entry * (1 - self.initial_stop_pct / 100.0)
        else:
            self._stop = self.entry * (1 + self.initial_stop_pct / 100.0)

    @property
    def stop(self) -> float:
        return self._stop

    @property
    def armed(self) -> bool:
        """True once the profit lock has engaged."""
        return self._locked_floor_pct() is not None

    def _locked_floor_pct(self) -> float | None:
        if self.lock_trigger_pct is None or self.profit_lock_pct is None:
            return None
        # Work in price space to avoid pct-subtraction float error (e.g. 1.2-1.0).
        eps = 1e-9
        step = self.step_pct or 0.0
        step_value = self.entry * (step / 100.0)
        if _is_long(self.side):
            trigger_price = self.entry * (1 + self.lock_trigger_pct / 100.0)
            if self._extreme < trigger_price - eps:
                return None
            advance = self._extreme - trigger_price
        else:
            trigger_price = self.entry * (1 - self.lock_trigger_pct / 100.0)
            if self._extreme > trigger_price + eps:
                return None
            advance = trigger_price - self._extreme
        extra_steps = int(advance / step_value + eps) if step_value > 0 else 0
        return self.profit_lock_pct + extra_steps * step

    def update(self, price: float, atr: float | None = None) -> float:
        """Feed the latest price; return the (monotonic) stop level."""
        if _is_long(self.side):
            self._extreme = max(self._extreme, price)
        else:
            self._extreme = min(self._extreme, price)

        floor_pct = self._locked_floor_pct()
        if floor_pct is not None:
            if _is_long(self.side):
                candidate = self.entry * (1 + floor_pct / 100.0)
                self._stop = max(self._stop, candidate)
            else:
                candidate = self.entry * (1 - floor_pct / 100.0)
                self._stop = min(self._stop, candidate)
        return self._stop

    def is_hit(self, price: float) -> bool:
        return price <= self._stop if _is_long(self.side) else price >= self._stop

    def state(self) -> dict:
        return {
            "kind": "percent",
            "entry": self.entry,
            "side": self.side.value,
            "initial_stop_pct": self.initial_stop_pct,
            "lock_trigger_pct": self.lock_trigger_pct,
            "profit_lock_pct": self.profit_lock_pct,
            "step_pct": self.step_pct,
            "extreme": self._extreme,
            "stop": self._stop,
        }


@dataclass
class AtrRatchet:
    entry: float
    side: Side
    atr_multiple_initial: float
    atr_multiple_trail: float
    atr: float  # ATR at entry
    _stop: float = field(init=False)
    _extreme: float = field(init=False)

    def __post_init__(self) -> None:
        self._extreme = self.entry
        if _is_long(self.side):
            self._stop = self.entry - self.atr_multiple_initial * self.atr
        else:
            self._stop = self.entry + self.atr_multiple_initial * self.atr

    @property
    def stop(self) -> float:
        return self._stop

    def update(self, price: float, atr: float | None = None) -> float:
        a = atr if atr is not None else self.atr
        if _is_long(self.side):
            self._extreme = max(self._extreme, price)
            candidate = self._extreme - self.atr_multiple_trail * a
            self._stop = max(self._stop, candidate)
        else:
            self._extreme = min(self._extreme, price)
            candidate = self._extreme + self.atr_multiple_trail * a
            self._stop = min(self._stop, candidate)
        return self._stop

    def is_hit(self, price: float) -> bool:
        return price <= self._stop if _is_long(self.side) else price >= self._stop

    def state(self) -> dict:
        return {
            "kind": "atr",
            "entry": self.entry,
            "side": self.side.value,
            "atr_multiple_initial": self.atr_multiple_initial,
            "atr_multiple_trail": self.atr_multiple_trail,
            "atr": self.atr,
            "extreme": self._extreme,
            "stop": self._stop,
        }


def ratchet_from_state(d: dict):
    """Rebuild a ratchet (including its high/low-water mark and current stop)."""
    side = Side(d["side"])
    if d["kind"] == "atr":
        r = AtrRatchet(
            entry=d["entry"], side=side,
            atr_multiple_initial=d["atr_multiple_initial"],
            atr_multiple_trail=d["atr_multiple_trail"], atr=d["atr"],
        )
    else:
        r = PercentRatchet(
            entry=d["entry"], side=side,
            initial_stop_pct=d["initial_stop_pct"],
            lock_trigger_pct=d.get("lock_trigger_pct"),
            profit_lock_pct=d.get("profit_lock_pct"),
            step_pct=d.get("step_pct"),
        )
    r._extreme = d["extreme"]
    r._stop = d["stop"]
    return r


def build_ratchet(
    strategy: str,
    params: dict,
    entry: float,
    side: Side,
    atr: float | None = None,
):
    """Construct the right ratchet for a strategy from its config params block.

    `params` is the per-strategy mapping under risk_limits.yaml -> ratchet_stop.
    """
    if "atr_multiple_initial" in params:
        if atr is None:
            raise ValueError(f"Strategy {strategy!r} needs an ATR value to build a ratchet.")
        return AtrRatchet(
            entry=entry,
            side=side,
            atr_multiple_initial=float(params["atr_multiple_initial"]),
            atr_multiple_trail=float(params.get("atr_multiple_trail", params["atr_multiple_initial"])),
            atr=atr,
        )
    return PercentRatchet(
        entry=entry,
        side=side,
        initial_stop_pct=float(params["initial_stop_pct"]),
        lock_trigger_pct=_opt(params, "lock_trigger_pct"),
        profit_lock_pct=_opt(params, "profit_lock_pct"),
        step_pct=_opt(params, "step_pct"),
    )


def _opt(params: dict, key: str) -> float | None:
    return float(params[key]) if key in params else None
