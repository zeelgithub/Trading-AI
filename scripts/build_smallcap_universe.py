"""
Build the small/micro-cap discovery-universe ticker list (RESEARCH/OFFLINE
TOOL -- not run automatically by the bot; regenerate periodically, same
"static, hand/script-refreshed" expectation as sp500.py/sp400.py/sp600.py).

Added 2026-08-24 at the user's request to widen discovery further into
"more volatile, more fluctuating" territory. The S&P 500/400/600 lists (and
the existing volatility re-ranker, src/discovery/sources/volatility.py) cover
large/mid/small-cap quality names; there's real market below that S&P 600
tends not to reach. No index (Russell 2000 or otherwise) publishes a free,
scrapable membership list the way Wikipedia does for the S&P tiers -- tried
first, hit a dead end (no Wikipedia constituent table, ETF-provider holdings
pages paywalled or paginated ~2,000 rows deep with no bulk export). This
script builds the list from first-party data instead, in five stages, each
using a free source with no scraping fragility and no LLM-in-the-loop
transcription risk:

  1. Pull the full Nasdaq + NYSE/other bulk listing files (nasdaqtrader.com's
     nasdaqlisted.txt / otherlisted.txt -- plain pipe-delimited text, no
     auth, no JS, no per-page limit, ~13,000 rows in one request each) and
     drop ETFs (an explicit column), test issues (also explicit), and common
     non-common-stock instrument types by name pattern: warrants, units,
     rights, preferred, depositary shares/ADRs, SPAC "Acquisition Corp"
     shells, notes/bonds, closed-end funds.
  2. Drop anything already covered by the existing discovery universe
     (S&P 500/400/600 + watchlist + extras + congress buys) -- this list
     exists to ADD to that, not duplicate it.
  3. Cross-check survivors against Alpaca's own tradable asset catalog
     (`AlpacaBroker.list_assets()`) -- same discipline as sp500.py/sp400.py/
     sp600.py: nothing ships that isn't a real, currently tradable Alpaca
     symbol.
  4. Price/liquidity screen via Alpaca's own batched daily-bars endpoint
     (free, market-data creds, ~one HTTP round-trip per 200 symbols).
     UPDATED 2026-08-24, same day, at the user's explicit follow-up request
     to remove the price and liquidity floors entirely ("$0 / no floor" and
     "remove entirely" -- see docs/ROADMAP.md's Phase H for the full
     back-and-forth and risk disclosure that preceded this). Originally:
     last close >= discovery.min_price ($5) and avg dollar volume >=
     $500k/day. Now: last close > $0 (still excludes non-positive/bad-data
     prices, not a real floor) and avg dollar volume > $0 (still excludes
     completely untraded tickers -- there's no way to actually fill an
     order in a name with zero real volume -- but is not a liquidity
     screen in any meaningful sense). This means `discovery.min_price`'s
     runtime guard (`DiscoveryPipeline._size_and_propose()`) is now the ONLY
     thing standing between a sub-$5 candidate and a live Proposal, and that
     guard is ALSO set to $0 in this deployment's config/settings.yaml --
     penny-stock inclusion here is a deliberate, disclosed choice, not an
     oversight.
  5. Market-cap band via yfinance's `fast_info` (free, unofficial Yahoo
     data -- NOT the full `.info` scrape src/data/providers/fundamentals.py
     uses for live scoring; `.info` measured ~19s/symbol serially here,
     `fast_info` is sub-second). Keep symbols with market cap in
     [$50M, $6B] -- roughly nano-cap-excluded through S&P-600-sized, i.e.
     genuinely smaller than/adjacent to what sp500/400/600 already cover,
     not a re-hash of it. yfinance throttles under too much concurrency
     (confirmed live 2026-08-24: a 16-worker burst against `.info` returned
     "Too Many Requests" for ~40% of calls, and Yahoo rate-limited the
     whole IP for a stretch afterward) -- this stage runs at a modest
     thread count (default 6) and checkpoints every 25 symbols to
     `state/smallcap_build_cache.json` (gitignored), so a mid-run rate
     limit or crash loses no completed work; re-running the script resumes
     rather than re-fetching.

Boundary: this SCRIPT does network I/O and writes the checked-in
src/discovery/smallcap.py list; the LIST ITSELF (and everything at runtime
that reads it, via src/discovery/universe.py) is static reference data, no
I/O, no live trading decision -- same split as sp500.py/sp400.py/sp600.py.

    python -m scripts.build_smallcap_universe             # full run (resumable)
    python -m scripts.build_smallcap_universe --dry-run   # print counts only, don't write smallcap.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = PROJECT_ROOT / "state" / "smallcap_build_cache.json"
OUTPUT_PATH = PROJECT_ROOT / "src" / "discovery" / "smallcap.py"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Both floors removed 2026-08-24 at the user's explicit request (was $5.0 /
# $500,000.0 -- see module docstring stage 4 for the full history). Kept as
# named constants at > 0, not literally unconstrained, so a bad-data $0.00
# price or a never-traded ticker still can't sneak in.
MIN_PRICE = 0.0
MIN_DOLLAR_VOL_30D = 0.0
MIN_MARKET_CAP = 50_000_000.0
MAX_MARKET_CAP = 6_000_000_000.0

_EXCLUDE_NAME_RE = re.compile(
    r"\bwarrant|\bunit(s)?\b|\bright(s)?\b|\bpreferred\b|\bdepositary\b|"
    r"\bdepository\b|\bnote(s)?\b|\bbond(s)?\b|\bconvertible\b|\bperpetual\b|"
    r"when issued|acquisition corp|\btrust\b(?!.*reit)|\bspac\b|\betf\b|"
    r"\betn\b|\bfund\b|\bclosed[- ]end\b|\bordinary shares?\b|class [b-z] common",
    re.IGNORECASE,
)


def _fetch_listing(url: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(url, timeout=30) as resp:  # nosec - fixed, hardcoded public data URL
        text = resp.read().decode("utf-8")
    lines = text.splitlines()
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def _clean_common_stock(rows: list[dict[str, str]], symbol_field: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows:
        if r.get("Test Issue", "N") == "Y" or r.get("ETF", "N") == "Y":
            continue
        name = r.get("Security Name", "")
        if _EXCLUDE_NAME_RE.search(name):
            continue
        sym = r.get(symbol_field, "").strip()
        if not sym or any(c in sym for c in "$."):
            continue
        out[sym] = name
    return out


def stage1_bulk_listings() -> dict[str, str]:
    """Nasdaq + NYSE/other, ETFs/warrants/units/rights/preferred/ADRs/SPACs/
    notes/test-issues excluded by explicit column or name pattern."""
    nas = _clean_common_stock(_fetch_listing(NASDAQ_LISTED_URL), "Symbol")
    oth = _clean_common_stock(_fetch_listing(OTHER_LISTED_URL), "ACT Symbol")
    return {**nas, **oth}


def stage2_exclude_existing_universe(candidates: dict[str, str], config, *, exclude_self: str | None = None) -> list[str]:
    """Drop anything already in the discovery universe (S&P 500/400/600 +
    watchlist + extras + congress buys + smallcap.py/volatile.py, whichever
    of those two are config-enabled).

    `exclude_self` names the `discovery.universe.<flag>` key to force OFF
    while computing this exclusion set. REQUIRED when regenerating a list
    that is itself one of those flags (pass "smallcap" from
    build_smallcap_universe.py, "volatile" from build_volatile_universe.py)
    -- otherwise `discovery_universe(config)` includes that list's own
    CURRENT on-disk content (since its flag is already on from the last
    build), so every previously-included ticker gets excluded from the new
    candidate pool and silently vanishes when the file is overwritten with
    only what's left. Hit this live 2026-08-24 rebuilding both lists with
    loosened thresholds: the entire prior smallcap.py/volatile.py contents
    were dropped, including names like RIOT/CIFR/WULF that plainly still
    qualified -- caught by a well-known-names test failing after rebuild,
    not by inspection, which is exactly why that test exists.
    """
    from dataclasses import replace as dc_replace

    from src.discovery.sp400 import SP400_TICKERS
    from src.discovery.sp500 import SP500_TICKERS
    from src.discovery.sp600 import SP600_TICKERS
    from src.discovery.universe import discovery_universe

    if exclude_self:
        settings = dict(config.settings)
        discovery = dict(settings.get("discovery", {}))
        universe = dict(discovery.get("universe", {}))
        universe[exclude_self] = False
        discovery["universe"] = universe
        settings["discovery"] = discovery
        config = dc_replace(config, settings=settings)

    existing = set(discovery_universe(config)) | set(SP500_TICKERS) | set(SP400_TICKERS) | set(SP600_TICKERS)
    return sorted(s for s in candidates if s not in existing)


def stage3_alpaca_tradable(symbols: list[str]) -> list[str]:
    from src.execution.broker_alpaca import AlpacaBroker

    assets = {a.symbol: a for a in AlpacaBroker().list_assets()}
    return [s for s in symbols if s in assets and assets[s].tradable]


def stage4_liquidity_screen(symbols: list[str]) -> list[str]:
    """30-day last close > MIN_PRICE and 30-day avg dollar volume >
    MIN_DOLLAR_VOL_30D -- both are 0.0 as of 2026-08-24 (see module
    docstring), so this now only excludes bad-data/non-positive prices and
    completely untraded tickers, not a real price or liquidity floor."""
    from src.data.providers.alpaca_data import AlpacaData

    data = AlpacaData()
    survivors: list[str] = []
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        bars_by_symbol = data.get_daily_bars_multi(chunk, lookback_days=30)
        for sym, df in bars_by_symbol.items():
            if df is None or df.empty:
                continue
            last_close = float(df.iloc[-1]["close"])
            avg_dollar_vol = float((df["close"] * df["volume"]).mean())
            if last_close > MIN_PRICE and avg_dollar_vol > MIN_DOLLAR_VOL_30D:
                survivors.append(sym)
    return sorted(survivors)


def _load_cache() -> dict[str, float | None]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _save_cache(cache: dict[str, float | None]) -> None:
    from src.common.jsonio import atomic_write_json

    atomic_write_json(CACHE_PATH, cache)


def _fast_market_cap(symbol: str) -> float | None:
    """yfinance's `fast_info` is a lighter endpoint than the full `.info`
    scrape YFinanceFundamentals uses -- `.info` was ~19s/symbol serially
    here (confirmed live 2026-08-24); fast_info is sub-second. Still throttles
    under a large concurrent burst (confirmed: 16 workers hitting `.info`
    triggered a stretch of hard "Too Many Requests" errors), so this runs at
    a modest thread count, not unbounded."""
    import yfinance as yf

    try:
        return yf.Ticker(symbol).fast_info.get("marketCap")
    except Exception:
        return None


def stage5_market_cap_band(symbols: list[str], *, workers: int = 6) -> list[str]:
    """Threaded (modest concurrency) + checkpointed -- see module docstring
    for why `.info` was replaced with the lighter `fast_info`. Safe to
    re-run after a rate limit; already-cached symbols are skipped."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache = _load_cache()
    todo = [s for s in symbols if s not in cache]
    print(f"market-cap stage: {len(symbols)} candidates, {len(cache)} cached, {len(todo)} to fetch")

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fast_market_cap, s): s for s in todo}
        for fut in as_completed(futures):
            sym = futures[fut]
            cache[sym] = fut.result()
            done += 1
            if done % 25 == 0:
                _save_cache(cache)
                print(f"  {done}/{len(todo)} fetched")
    _save_cache(cache)

    return sorted(
        s for s in symbols
        if cache.get(s) is not None and MIN_MARKET_CAP <= cache[s] <= MAX_MARKET_CAP
    )


def _write_module(tickers: list[str], sourced_date: str) -> None:
    from src.common.jsonio import atomic_write_text

    lines = []
    for i in range(0, len(tickers), 10):
        row = ", ".join(f'"{t}"' for t in tickers[i:i + 10])
        lines.append(f"    {row},")
    body = "\n".join(lines)
    cap_lo = f"${MIN_MARKET_CAP / 1e6:.0f}M"
    cap_hi = f"${MAX_MARKET_CAP / 1e9:.0f}B"
    atomic_write_text(OUTPUT_PATH, f'''"""
Small/micro-cap discovery-universe tickers -- discovery layer.

NOT an index membership list (unlike sp500.py/sp400.py/sp600.py) -- there is
no free, scrapable Russell 2000 (or similar) constituent list (see
scripts/build_smallcap_universe.py's module docstring for what was tried and
why it doesn't exist as a static list here). This is instead a data-derived
screen, built by that script: Nasdaq+NYSE bulk listing files, filtered to
real common stock (no ETFs/warrants/units/rights/preferred/ADRs/SPACs),
excluding anything already in the existing discovery universe, cross-checked
against Alpaca's tradable asset catalog, price/liquidity-screened via
Alpaca's own 30-day bars, and market-cap-banded via yfinance ({cap_lo}-{cap_hi}
-- below/adjacent to S&P 600's range, not a re-hash of it).

UPDATED 2026-08-24, same day as the initial build: the price and liquidity
floors were REMOVED at the user's explicit request (originally price >= $5 /
avg dollar volume >= $500k/day, matching discovery.min_price -- see
docs/ROADMAP.md Phase H for the full request and risk disclosure). Now only
requires last close > $0 and avg dollar volume > $0 (excludes bad-data and
never-traded tickers, nothing more) -- so this list CAN and DOES include
sub-$5, thin-volume names as long as their market cap fits the band above.
The runtime `discovery.min_price` guard (config/settings.yaml) is the only
remaining price-based protection, and it is ALSO set to $0 in this
deployment -- penny-stock inclusion is deliberate here, not an oversight.

Sourced {sourced_date}. Regenerate periodically with
`python -m scripts.build_smallcap_universe` -- this is a snapshot, not a
live feed; names drift in and out of the market-cap band over time.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "{sourced_date}"

SMALLCAP_TICKERS: list[str] = [
{body}
]
''')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print counts only, don't write smallcap.py")
    parser.add_argument("--workers", type=int, default=6, help="concurrent yfinance fetches (stage 5)")
    args = parser.parse_args()

    from datetime import date, timezone
    from src.common.config import load_config

    config = load_config()

    print("Stage 1: bulk listing files...")
    raw = stage1_bulk_listings()
    print(f"  {len(raw)} clean common-stock candidates")

    print("Stage 2: excluding existing discovery universe...")
    stage2 = stage2_exclude_existing_universe(raw, config, exclude_self="smallcap")
    print(f"  {len(stage2)} remain")

    print("Stage 3: Alpaca tradability...")
    stage3 = stage3_alpaca_tradable(stage2)
    print(f"  {len(stage3)} remain")

    print("Stage 4: Alpaca price/liquidity screen...")
    stage4 = stage4_liquidity_screen(stage3)
    print(f"  {len(stage4)} remain")

    print("Stage 5: yfinance market-cap band (threaded, checkpointed)...")
    final = stage5_market_cap_band(stage4, workers=args.workers)
    print(f"  {len(final)} final tickers")

    if args.dry_run:
        print("\n--dry-run: not writing src/discovery/smallcap.py")
        return

    _write_module(final, date.today().isoformat())
    print(f"\nWrote {len(final)} tickers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
