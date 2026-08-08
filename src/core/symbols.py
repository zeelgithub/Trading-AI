"""
Symbol resolver -- core layer.

Turns free-form text ("tesla", "TSLA", "palantir", "teslla") into a validated,
tradable ticker using the broker's FULL asset catalog (~11k US equities) instead
of a hardcoded name table. The catalog is cached locally (atomic JSON, refreshed
every few days) so resolution is offline-fast and one broker outage never blinds
the phone.

Resolution order: alias table (nicknames like "google" -> GOOGL) -> exact ticker
-> company-name match (prefix > substring > fuzzy for typos). One confident hit
resolves; several plausible hits come back as "ambiguous" with suggestions; no
hit is an honest "none" (e.g. SpaceX -- private, no listing).

Boundary: read-only catalog via the broker interface; places orders NO.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = PROJECT_ROOT / "state" / "assets_cache.json"
CACHE_MAX_AGE_HOURS = 72

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")

# Words that appear in almost every listing name; ignored for name matching.
_NOISE = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd",
          "plc", "class", "common", "stock", "shares", "the", "holdings",
          "group", "company", "companies"}

log = get_logger("symbols")


@dataclass(frozen=True)
class ResolveResult:
    status: str                      # ok | ambiguous | none
    symbol: str | None = None
    name: str = ""
    candidates: tuple = ()           # ((symbol, name), ...) when ambiguous
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class SymbolResolver:
    def __init__(
        self,
        broker=None,
        cache_path: str | Path = DEFAULT_CACHE_PATH,
        aliases: dict[str, str] | None = None,
        max_age_hours: int = CACHE_MAX_AGE_HOURS,
    ) -> None:
        self.broker = broker
        self.cache_path = Path(cache_path)
        self.aliases = {k.lower(): v.upper() for k, v in (aliases or {}).items()}
        self.max_age_hours = max_age_hours
        self._catalog: dict[str, str] | None = None  # SYMBOL -> company name

    # --- catalog (cached) ---
    def catalog(self) -> dict[str, str]:
        if self._catalog is not None:
            return self._catalog
        cached, age_ok = self._read_cache()
        if cached is not None and age_ok:
            self._catalog = cached
            return self._catalog
        fresh = self._fetch()
        if fresh:
            self._catalog = fresh
        elif cached is not None:
            log.warning("asset catalog refresh failed -- using stale cache")
            self._catalog = cached          # stale beats blind
        else:
            self._catalog = {}
        return self._catalog

    def _read_cache(self) -> tuple[dict[str, str] | None, bool]:
        payload, quarantined = load_json_or_quarantine(self.cache_path)
        if quarantined is not None or not isinstance(payload, dict):
            return None, False
        assets = payload.get("assets")
        if not isinstance(assets, dict) or not assets:
            return None, False
        try:
            ts = datetime.fromisoformat(payload.get("ts", ""))
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        except ValueError:
            return assets, False
        return assets, age_h <= self.max_age_hours

    def _fetch(self) -> dict[str, str]:
        if self.broker is None:
            return {}
        try:
            assets = {a.symbol.upper(): a.name for a in self.broker.list_assets() if a.tradable}
        except Exception as exc:
            log.warning("could not fetch asset catalog: %s", exc)
            return {}
        if assets:
            atomic_write_json(self.cache_path, {
                "ts": datetime.now(timezone.utc).isoformat(), "assets": assets})
        return assets

    # --- resolution ---
    def resolve(self, query: str) -> ResolveResult:
        q = (query or "").strip()
        if not q:
            return ResolveResult("none", note="empty query")
        catalog = self.catalog()

        alias = self.aliases.get(q.lower())
        if alias and (not catalog or alias in catalog):
            return ResolveResult("ok", alias, catalog.get(alias, ""))

        upper = q.upper()
        if _TICKER_RE.match(upper):
            if upper in catalog:
                return ResolveResult("ok", upper, catalog[upper])
            if not catalog:  # catalog unavailable: trust ticker-shaped input
                return ResolveResult("ok", upper, note="unverified (no catalog)")

        matches = self._name_search(q, catalog)
        if len(matches) == 1:
            sym, name = matches[0]
            return ResolveResult("ok", sym, name)
        if matches:
            return ResolveResult("ambiguous", candidates=tuple(matches[:5]))
        return ResolveResult(
            "none",
            note=f'"{q}" matches no tradable US listing -- it may be private, '
                 "foreign-listed, or misspelled.")

    def _name_search(self, q: str, catalog: dict[str, str]) -> list[tuple[str, str]]:
        ql = q.lower().strip()
        if not ql or not catalog:
            return []
        prefix: list[tuple[str, str]] = []
        contains: list[tuple[str, str]] = []
        cleaned: dict[str, str] = {}
        for sym, name in catalog.items():
            nl = " ".join(w for w in name.lower().replace(",", " ").split()
                          if w not in _NOISE)
            if not nl:
                continue
            cleaned[sym] = nl
            if nl.startswith(ql) or any(w.startswith(ql) for w in nl.split()):
                prefix.append((sym, name))
            elif ql in nl:
                contains.append((sym, name))
        # Exact cleaned-name match beats everything (e.g. "tesla" -> Tesla Inc).
        exact = [(s, catalog[s]) for s, nl in cleaned.items() if nl == ql]
        if len(exact) == 1:
            return exact
        hits = prefix or contains
        if hits:
            return sorted(hits, key=lambda t: len(t[1]))[:5]
        # Typo tolerance: fuzzy-match the query against cleaned names.
        close = difflib.get_close_matches(ql, list(cleaned.values()), n=3, cutoff=0.8)
        return [(s, catalog[s]) for s, nl in cleaned.items() if nl in close][:5]
