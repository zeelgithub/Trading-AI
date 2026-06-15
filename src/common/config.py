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


@lru_cache(maxsize=1)
def load_config(config_dir: str | None = None) -> Config:
    """Load all config files once (cached). Pass config_dir to override."""
    base = Path(config_dir) if config_dir else CONFIG_DIR
    return Config(
        settings=_load_yaml(base / "settings.yaml"),
        risk_limits=_load_yaml(base / "risk_limits.yaml"),
        strategies=_load_yaml(base / "strategies.yaml"),
        symbols=_load_yaml(base / "symbols.yaml"),
    )
