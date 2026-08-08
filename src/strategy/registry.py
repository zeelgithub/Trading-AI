"""
Strategy registry -- strategy layer.

Single place where strategy names map to classes. Adding strategy #4 is: write
the module, `@register` it (or add it to the imports below), give it a block in
config/strategies.yaml -- no orchestrator edits. The orchestrator, rotation
service, and scoreboard all key on the same `name` attribute.

Boundary: places orders NO.
"""

from __future__ import annotations

from src.common.config import Config
from src.strategy.base import Strategy
from src.strategy.breakout import Breakout
from src.strategy.mean_reversion import MeanReversion
from src.strategy.trend_following import TrendFollowing

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (TrendFollowing, MeanReversion, Breakout)
}


def register(cls: type[Strategy]) -> type[Strategy]:
    """Class decorator: `@register` on a Strategy subclass adds it by name."""
    REGISTRY[cls.name] = cls
    return cls


def build_strategies(config: Config) -> dict[str, Strategy]:
    """Instantiate every registered strategy that has a config block (all
    registered strategies if config lists none). Unknown config names are
    ignored -- config cannot conjure code that doesn't exist."""
    configured = list((config.strategies.get("strategies") or {}).keys())
    names = [n for n in configured if n in REGISTRY] or list(REGISTRY)
    return {name: REGISTRY[name](config) for name in names}
