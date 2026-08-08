"""Technical source: emits a contribution when a routed strategy fires, blends
the strategy confidence with the scoreboard verdict, and carries entry/stop in
meta. Regime + strategies are injected so the test needs no real indicators."""

from __future__ import annotations

import pandas as pd

from src.common.config import load_config
from src.common.models import Intent, Regime, Side
from src.discovery.sources.technical import TechnicalSource
from src.research.scoreboard import Scoreboard, StrategyScore


class FakeRegime:
    def active_strategy(self, feats):
        return "trend_following"

    def classify(self, feats):
        return Regime.TRENDING


class FakeStrategy:
    def __init__(self, intent):
        self._intent = intent

    def generate(self, symbol, feats):
        return self._intent


def _feats():
    return pd.DataFrame([{"close": 100.0, "rsi": 58.0, "atr": 2.5}])


def _source(intent, scoreboard=None):
    src = TechnicalSource(
        config=load_config(),
        feature_provider=lambda sym: _feats(),
        universe=["NVDA"],
        scoreboard=scoreboard,
    )
    src._regime = FakeRegime()
    src._strategies = {"trend_following": FakeStrategy(intent)}
    return src


def _intent():
    return Intent(symbol="NVDA", strategy="trend_following", side=Side.LONG,
                  confidence=0.6, entry_price=100.0, stop_loss=90.0)


def test_emits_contribution_with_levels_in_meta():
    out = _source(_intent()).gather()
    assert len(out) == 1
    c = out[0]
    assert c.symbol == "NVDA" and c.source == "technical"
    assert c.meta["strategy"] == "trend_following"
    assert c.meta["entry_price"] == 100.0 and c.meta["stop_loss"] == 90.0
    assert "RSI 58" in c.reason and "trend_following" in c.reason


def test_no_intent_means_no_contribution():
    src = _source(None)
    src._strategies = {"trend_following": FakeStrategy(None)}
    assert src.gather() == []


def test_noise_verdict_discounts_score(tmp_path):
    # Both boards are explicitly isolated (tmp_path): relying on the default
    # Scoreboard() pointing at the real state/scoreboard.json broke this test
    # the moment a real `evaluate_strategies` run gave trend_following an
    # actual verdict there -- the "empty board" case must be a genuinely
    # empty board, not whatever happens to be in the live scoreboard today.
    noisy_board = Scoreboard(tmp_path / "noisy.json")
    noisy_board.upsert(StrategyScore(strategy="trend_following", verdict="noise"))
    empty_board = Scoreboard(tmp_path / "empty.json")
    noisy = _source(_intent(), scoreboard=noisy_board).gather()[0]
    neutral = _source(_intent(), scoreboard=empty_board).gather()[0]  # "inconclusive"
    assert noisy.score < neutral.score
