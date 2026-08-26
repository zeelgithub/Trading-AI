"""
Config loader -- common layer.

Loads and validates the YAML files in config/ (settings, risk_limits,
strategies, symbols) into read-only accessors.

Boundary: holds NO secrets (those live in secrets.py / .env).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.common.config_schema import validate_config

# Project root = two levels up from this file (src/common/config.py -> repo root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config {path.name} must be a mapping at top level.")
    return data


@dataclass(frozen=True)
class Config:
    """Immutable view over the four YAML config files."""

    settings: dict[str, Any]
    risk_limits: dict[str, Any]
    strategies: dict[str, Any]
    symbols: dict[str, Any]

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a nested value by dotted path, e.g. 'data.timeframe'.

        The first segment selects the file (settings/risk_limits/strategies/
        symbols); the rest walks the mapping.
        """
        head, _, tail = dotted.partition(".")
        node: Any = getattr(self, head, None)
        if node is None:
            return default
        for key in filter(None, tail.split(".")):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def is_live(self) -> bool:
        return str(self.settings.get("mode", "paper")).lower() == "live"

    def enabled_symbols(self) -> list[str]:
        watch = self.symbols.get("watchlist", []) or []
        return [w["symbol"] for w in watch if w.get("enabled", True)]

    def watchlist_entry(self, symbol: str) -> dict[str, Any] | None:
        """The raw symbols.yaml watchlist row for `symbol`, or None if it
        isn't on the watchlist. Case-sensitive exact match. Shared lookup for
        the two call sites that used to each hand-roll this same scan
        (Strategy.shorts_allowed, RiskManager._max_position_pct)."""
        for w in self.symbols.get("watchlist", []) or []:
            if w.get("symbol") == symbol:
                return w
        return None

    def research_universe(self) -> list[str]:
        """settings.research.backtest_universe if configured (the wider,
        survivorship-bias-corrected universe research tooling evaluates
        against), else the live watchlist. Shared default previously
        duplicated verbatim in scripts/evaluate_strategies.py and
        scripts/run_backtest.py."""
        return list(self.get("settings.research.backtest_universe", None) or self.enabled_symbols())

    def data_lookback_days(self) -> int:
        """settings.data.lookback_days, defaulting to 400 (enough history for
        the 200-day EMA + warmup, see settings.yaml's own comment). Shared
        default previously duplicated across five independent call sites."""
        return int(self.get("settings.data.lookback_days", 400))


@lru_cache(maxsize=1)
def load_config(config_dir: str | None = None) -> Config:
    """Load all config files once (cached). Pass config_dir to override.

    Validates all four YAML files against config_schema before returning: a
    misspelled key falling back to a silent default, or a wrong-type/
    out-of-range value crashing deep inside RiskManager or the strategy layer,
    both become one clear ConfigError here instead -- before any trading logic
    runs.
    """
    base = Path(config_dir) if config_dir else CONFIG_DIR
    settings = _load_yaml(base / "settings.yaml")
    risk_limits = _load_yaml(base / "risk_limits.yaml")
    strategies = _load_yaml(base / "strategies.yaml")
    symbols = _load_yaml(base / "symbols.yaml")
    validate_config(settings, risk_limits, strategies, symbols)
    return Config(
        settings=settings,
        risk_limits=risk_limits,
        strategies=strategies,
        symbols=symbols,
    )
