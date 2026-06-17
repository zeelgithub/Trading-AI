"""
Congress source -- discovery layer.

Turns congressional disclosures (CapitolTrades rows written to disclosures.json
by the scheduled Chrome ingestion job) into buy candidates. This is what lets a
*new* ticker -- one not on your watchlist -- surface as an idea.

Reality check baked into the score: STOCK Act filings lag the actual trade by
~30-45 days, so a fresher disclosure is worth more and we expose `lag_days` on
every idea. Equities only (the bot has no options support). Reads the same
JSON + model as `congress_copy`; it does not re-scrape anything.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from congress_copy.models import DisclosedTrade
from congress_copy.providers import DisclosureProvider
from src.discovery.candidate import SignalContribution


@dataclass
class CongressSource:
    name: str = "congress"
    provider: DisclosureProvider | None = None
    politicians: tuple[str, ...] = ()      # empty => any member in the file
    max_age_days: int = 45
    as_of: date | None = None
    _trades: list[DisclosedTrade] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        # Allow direct injection of trades (tests) without a provider.
        if self.provider is not None:
            self._trades = self.provider.fetch()

    def gather(self) -> list[SignalContribution]:
        as_of = self.as_of or date.today()
        allow = {p.lower() for p in self.politicians}

        # Group recent stock BUYs by ticker so multiple buyers compound.
        per_ticker: dict[str, list[tuple[DisclosedTrade, int]]] = {}
        for t in self._trades:
            if not t.is_stock or not t.is_buy:
                continue
            if allow and t.politician.lower() not in allow:
                continue
            age = (as_of - t.disclosure_date).days
            if age < 0 or age > self.max_age_days:
                continue
            per_ticker.setdefault(t.ticker.upper(), []).append((t, age))

        out: list[SignalContribution] = []
        for ticker, items in per_ticker.items():
            items.sort(key=lambda x: x[1])          # freshest disclosure first
            lead, lead_age = items[0]
            n_buyers = len({t.politician for t, _ in items})

            freshness = max(0.0, 1.0 - lead_age / self.max_age_days)
            buyer_bonus = min(n_buyers - 1, 2) / 2.0   # 0, 0.5, 1.0
            # A high baseline (0.55) keeps even an older, still-eligible
            # disclosure above the surfacing floor: the 30-45d lag is shown on
            # the idea, never used to bury an otherwise-relevant name. Freshness
            # and extra buyers only *raise* it from there.
            score = min(1.0, 0.55 + 0.30 * freshness + 0.15 * buyer_bonus)

            extra = ""
            if n_buyers > 1:
                extra = f" (+{n_buyers - 1} more buyer{'s' if n_buyers - 1 > 1 else ''})"
            reason = (
                f"Congress: {lead.politician} bought {_amount(lead)}, "
                f"filed {lead_age}d ago{extra}"
            )
            out.append(SignalContribution(
                symbol=ticker, source=self.name, score=score, reason=reason,
                meta={
                    "politician": lead.politician,
                    "n_buyers": n_buyers,
                    "lag_days": lead.disclosure_lag_days,
                    "disclosure_age_days": lead_age,
                },
            ))
        return out


def _amount(t: DisclosedTrade) -> str:
    if t.amount_low is None and t.amount_high is None:
        return "an undisclosed amount"
    if t.amount_high is None:
        return f"${_k(t.amount_low)}+"
    return f"${_k(t.amount_low)}-{_k(t.amount_high)}"


def _k(v: float | None) -> str:
    if v is None:
        return "?"
    return f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}"
