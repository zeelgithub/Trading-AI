"""
Build the "fluctuating / volatile" discovery-universe ticker list (RESEARCH/
OFFLINE TOOL -- not run automatically by the bot; regenerate periodically,
same expectation as sp500.py/sp400.py/sp600.py/smallcap.py).

Added 2026-08-24, same session as smallcap.py, direct follow-up to a gap that
build spotlighted rather than closed: `src/discovery/smallcap.py` only adds
names in a $50M-$6B market-cap band, so a genuinely volatile name OUTSIDE
that band -- either because it's grown past $6B without an index seat yet
(recent IPO, pre-reconstitution), or it's a well-known high-beta name that
simply isn't in S&P 500/400/600 -- fell through both that screen and the
S&P lists. Confirmed live 2026-08-24: re-running smallcap.py's own stage1-3
(bulk listings -> real-common-stock filter -> exclude existing universe ->
Alpaca tradability) and screening what was LEFT (i.e. still not in the
universe after smallcap.py) by realized volatility instead of market cap
surfaced RIOT, CIFR, WULF, HUT (bitcoin miners), RKLB, ASTS, OKLO, QBTS, BE,
ALAB, CRWV -- all real, well-known, genuinely volatile names, several of
them multi-billion-dollar companies, none of them penny stocks or junk.

Reuses smallcap.py's stage1/stage2/stage3 (see scripts/build_smallcap_universe.py
for that logic) so this list can never duplicate a symbol that's already in
S&P 500/400/600, smallcap.py, the watchlist, or the configured extras -- it
only ever ADDS to those. Stage 4 here is volatility-first instead of
market-cap-first:

  4. Alpaca 90-day daily bars (free, market-data creds, batched) -> Wilder's
     ATR(14) via `src.data.indicators.atr` -- the SAME function
     src/data/features.py and src/discovery/sources/volatility.py use, so
     "volatile" means the same thing here as it does everywhere else in this
     codebase, not a bespoke metric. Keep symbols with:
       - last close > $0 and 90-day avg dollar volume > $0 (excludes bad-data
         and never-traded tickers only -- NOT a price or liquidity floor;
         both were REMOVED 2026-08-24, same day, at the user's explicit
         request, reusing smallcap.py's MIN_PRICE/MIN_DOLLAR_VOL_30D
         constants, which were relaxed the same way -- see
         build_smallcap_universe.py's docstring and docs/ROADMAP.md Phase H
         for the full request and risk disclosure). Originally >= $5 /
         >= $500k/day.
       - ATR as % of price >= 2.0% (lowered same day from 3.0%) -- still
         above a calm large-cap's typical ATR% (AAPL/MSFT run ~1.5-2.5%),
         so this remains "somewhat more volatile than the market," not zero
         bar, but is now meaningfully more inclusive than the original cut.

No yfinance step at all -- unlike smallcap.py, this needs no market-cap
lookup, so it's immune to the rate-limit issues documented in
build_smallcap_universe.py; everything here comes from Alpaca's own
market-data feed, already used throughout src/data/.

Boundary: this SCRIPT does network I/O and writes the checked-in
src/discovery/volatile.py list; the LIST ITSELF (and everything at runtime
that reads it, via src/discovery/universe.py) is static reference data, no
I/O, no live trading decision -- same split as the other universe-widening
lists.

    python -m scripts.build_volatile_universe             # full run
    python -m scripts.build_volatile_universe --dry-run   # print counts only, don't write volatile.py
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from scripts.build_smallcap_universe import (
    MIN_DOLLAR_VOL_30D,
    MIN_PRICE,
    stage1_bulk_listings,
    stage2_exclude_existing_universe,
    stage3_alpaca_tradable,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "src" / "discovery" / "volatile.py"

MIN_ATR_PCT = 0.02  # 2%, lowered 2026-08-24 from 3.0% at the user's explicit
                    # request -- see module docstring for the history


def stage4_volatility_screen(symbols: list[str], *, lookback_days: int = 90) -> list[str]:
    from src.data.indicators import atr
    from src.data.providers.alpaca_data import AlpacaData

    data = AlpacaData()
    survivors: list[str] = []
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        bars_by_symbol = data.get_daily_bars_multi(chunk, lookback_days=lookback_days)
        for sym, df in bars_by_symbol.items():
            if df is None or len(df) < 20:
                continue
            last_close = float(df["close"].iloc[-1])
            avg_dollar_vol = float((df["close"] * df["volume"]).mean())
            if last_close <= MIN_PRICE or avg_dollar_vol <= MIN_DOLLAR_VOL_30D:
                continue
            atr_last = atr(df["high"], df["low"], df["close"], period=14).iloc[-1]
            if atr_last != atr_last:  # NaN -- not enough history yet
                continue
            if (float(atr_last) / last_close) >= MIN_ATR_PCT:
                survivors.append(sym)
    return sorted(survivors)


def _write_module(tickers: list[str], sourced_date: str) -> None:
    from src.common.jsonio import atomic_write_text

    lines = []
    for i in range(0, len(tickers), 10):
        row = ", ".join(f'"{t}"' for t in tickers[i:i + 10])
        lines.append(f"    {row},")
    body = "\n".join(lines)
    atomic_write_text(OUTPUT_PATH, f'''"""
Fluctuating/volatile discovery-universe tickers -- discovery layer.

NOT an index membership list and NOT a re-rank of the existing universe
(that's src/discovery/sources/volatility.py's job) -- this is a data-derived
screen for genuinely volatile names that fall OUTSIDE every other list:
too large for smallcap.py's $50M-$6B market-cap band (a recent IPO, a name
that's grown past $6B without an index seat yet) or simply not yet in
S&P 500/400/600. Built by scripts/build_volatile_universe.py -- see that
module's docstring for the full methodology: reuses smallcap.py's
existing-universe exclusion (so this never duplicates another list), then
screens what's left by Wilder's ATR(14) as a percentage of price (the same
metric src/discovery/sources/volatility.py re-ranks by) -- price > $0 and
90-day avg dollar volume > $0 (no real floor, only excludes bad-data/
never-traded names; originally >= $5 / >= $500,000/day, loosened 2026-08-24
at the user's explicit request -- see docs/ROADMAP.md Phase H), and
ATR% >= 2.0% (lowered same day from 3.0%).

Sourced {sourced_date}. Regenerate periodically with
`python -m scripts.build_volatile_universe` -- realized volatility drifts;
this is a snapshot, not a live feed.

Boundary: static reference data, no I/O, no live trading decision.
"""

from __future__ import annotations

SOURCED_DATE = "{sourced_date}"

VOLATILE_TICKERS: list[str] = [
{body}
]
''')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print counts only, don't write volatile.py")
    args = parser.parse_args()

    from src.common.config import load_config

    config = load_config()

    print("Stage 1: bulk listing files...")
    raw = stage1_bulk_listings()
    print(f"  {len(raw)} clean common-stock candidates")

    print("Stage 2: excluding existing discovery universe (incl. smallcap.py)...")
    stage2 = stage2_exclude_existing_universe(raw, config, exclude_self="volatile")
    print(f"  {len(stage2)} remain")

    print("Stage 3: Alpaca tradability...")
    stage3 = stage3_alpaca_tradable(stage2)
    print(f"  {len(stage3)} remain")

    print("Stage 4: Alpaca price/liquidity + ATR% volatility screen...")
    final = stage4_volatility_screen(stage3)
    print(f"  {len(final)} final tickers")

    if args.dry_run:
        print("\n--dry-run: not writing src/discovery/volatile.py")
        return

    _write_module(final, date.today().isoformat())
    print(f"\nWrote {len(final)} tickers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
