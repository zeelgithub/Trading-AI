"""
Technical source -- discovery layer.

Runs the existing regime filter + routed strategy (trend / mean-reversion /
breakout) over a universe and emits a contribution for every symbol that
produces an entry Intent. The raw score blends the strategy's own confidence
with how trustworthy that strategy currently is on the scoreboard, so an
unproven ("noise") strategy is discounted automatically.

The strategy's own entry/stop levels travel in `meta` so the pipeline can size
the suggestion against the strategy's plan rather than a generic stop.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from src.common.config import Config
from src.discovery.candidate import SignalContribution
from src.research.scoreboard import Scoreboard
from src.strategy.regime_filter import RegimeFilter
from src.strategy.registry import build_strategies

FeatureProvider = Callable[[str], pd.DataFrame]

# How far to trust a routing strategy given its current scoreboard verdict.
_VERDICT_FACTOR = {"validated": 1.0, "promising": 0.9, "inconclusive": 0.7, "noise": 0.45}


@dataclass
class TechnicalSource:
    config: Config
    feature_provider: FeatureProvider
    universe: list[str]
    scoreboard: Scoreboard | None = None
    name: str = "technical"
    _strategies: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._regime = RegimeFilter(self.config)
        self._strategies = build_strategies(self.config)

    def gather(self) -> list[SignalContribution]:
        board = (self.scoreboard or Scoreboard()).load()
        out: list[SignalContribution] = []
        for symbol in dict.fromkeys(s.upper() for s in self.universe):
            try:
                feats = self.feature_provider(symbol)
            except Exception:
                continue
            if feats is None or feats.empty:
                continue
            active = self._regime.active_strategy(feats)
            if active is None or active not in self._strategies:
                continue
            intent = self._strategies[active].generate(symbol, feats)
            if intent is None:
                continue

            verdict = board[active].verdict if active in board else "inconclusive"
            base = 0.5 + 0.5 * float(intent.confidence)
            score = min(1.0, base * _VERDICT_FACTOR.get(verdict, 0.7))

            last = feats.iloc[-1]
            regime = self._regime.classify(feats).value
            rsi = _num(last, "rsi")
            reason = f"Technical: {active} setup ({intent.side.value}), regime {regime}"
            if rsi is not None:
                reason += f", RSI {rsi:.0f}"

            out.append(SignalContribution(
                symbol=symbol, source=self.name, score=score, reason=reason,
                meta={
                    "strategy": active,
                    "verdict": verdict,
                    "regime": regime,
                    "confidence": float(intent.confidence),
                    "entry_price": intent.entry_price if intent.entry_price else float(last.close),
                    "stop_loss": intent.stop_loss,
                    "atr": _num(last, "atr"),
                },
            ))
        return out


def _num(row: pd.Series, col: str) -> float | None:
    if col not in row or pd.isna(row[col]):
        return None
    return float(row[col])
