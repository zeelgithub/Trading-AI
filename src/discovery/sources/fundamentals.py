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

_MIN_REAL_CAP = 2_000_000_000.0   # below this we treat size as a (small) risk


@dataclass
class FundamentalsSource:
    provider: FundamentalsProvider
    universe: list[str]
    name: str = "fundamentals"

    def gather(self) -> list[SignalContribution]:
        out: list[SignalContribution] = []
        for symbol in dict.fromkeys(s.upper() for s in self.universe):
            try:
                f = self.provider.fetch(symbol)
            except Exception:
                continue
            if not f.has_data:
                continue
            out.append(_score(f, self.name))
        return out


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
