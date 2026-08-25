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
    max_open_risk_pct: float | None = Field(None, gt=0, le=100)
    max_gross_exposure_pct: float = Field(175.0, gt=0)
    max_open_positions: int = Field(10, gt=0)


class PositionLimits(_Lenient):
    max_position_pct: float = Field(10.0, gt=0, le=100)
    max_per_symbol_pct: float = Field(10.0, gt=0, le=100)
    per_trade_risk_pct: float = Field(1.0, gt=0, le=100)


class AllocationLimits(_Lenient):
    per_strategy_risk_pct: float = Field(1.33, gt=0, le=100)


class CircuitBreakerLimits(_Lenient):
    max_orders_per_minute: int = Field(10, gt=0)
    order_rate_window_seconds: int = Field(60, gt=0)
    max_orders_per_day: int = Field(50, gt=0)
    max_consecutive_errors: int = Field(5, gt=0)
    fat_finger_price_band_pct: float = Field(20.0, gt=0)
    require_manual_reset_after_kill: bool = True


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
    def _one_family_present(self) -> RatchetParams:
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
    # Optional dead-man's-switch: if set, a HEALTHY probe pings this URL (e.g.
    # a free healthchecks.io check). That service pages YOU if the ping itself
    # stops arriving -- the one failure mode healthcheck.py can't detect on
    # its own (the machine is off, or the scheduled task never fires).
    dead_mans_switch_url: str | None = None


class ApprovalSettings(_Lenient):
    require_approval: bool = True
    proposal_expiry_minutes: int = Field(1080, gt=0)


class AlertsSettings(_Lenient):
    enabled: bool = True
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


class ExecutionSettings(_Lenient):
    # Alpaca auto-cancels a GTC order 90 days after creation/last-modification
    # (docs.alpaca.markets/us/docs/orders-at-alpaca) -- must stay under 90 or
    # every resting stop would qualify for refresh on every cycle.
    stop_refresh_min_days_remaining: int = Field(15, gt=0, lt=90)


class DiscoveryCongressSettings(_Lenient):
    politicians: list[str] = Field(default_factory=list)
    max_disclosure_age_days: int = Field(45, gt=0)
    default_stop_pct: float = Field(10.0, gt=0, le=100)


class DiscoveryNewsSettings(_Lenient):
    lookback_days: int = Field(7, gt=0)


class DiscoverySocialSettings(_Lenient):
    subreddits: list[str] = Field(default_factory=lambda: ["wallstreetbets", "stocks"])
    limit_per_subreddit: int = Field(100, gt=0)


class DiscoveryUniverseSettings(_Lenient):
    extra: list[str] = Field(default_factory=list)
    sp500: bool = False
    sp400: bool = False
    sp600: bool = False
    smallcap: bool = False
    volatile: bool = False
    # Each static list (sp500.py etc.) carries its own SOURCED_DATE but
    # nothing read it before src/discovery/freshness.py -- this is the
    # alert threshold that module checks against.
    max_staleness_days: int = Field(45, gt=0)


class DiscoverySettings(_Lenient):
    """discovery.* had NO schema at all before this -- every other
    settings.yaml section does. `sources`/`weights` are kept as open
    dict[str, ...] (not per-source fields) so the single source of truth for
    "what sources exist" stays src/discovery/candidate.SOURCES /
    sources/registry.py, not a third hand-copied list here; the
    model_validator below checks their KEYS against that list instead."""

    top_n: int = Field(4, gt=0)
    min_score: float = Field(25.0, ge=0, le=100)
    min_price: float = Field(5.0, ge=0)
    # Bounds how long DiscoveryPipeline._gather() waits on any one source
    # (see pipeline.py) -- what makes a throttled source (the documented
    # `fundamentals` yfinance case) fail soft on time instead of blowing out
    # the whole daily schedule.
    source_timeout_seconds: float = Field(300.0, gt=0)
    sources: dict[str, bool] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    congress: DiscoveryCongressSettings = Field(default_factory=DiscoveryCongressSettings)
    news: DiscoveryNewsSettings = Field(default_factory=DiscoveryNewsSettings)
    social: DiscoverySocialSettings = Field(default_factory=DiscoverySocialSettings)
    universe: DiscoveryUniverseSettings = Field(default_factory=DiscoveryUniverseSettings)

    @model_validator(mode="after")
    def _sources_and_weights_keys_are_known(self) -> DiscoverySettings:
        # Lazy, function-local: src/common is foundational and must not
        # import src/discovery at module load (discovery already imports
        # src.common.config, so a module-level import here would invert that
        # layering and risk a circular import). This validator only runs at
        # config-load time, well after both packages are fully importable.
        from src.discovery.candidate import SOURCES

        known = set(SOURCES)
        bad_sources = set(self.sources) - known
        bad_weights = set(self.weights) - known
        if bad_sources:
            raise ValueError(
                f"discovery.sources has unknown key(s) {sorted(bad_sources)} "
                f"(known sources: {sorted(known)}) -- a typo here was previously "
                f"silently ignored at runtime, not an error"
            )
        if bad_weights:
            raise ValueError(
                f"discovery.weights has unknown key(s) {sorted(bad_weights)} "
                f"(known sources: {sorted(known)}) -- a typo here was previously "
                f"silently ignored at runtime (Scorer never reads an unknown "
                f"weight key), not an error"
            )
        return self


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
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)


# --- strategies.yaml ------------------------------------------------------

class RegimeFilterSchema(_Lenient):
    enabled: bool = True
    adx_period: int = Field(14, gt=0)
    trending_adx_min: float = Field(25.0, gt=0, le=100)
    ranging_adx_max: float = Field(20.0, gt=0, le=100)
    atr_slope_lookback: int = Field(5, gt=0)
    routing: dict[str, str] = Field(default_factory=dict)


class ConfirmationCandleSchema(_Lenient):
    min_body_ratio: float = Field(0.5, gt=0, le=1)


class TrendConfidenceSchema(_Lenient):
    """trend_following's ADX-scaled confidence formula: min(max, base +
    max(0, adx - adx_baseline) / adx_divisor)."""
    base: float = Field(0.55, ge=0, le=1)
    adx_baseline: float = Field(25.0, ge=0)
    adx_divisor: float = Field(100.0, gt=0)
    max: float = Field(0.9, ge=0, le=1)


class StrategyBlockSchema(_Lenient):
    """One strategy's block under strategies.strategies.<name>. Deliberately
    loose beyond `enabled`/`direction`/`confidence` -- indicators/conditions/
    entry/exit vary per strategy and are mostly descriptive text, not
    machine-parsed, so they aren't worth a rigid schema; `extra="allow"`
    still passes them through untouched for the strategy classes themselves
    to read. `confidence` gets its own check since it directly scales
    position sizing (RiskManager.evaluate() step 7) -- real financial risk,
    not just descriptive config."""
    enabled: bool = True
    direction: list[str] = Field(default_factory=lambda: ["long", "short"])
    confidence: float | TrendConfidenceSchema | None = None

    @model_validator(mode="after")
    def _flat_confidence_in_range(self) -> StrategyBlockSchema:
        if isinstance(self.confidence, float) and not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        return self


class SentimentGateSchema(_Lenient):
    enabled: bool = True
    neutral_confidence_haircut: float = Field(0.8, gt=0, le=1)
    news_scorer_lookback_days: int = Field(3, gt=0)
    on_feed_unavailable: str = "skip_gate"


class StrategiesSchema(_Lenient):
    regime_filter: RegimeFilterSchema = Field(default_factory=RegimeFilterSchema)
    confirmation_candle: ConfirmationCandleSchema = Field(default_factory=ConfirmationCandleSchema)
    strategies: dict[str, StrategyBlockSchema] = Field(default_factory=dict)
    sentiment_gate: SentimentGateSchema = Field(default_factory=SentimentGateSchema)


# --- symbols.yaml ----------------------------------------------------------

class SymbolOverrides(_Lenient):
    # The only override RiskManager currently reads (per-symbol max position
    # size cap) -- see RiskManager._max_position_pct.
    max_position_pct: float | None = Field(default=None, gt=0, le=100)


class WatchlistEntry(_Lenient):
    symbol: str
    enabled: bool = True
    allow_short: bool = False
    overrides: SymbolOverrides = Field(default_factory=SymbolOverrides)

    @field_validator("symbol")
    @classmethod
    def _symbol_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("symbol must be a non-empty string")
        return v


class SymbolsSchema(_Lenient):
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


def validate_config(
    settings: dict[str, Any],
    risk_limits: dict[str, Any],
    strategies: dict[str, Any] | None = None,
    symbols: dict[str, Any] | None = None,
) -> None:
    """Validate the raw config mappings. Raises ConfigError with every problem
    listed (not just the first) on any type/range violation. strategies/symbols
    are optional params (default None -> skipped) so existing callers that only
    pass settings/risk_limits keep working unchanged."""
    errors: list[str] = []
    checks = [
        ("settings.yaml", SettingsSchema, settings),
        ("risk_limits.yaml", RiskLimitsSchema, risk_limits),
    ]
    if strategies is not None:
        checks.append(("strategies.yaml", StrategiesSchema, strategies))
    if symbols is not None:
        checks.append(("symbols.yaml", SymbolsSchema, symbols))
    for label, schema, payload in checks:
        try:
            schema.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            errors.append(f"{label}:\n{exc}")
    if errors:
        raise ConfigError(
            "Invalid configuration -- fix these before the bot will start:\n\n"
            + "\n\n".join(errors)
        )
