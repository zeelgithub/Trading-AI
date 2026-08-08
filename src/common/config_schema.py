"""
Config validation schema -- common layer.

Every risk-critical value up to now was read with `.get(key, hardcoded_default)`
scattered across risk_manager.py, circuit_breakers.py, etc: a misspelled YAML
key silently falls back to the code default with no warning, and a wrong-type
value (e.g. a quoted percent) crashes deep inside RiskManager.__init__ with an
unhelpful bare ValueError. This module gives config a schema so both failure
modes become one clear error at boot, before any trading logic runs.

Deliberately lenient at the section level (every section is optional, with
defaults mirroring the existing code fallbacks) -- the point is "if a value is
present, it must be sane," not "every key is now mandatory." Unknown keys are
allowed (extra="allow") so new config additions never need a schema change to
avoid breaking, and future YAML sections are simply unchecked, not rejected.

Boundary: pure validation, no IO, no orders.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigError(Exception):
    """Raised at load_config() time when a YAML value is missing/wrong-type/
    out-of-range. Fails fast, before any trading logic runs."""


class _Lenient(BaseModel):
    """Base: unknown keys pass through untouched (forward-compatible)."""

    model_config = ConfigDict(extra="allow")


# --- risk_limits.yaml ---------------------------------------------------

class AccountLimits(_Lenient):
    max_daily_loss_pct: float = Field(4.0, gt=0, le=100)
    max_gross_exposure_pct: float = Field(175.0, gt=0)
    max_open_positions: int = Field(10, gt=0)


class PositionLimits(_Lenient):
    max_position_pct: float = Field(10.0, gt=0, le=100)
    max_per_symbol_pct: float = Field(10.0, gt=0, le=100)
    per_trade_risk_pct: float = Field(1.0, gt=0, le=100)


class AllocationLimits(_Lenient):
    scheme: str = "equal_risk"
    per_strategy_risk_pct: float = Field(1.33, gt=0, le=100)


class CircuitBreakerLimits(_Lenient):
    max_orders_per_minute: int = Field(10, gt=0)
    max_orders_per_day: int = Field(50, gt=0)
    max_consecutive_errors: int = Field(5, gt=0)
    fat_finger_price_band_pct: float = Field(20.0, gt=0)
    require_manual_reset_after_kill: bool = True


class PdtLimits(_Lenient):
    enforce: bool = True
    min_equity_for_daytrading: float = Field(25000.0, gt=0)
    max_day_trades_rolling_5d: int = Field(3, ge=0)


class RatchetParams(_Lenient):
    """One strategy's ratchet_stop block. Either the percent family
    (initial_stop_pct, ...) or the ATR family (atr_multiple_initial, ...)
    must be present -- build_ratchet() requires one of them to construct a
    stop at all."""

    initial_stop_pct: float | None = Field(default=None, gt=0, le=100)
    lock_trigger_pct: float | None = Field(default=None, gt=0)
    profit_lock_pct: float | None = Field(default=None, gt=0)
    step_pct: float | None = Field(default=None, gt=0)
    profit_target_pct: float | None = Field(default=None, gt=0)
    atr_multiple_initial: float | None = Field(default=None, gt=0)
    atr_multiple_trail: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_family_present(self) -> "RatchetParams":
        if self.initial_stop_pct is None and self.atr_multiple_initial is None:
            raise ValueError(
                "needs initial_stop_pct (percent ratchet) or "
                "atr_multiple_initial (ATR ratchet)")
        return self


class RiskLimitsSchema(_Lenient):
    account: AccountLimits = Field(default_factory=AccountLimits)
    position: PositionLimits = Field(default_factory=PositionLimits)
    allocation: AllocationLimits = Field(default_factory=AllocationLimits)
    circuit_breakers: CircuitBreakerLimits = Field(default_factory=CircuitBreakerLimits)
    pdt: PdtLimits = Field(default_factory=PdtLimits)
    ratchet_stop: dict[str, RatchetParams] = Field(default_factory=dict)


# --- settings.yaml -------------------------------------------------------

class DataSettings(_Lenient):
    timeframe: str = "1Day"
    feed: str = "iex"
    lookback_days: int = Field(400, gt=0)
    max_bar_age_days: int = Field(4, gt=0)


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SchedulingSettings(_Lenient):
    evaluate_at: str = "15:45"

    @field_validator("evaluate_at")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError(f"evaluate_at must be 24h HH:MM, got {v!r}")
        return v


class WatchdogSettings(_Lenient):
    cycle_grace_minutes: int = Field(45, gt=0)
    listener_max_age_seconds: int = Field(300, gt=0)
    alert_cooldown_minutes: int = Field(360, gt=0)


class ApprovalSettings(_Lenient):
    require_approval: bool = True
    proposal_expiry_minutes: int = Field(1080, gt=0)


class AlertsSettings(_Lenient):
    enabled: bool = True
    channel: str = "telegram"
    events: list[str] = Field(default_factory=list)


class ResearchSettings(_Lenient):
    backtest_universe: list[str] = Field(default_factory=list)


class BacktestSettings(_Lenient):
    # Alpaca is commission-free for US equities; slippage is a conservative
    # fill-cost assumption so backtests don't look better than live will be.
    commission_per_share: float = Field(0.0, ge=0)
    slippage_bps: float = Field(5.0, ge=0)


class SelfHealSettings(_Lenient):
    cooldown_seconds: int = Field(300, gt=0)
    max_per_day: int = Field(3, gt=0)


class ConcurrencySettings(_Lenient):
    cycle_lock_timeout_seconds: int = Field(60, gt=0)
    action_lock_timeout_seconds: int = Field(15, gt=0)


class SettingsSchema(_Lenient):
    mode: Literal["paper", "live"] = "paper"
    data: DataSettings = Field(default_factory=DataSettings)
    scheduling: SchedulingSettings = Field(default_factory=SchedulingSettings)
    watchdog: WatchdogSettings = Field(default_factory=WatchdogSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    alerts: AlertsSettings = Field(default_factory=AlertsSettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    self_heal: SelfHealSettings = Field(default_factory=SelfHealSettings)
    concurrency: ConcurrencySettings = Field(default_factory=ConcurrencySettings)


def validate_config(settings: dict[str, Any], risk_limits: dict[str, Any]) -> None:
    """Validate the raw settings/risk_limits mappings. Raises ConfigError with
    every problem listed (not just the first) on any type/range violation."""
    errors: list[str] = []
    for label, schema, payload in (
        ("settings.yaml", SettingsSchema, settings),
        ("risk_limits.yaml", RiskLimitsSchema, risk_limits),
    ):
        try:
            schema.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            errors.append(f"{label}:\n{exc}")
    if errors:
        raise ConfigError(
            "Invalid configuration -- fix these before the bot will start:\n\n"
            + "\n\n".join(errors)
        )
