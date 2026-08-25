"""
Fundamentals source -- discovery layer.

Turns the coarse yfinance snapshot into a 0-1 quality score. Acts as a quality
lens on the ranking: profitable, growing, real-sized companies score higher;
unprofitable / shrinking ones are pulled down (this source votes on every name
it has data for, so it can penalise, not just boost). With a modest config
weight it nudges the order without overriding a strong congress/technical read.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.providers.fundamentals import Fundamentals, FundamentalsProvider
from src.discovery.candidate import SignalContribution
from src.discovery.sources._util import fetch_concurrent, safe_call

_MIN_REAL_CAP = 2_000_000_000.0   # below this we treat size as a (small) risk


@dataclass
class FundamentalsSource:
    provider: FundamentalsProvider
    universe: list[str]
    name: str = "fundamentals"
    max_workers: int = 16

    def gather(self) -> list[SignalContribution]:
        """Threaded: yfinance's `.info` (what YFinanceFundamentals.fetch uses)
        is slow and network-bound -- confirmed live 2026-08-25 at ~19s/symbol
        serially. At this project's current ~4,000-symbol discovery universe
        that's 20+ hours serial. 8 workers (tried first) still left a full
        cycle taking 30+ minutes -- `.info` (unlike the lighter `fast_info`
        used in scripts/build_smallcap_universe.py, which lacks the
        profitability/growth fields this source needs) is heavy enough that
        even 8-way concurrency doesn't clear it fast. Raised to 16 (the
        level confirmed live to finish 815 symbols in ~12s, at the cost of
        ~40% of calls hitting "Too Many Requests" under that load) --
        deliberately trading some data completeness for speed, which this
        source already tolerates: a rate-limited symbol just fails soft
        (withholds its vote for that name) exactly like a missing-data one
        always has."""
        symbols = list(dict.fromkeys(s.upper() for s in self.universe))
        return fetch_concurrent(symbols, self._fetch_one, max_workers=self.max_workers)

    def _fetch_one(self, symbol: str) -> SignalContribution | None:
        f = safe_call(self.provider.fetch, symbol)
        if f is None or not f.has_data:
            return None
        return _score(f, self.name)


def _score(f: Fundamentals, source_name: str) -> SignalContribution:
    score = 0.3
    bits: list[str] = []

    if f.profitable is True:
        score += 0.3
        bits.append("profitable")
    elif f.profitable is False:
        score -= 0.2
        bits.append("unprofitable")

    if f.revenue_growth is not None:
        g = f.revenue_growth
        if g > 0:
            score += min(g, 0.3) / 0.3 * 0.3
            bits.append(f"rev {g * 100:+.0f}% YoY")
        else:
            score -= 0.15
            bits.append(f"rev {g * 100:+.0f}% YoY")

    if f.market_cap is not None and f.market_cap >= _MIN_REAL_CAP:
        score += 0.1
    elif f.market_cap is not None:
        score -= 0.1
        bits.append("small cap")

    score = max(0.0, min(1.0, score))
    reason = "Fundamentals: " + (", ".join(bits) if bits else "mixed")
    return SignalContribution(symbol=f.symbol, source=source_name, score=score, reason=reason,
                              meta={"profitable": f.profitable, "revenue_growth": f.revenue_growth,
                                    "market_cap": f.market_cap})
