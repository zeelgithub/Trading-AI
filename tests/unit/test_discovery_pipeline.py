"""Pipeline: group -> score -> drop held/low-score -> rank -> risk-gate top N
-> Proposal. Uses a fake source + the real Scorer/RiskManager (offline)."""

from __future__ import annotations

from types import SimpleNamespace

from src.common.config import load_config
from src.discovery.candidate import SignalContribution
from src.discovery.pipeline import Account, DiscoveryPipeline
from src.discovery.scorer import Scorer
from src.execution.order_manager import PositionStatus
from src.risk.risk_manager import RiskManager

ACCOUNT = Account(equity=100_000.0, last_equity=100_000.0, buying_power=200_000.0)


class FakeSource:
    name = "fake"

    def __init__(self, contributions):
        self._contributions = contributions

    def gather(self):
        return list(self._contributions)


def _tech(symbol, score, *, entry, stop, strategy="trend_following"):
    return SignalContribution(symbol, "technical", score, f"{strategy} setup",
                              meta={"strategy": strategy, "entry_price": entry,
                                    "stop_loss": stop, "atr": None})


def _congress(symbol, score):
    return SignalContribution(symbol, "congress", score, "Congress bought")


def _held_pos():
    return SimpleNamespace(status=PositionStatus.OPEN, filled_qty=10, qty=10,
                           ratchet=SimpleNamespace(entry=50.0, stop=45.0))


def _pipeline(contributions, *, top_n=2, min_score=25.0):
    return DiscoveryPipeline(
        sources=[FakeSource(contributions)],
        scorer=Scorer(active_sources=frozenset({"congress", "technical"})),
        risk=RiskManager(load_config()),
        config=load_config(),
        price_fn=lambda s: 100.0,        # for congress-only candidates
        top_n=top_n, min_score=min_score,
    )


def _mixed():
    return [
        _congress("BBB", 0.8), _tech("BBB", 0.8, entry=200.0, stop=180.0),  # 80
        _congress("AAA", 0.8),                                              # 40, congress-only
        _congress("CCC", 0.1),                                              # 5  -> below floor
    ]


def test_ranks_scores_and_proposes_top_n():
    report = _pipeline(_mixed()).run(ACCOUNT, {})
    assert report.screened == 3
    assert [c.symbol for c in report.candidates] == ["BBB", "AAA"]   # CCC dropped, ranked desc
    assert [p.symbol for p in report.proposals] == ["BBB", "AAA"]


def test_proposal_levels_and_strategy():
    report = _pipeline(_mixed()).run(ACCOUNT, {})
    bbb = next(p for p in report.proposals if p.symbol == "BBB")
    aaa = next(p for p in report.proposals if p.symbol == "AAA")
    assert bbb.strategy == "trend_following"
    assert bbb.intent["entry_price"] == 200.0 and bbb.intent["stop_loss"] == 180.0
    # Congress-only: priced live, default 10% stop, congress_copy strategy.
    assert aaa.strategy == "congress_copy"
    assert aaa.intent["entry_price"] == 100.0 and aaa.intent["stop_loss"] == 90.0
    assert bbb.approved_qty > 0 and aaa.approved_qty > 0


def test_held_symbols_excluded():
    report = _pipeline(_mixed()).run(ACCOUNT, {"AAA": _held_pos()})
    assert "AAA" not in [c.symbol for c in report.candidates]
    assert "AAA" not in [p.symbol for p in report.proposals]


def test_exclude_set_blocks_pending():
    report = _pipeline(_mixed()).run(ACCOUNT, {}, exclude={"bbb"})
    assert "BBB" not in [p.symbol for p in report.proposals]


def test_top_n_caps_proposals():
    report = _pipeline(_mixed(), top_n=1).run(ACCOUNT, {})
    assert len(report.proposals) == 1 and report.proposals[0].symbol == "BBB"
    assert len(report.candidates) == 2   # both still scored, only one proposed


def test_invalid_stop_is_skipped():
    # entry == stop -> not a valid long; must be skipped, not proposed.
    report = _pipeline([_tech("DDD", 0.8, entry=100.0, stop=100.0)]).run(ACCOUNT, {})
    assert report.proposals == []
    assert any(sym == "DDD" for sym, _ in report.skipped)
