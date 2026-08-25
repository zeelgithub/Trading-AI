"""
Universe staleness check -- discovery layer.

Every static/data-derived ticker list this project widens the discovery
universe with (sp500.py, sp400.py, sp600.py, smallcap.py, volatile.py)
carries its own `SOURCED_DATE` constant, but nothing ever read it before this
module existed -- a list could silently rot indefinitely with no signal to
the operator that it needs regenerating. This checks every list that's
actually enabled (discovery.universe.<flag>) against
discovery.universe.max_staleness_days and reports which ones are overdue.

Deliberately a WARNING signal, not a halt: rule 3's default-to-halt covers
stale live PRICE data (a trading-correctness issue); a stale index-membership
snapshot is a data-quality issue for a suggestion-only layer that a human
still approves every idea from -- annoying if ignored, not unsafe.

Boundary: pure/read-only, no network, no orders. Sending the actual alert
(a network call via the Telegram notifier) is the caller's job -- see
scripts/run_discovery.py.
"""

from __future__ import annotations

import importlib
from datetime import date

from src.common.config import Config

# flag name (discovery.universe.<flag>) -> the module carrying SOURCED_DATE.
_UNIVERSE_LISTS: dict[str, str] = {
    "sp500": "src.discovery.sp500",
    "sp400": "src.discovery.sp400",
    "sp600": "src.discovery.sp600",
    "smallcap": "src.discovery.smallcap",
    "volatile": "src.discovery.volatile",
}


def stale_universe_lists(config: Config, *, today: date | None = None) -> list[tuple[str, int, int]]:
    """(flag, age_days, max_age_days) for every ENABLED static list whose
    SOURCED_DATE is older than discovery.universe.max_staleness_days. Only
    checks lists actually switched on -- a disabled list rotting is nobody's
    problem."""
    max_age = int(config.get("settings.discovery.universe.max_staleness_days", 45))
    today = today or date.today()
    stale: list[tuple[str, int, int]] = []
    for flag, module_name in _UNIVERSE_LISTS.items():
        if not config.get(f"settings.discovery.universe.{flag}", False):
            continue
        mod = importlib.import_module(module_name)
        sourced = date.fromisoformat(mod.SOURCED_DATE)
        age = (today - sourced).days
        if age > max_age:
            stale.append((flag, age, max_age))
    return stale


_HAS_BUILD_SCRIPT = {"smallcap", "volatile"}  # sp500/400/600 have no generator script yet


def _regen_hint(flag: str) -> str:
    if flag in _HAS_BUILD_SCRIPT:
        return f"regenerate with `python -m scripts.build_{flag}_universe`"
    return f"re-source manually (see src/discovery/{flag}.py's docstring)"


def staleness_detail(stale: list[tuple[str, int, int]]) -> str:
    """Human-readable digest line(s) for the Telegram alert / console print."""
    lines = [f"- discovery.universe.{flag}: sourced {age}d ago (limit {max_age}d) -- {_regen_hint(flag)}"
             for flag, age, max_age in stale]
    return "\n".join(lines)
