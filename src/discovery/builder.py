"""
Pipeline builder -- discovery layer.

Composition root for a live `DiscoveryPipeline`: reads the `discovery:` config
block, switches on the enabled sources, and wires their real (free) data feeds.
Shared by the `run_discovery` entrypoint and the Telegram `/ideas` command so
both build an identical pipeline.

Boundary: constructs read-only sources; the pipeline it returns places NO orders.
"""

from __future__ import annotations

from typing import Callable

from congress_copy.providers import JSONFileProvider
from src.common.config import Config, load_config
from src.discovery.pipeline import DiscoveryPipeline, PriceFn
from src.discovery.scorer import Scorer
from src.discovery.sources.congress import CongressSource
from src.discovery.sources.technical import TechnicalSource
from src.discovery.universe import DEFAULT_DISCLOSURES, cached_feature_provider, discovery_universe
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

    sources = []
    if enabled.get("congress", False):
        cfg = d.get("congress", {}) or {}
        sources.append(CongressSource(
            provider=JSONFileProvider(DEFAULT_DISCLOSURES),
            politicians=tuple(cfg.get("politicians", []) or ()),
            max_age_days=int(cfg.get("max_disclosure_age_days", 45)),
        ))
    if enabled.get("technical", False):
        sources.append(TechnicalSource(
            config=config,
            feature_provider=feature_provider or cached_feature_provider(config),
            universe=discovery_universe(config),
            scoreboard=scoreboard or Scoreboard(),
        ))
    if enabled.get("news", False):
        from src.data.providers.news import AlpacaNews
        from src.discovery.sources.news import NewsSource
        ncfg = d.get("news", {}) or {}
        sources.append(NewsSource(
            provider=AlpacaNews(),
            universe=discovery_universe(config),
            days=int(ncfg.get("lookback_days", 7)),
        ))
    if enabled.get("fundamentals", False):
        from src.data.providers.fundamentals import YFinanceFundamentals
        from src.discovery.sources.fundamentals import FundamentalsSource
        sources.append(FundamentalsSource(
            provider=YFinanceFundamentals(),
            universe=discovery_universe(config),
        ))

    return DiscoveryPipeline(
        sources=sources,
        scorer=Scorer.from_config(config),
        risk=RiskManager(config),
        config=config,
        price_fn=price_fn or _default_price_fn(),
        top_n=int(d.get("top_n", 4)),
        min_score=float(d.get("min_score", 25)),
        default_stop_pct=float((d.get("congress", {}) or {}).get("default_stop_pct", 10.0)),
        expiry_minutes=int(config.get("settings.approval.proposal_expiry_minutes", 1080)),
    )
