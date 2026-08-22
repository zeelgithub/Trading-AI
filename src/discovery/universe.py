"""
Universe + feature-provider helpers -- discovery layer.

The technical source needs (a) a list of symbols to screen and (b) a way to turn
a symbol into a causal feature frame. Discovery widens the watchlist-only
universe with the names Congress is buying plus a configured `extra` list, which
is how brand-new tickers get a technical read.

Boundary: read-only; places orders NO, holds credentials NO (the feature
provider uses market-data creds only, via the data layer).
"""

from __future__ import annotations

from pathlib import Path

from congress_copy.models import DisclosedTrade
from src.common.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISCLOSURES = PROJECT_ROOT / "congress_copy" / "data" / "disclosures.json"


def congress_buy_tickers(path: str | Path = DEFAULT_DISCLOSURES) -> list[str]:
    """Distinct tickers with a disclosed stock BUY (order-preserving)."""
    import json

    p = Path(path)
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out: list[str] = []
    for r in rows:
        try:
            t = DisclosedTrade.from_dict(r)
        except (KeyError, ValueError):
            continue
        if t.is_stock and t.is_buy and t.ticker not in out:
            out.append(t.ticker)
    return out


def discovery_universe(config: Config, disclosures: str | Path = DEFAULT_DISCLOSURES) -> list[str]:
    """Watchlist + congress buys + configured extras, de-duplicated."""
    syms: list[str] = list(config.enabled_symbols())
    for t in congress_buy_tickers(disclosures):
        if t not in syms:
            syms.append(t)
    for t in (config.get("settings.discovery.universe.extra", []) or []):
        t = str(t).upper()
        if t not in syms:
            syms.append(t)
    return syms


def cached_feature_provider(config: Config):
    """A symbol -> feature-frame callable. Delegates to the data layer's
    `live_feature_provider` -- the SAME pipeline the orchestrator uses
    (incremental ingest, in-progress bar dropped, causal features), so the
    technical source sees exactly what the live cycle sees."""
    from src.data.ingest import live_feature_provider

    return live_feature_provider(config)
