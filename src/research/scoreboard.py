"""
Strategy scoreboard -- research layer.

The persisted source of truth for how each strategy is performing: its
out-of-sample backtest statistics, a significance-based verdict, and live
attribution accumulated by the running bot. The future strategy-analyst agent
reads this to rank strategies and PROPOSE rotation -- nothing here ever places
an order or changes what runs.

Verdict thresholds are deliberately conservative defaults (a money loop should
err toward "noise"); they are arguments so they can be tuned in config later.

Boundary: read/write JSON state; places orders NO.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "state" / "scoreboard.json"

VERDICTS = ("validated", "promising", "inconclusive", "noise")
VERDICT_RANK = {"validated": 3, "promising": 2, "inconclusive": 1, "noise": 0}

# JSON has no infinity; profit_factor of a loss-free strategy is clamped to
# this. Public (not _-prefixed): evaluation.py and walkforward.py both need
# the SAME clamp value to agree with what this module persists, previously
# two independent copies of this literal.
PF_CLAMP = 999.0


@dataclass
class StrategyScore:
    strategy: str
    num_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    sharpe: float = 0.0                 # per-trade Sharpe (see significance.trade_sharpe)
    bootstrap_p_value: float = 1.0      # raw one-sided bootstrap p
    p_value_adjusted: float = 1.0       # after Sidak multiple-testing correction
    psr: float = 0.0                    # probabilistic Sharpe ratio
    consistency: float | None = None    # fraction of time-buckets net profitable
    verdict: str = "inconclusive"
    updated: str = ""
    # live attribution -- accumulated by the running bot as real trades close.
    live_num_trades: int = 0
    live_total_pnl: float = 0.0


def classify(
    num_trades: int,
    p_value: float,
    psr: float,
    consistency: float | None,
    *,
    min_trades: int = 30,
    min_consistency: float = 0.6,
) -> str:
    """Map evidence to a verdict. `p_value` should already be multiple-testing
    adjusted. Order matters: rule out noise first, only then promote."""
    if num_trades < 10:
        return "noise"                       # not enough trades to claim anything
    if p_value > 0.10 or psr <= 0.0:
        return "noise"                       # indistinguishable from luck
    consistent = consistency is None or consistency >= min_consistency
    if p_value < 0.05 and psr >= 0.95 and num_trades >= min_trades and consistent:
        return "validated"
    if p_value < 0.05:
        return "promising"      # significant, but missing a "validated" criterion
    return "inconclusive"       # marginal (0.05 <= p <= 0.10)


class Scoreboard:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, StrategyScore]:
        raw, quarantined = load_json_or_quarantine(self.path)
        if quarantined is not None:
            get_logger("scoreboard").error(
                "scoreboard file corrupt; moved to %s -- reverting to empty", quarantined)
            return {}
        return {k: StrategyScore(**v) for k, v in (raw or {}).items()}

    def save(self, scores: dict[str, StrategyScore]) -> None:
        payload = {k: _sanitize(asdict(v)) for k, v in scores.items()}
        atomic_write_json(self.path, payload)

    def upsert(self, score: StrategyScore) -> None:
        scores = self.load()
        # Preserve accumulated live attribution across re-evaluations.
        existing = scores.get(score.strategy)
        if existing is not None:
            score.live_num_trades = existing.live_num_trades
            score.live_total_pnl = existing.live_total_pnl
        scores[score.strategy] = score
        self.save(scores)

    def record_live_trade(self, strategy: str, pnl: float) -> None:
        """Accumulate a real, closed trade's PnL against its strategy. Called by
        the running bot so the scoreboard reflects live performance, not just the
        backtest."""
        scores = self.load()
        s = scores.get(strategy) or StrategyScore(strategy=strategy)
        s.live_num_trades += 1
        s.live_total_pnl = round(s.live_total_pnl + float(pnl), 2)
        s.updated = datetime.now(timezone.utc).isoformat()
        scores[strategy] = s
        self.save(scores)

    def rank(self) -> list[StrategyScore]:
        """Best strategies first: verdict, then PSR, then total PnL."""
        return sorted(
            self.load().values(),
            key=lambda s: (VERDICT_RANK.get(s.verdict, 0), s.psr, s.total_pnl),
            reverse=True,
        )


def _sanitize(d: dict) -> dict:
    """Clamp non-finite floats so the JSON is valid and reloadable."""
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and not math.isfinite(v):
            out[k] = PF_CLAMP if v > 0 else 0.0
        else:
            out[k] = v
    return out
