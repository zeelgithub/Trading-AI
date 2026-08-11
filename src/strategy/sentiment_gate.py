"""
Sentiment gate -- strategy layer (NON-EXECUTING).

Scores headlines -1 / 0 / +1 and gates intents: longs require score >= 0,
shorts require score <= 0. A confirming score leaves confidence intact; a merely
neutral score reduces it; an opposing score blocks the trade entirely. The AI
NEVER originates or triggers a trade -- it can only veto or shrink one.

The scorer is injected (a callable symbol -> int). With no scorer wired, every
symbol scores 0 (neutral) so the gate is a safe no-op pass-through with a small
confidence haircut.

`on_feed_unavailable: skip_gate` (config/strategies.yaml): if a wired scorer
RAISES (network/API error, not just "no scorer configured"), the gate passes the
intent through completely unchanged for that symbol -- no haircut, no block. A
transient sentiment-feed outage must never block trading or count toward the
cycle's consecutive-error breaker the way a real logic error should; that is a
materially different failure ("couldn't ask") from a genuine neutral score
("asked, no signal either way").

Boundary: places orders NO, can only veto/shrink, never originate.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from src.common.config import Config
from src.common.logging import get_logger
from src.common.models import Intent, Side

_NEUTRAL_HAIRCUT = 0.8

log = get_logger("sentiment_gate")


class SentimentGate:
    def __init__(self, config: Config, scorer: Callable[[str], int] | None = None) -> None:
        sg = config.strategies.get("sentiment_gate", {})
        self.enabled = bool(sg.get("enabled", True))
        rules = sg.get("rules", {})
        self.long_requires_min = int(rules.get("long_requires_min", 0))
        self.short_requires_max = int(rules.get("short_requires_max", 0))
        self._scorer = scorer

    def score(self, symbol: str) -> int:
        """Raises if a wired scorer fails -- see apply()'s on_feed_unavailable
        handling, which is where that distinction actually matters."""
        if self._scorer is None:
            return 0
        return max(-1, min(1, int(self._scorer(symbol))))

    def apply(self, intent: Intent) -> Intent | None:
        """Return the (possibly confidence-adjusted) intent, or None if blocked."""
        if not self.enabled:
            return intent
        try:
            s = self.score(intent.symbol)
        except Exception as exc:
            log.warning(
                "sentiment scorer failed for %s (%s) -- on_feed_unavailable=skip_gate, "
                "passing the intent through unchanged this cycle", intent.symbol, exc,
            )
            return intent

        if intent.side == Side.LONG and s < self.long_requires_min:
            return None
        if intent.side == Side.SHORT and s > self.short_requires_max:
            return None

        confirms = (intent.side == Side.LONG and s > 0) or (intent.side == Side.SHORT and s < 0)
        if confirms:
            return intent
        return replace(intent, confidence=round(intent.confidence * _NEUTRAL_HAIRCUT, 4))
