"""
Pipeline builder -- discovery layer.

Composition root for a live `DiscoveryPipeline`: reads the `discovery:` config
block, switches on the enabled sources, and wires their real (free) data feeds.
Shared by the `run_discovery` entrypoint and the Telegram `/ideas` command so
both build an identical pipeline.

Boundary: constructs read-only sources; the pipeline it returns places NO orders.
"""

from __future__ import annotations

from collections.abc import Callable

from src.common.config import Config, load_config
from src.discovery.pipeline import DiscoveryPipeline, PriceFn
from src.discovery.scorer import Scorer
from src.discovery.sources.registry import REGISTRY, SourceContext
from src.discovery.universe import cached_feature_provider, discovery_universe
from src.discovery.weight_advisor import DiscoveryWeightStateStore
from src.research.scoreboard import Scoreboard
from src.risk.risk_manager import RiskManager


def _default_price_fn() -> PriceFn:
    """Last daily close via the market-data feed (read-only, no trading creds)."""
    from src.data.providers.alpaca_data import AlpacaData

    data = AlpacaData()

    def price(symbol: str) -> float | None:
        bars = data.get_daily_bars(symbol, lookback_days=30)
        if bars.empty:
            return None
        return float(bars.iloc[-1].close)

    return price


def build_discovery_pipeline(
    config: Config | None = None,
    *,
    price_fn: PriceFn | None = None,
    scoreboard: Scoreboard | None = None,
    feature_provider: Callable | None = None,
) -> DiscoveryPipeline:
    config = config or load_config()
    d = config.get("settings.discovery", {}) or {}
    enabled = d.get("sources", {}) or {}

    # Computed once and shared by every source below -- previously each
    # enabled source called discovery_universe(config) independently
    # (cheap but redundant), and technical/volatility each built their OWN
    # cached_feature_provider(config) with no universe hint, so the bar
    # cache got warmed with ~4,000 individual HTTP round-trips instead of a
    # handful of batched ones. See cached_feature_provider()'s docstring.
    universe = discovery_universe(config)
    _shared_feature_provider = None

    def _feature_provider():
        nonlocal _shared_feature_provider
        if feature_provider is not None:
            return feature_provider
        if _shared_feature_provider is None:
            _shared_feature_provider = cached_feature_provider(config, universe=universe)
        return _shared_feature_provider

    # One registered factory per source name (src/discovery/sources/registry.py)
    # instead of a hand-written if/elif chain -- adding a 7th source means
    # registering it there, not editing this loop.
    ctx = SourceContext(config=config, discovery=d, universe=universe,
                        feature_provider=_feature_provider, scoreboard=scoreboard)
    sources = [factory(ctx) for name, factory in REGISTRY.items() if enabled.get(name, False)]

    # Approved reweighting (src/discovery/weight_advisor.py, applied via
    # /reweight on the phone) overrides the static config default -- absent
    # any approval, this is a no-op and the config weights win, same posture
    # as strategy rotation's state-over-config layering.
    scorer = Scorer.from_config(config)
    scorer.weights.update(DiscoveryWeightStateStore().load().weights)

    return DiscoveryPipeline(
        sources=sources,
        scorer=scorer,
        risk=RiskManager(config),
        config=config,
        price_fn=price_fn or _default_price_fn(),
        top_n=int(d.get("top_n", 4)),
        min_score=float(d.get("min_score", 25)),
        default_stop_pct=float((d.get("congress", {}) or {}).get("default_stop_pct", 10.0)),
        expiry_minutes=int(config.get("settings.approval.proposal_expiry_minutes", 1080)),
        min_price=float(d.get("min_price", 5.0)),
        source_timeout_seconds=float(d.get("source_timeout_seconds", 300.0)),
    )
