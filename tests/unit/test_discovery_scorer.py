"""Scorer: weighted blend, confluence, active-source renormalisation."""

from __future__ import annotations

from src.discovery.candidate import Candidate, SignalContribution
from src.discovery.scorer import Scorer


def _cand(symbol, *contribs):
    c = Candidate(symbol=symbol)
    for source, score in contribs:
        c.contributions.append(SignalContribution(symbol, source, score, f"{source} reason"))
    return c


def test_single_source_renormalised_to_active_weight():
    # Only congress+technical active (Phase A). One source at 0.8 -> 40/100.
    scorer = Scorer(active_sources=frozenset({"congress", "technical"}))
    assert scorer.score(_cand("AAA", ("congress", 0.8))) == 40.0


def test_confluence_outscores_single_source():
    scorer = Scorer(active_sources=frozenset({"congress", "technical"}))
    one = scorer.score(_cand("AAA", ("congress", 0.8)))
    both = scorer.score(_cand("BBB", ("congress", 0.8), ("technical", 0.8)))
    assert both > one
    assert both == 80.0  # equal weights, both maxed proportionally


def test_inactive_source_ignored():
    scorer = Scorer(active_sources=frozenset({"congress"}))
    # A technical contribution must not move the score when technical is off.
    assert scorer.score(_cand("AAA", ("congress", 0.5), ("technical", 1.0))) == 50.0


def test_best_contribution_per_source_wins():
    scorer = Scorer(active_sources=frozenset({"congress"}))
    c = _cand("AAA", ("congress", 0.3), ("congress", 0.9))
    assert scorer.score(c) == 90.0


def test_no_active_weight_scores_zero():
    assert Scorer(active_sources=frozenset()).score(_cand("AAA", ("congress", 1.0))) == 0.0


def test_from_config_reads_enabled_sources():
    from src.common.config import load_config

    scorer = Scorer.from_config(load_config())
    # technical/news/volatility on (news enabled 2026-08-22, volatility
    # 2026-08-24 -- fail soft per-symbol, no new credentials needed);
    # fundamentals enabled 2026-08-22 then disabled again 2026-08-25 (yfinance
    # throughput doesn't scale to the ~4,000-symbol universe -- see
    # docs/ROADMAP.md Phase J); social off by default (needs Reddit creds);
    # congress stays off (no ingestion ships in this repo -- see
    # congress_copy/README.md "Scheduled ingestion").
    assert scorer.active_sources == frozenset({"technical", "news", "volatility"})


def test_stars_track_score():
    c = Candidate(symbol="AAA")
    for score, stars in ((85, 5), (65, 4), (45, 3), (25, 2), (5, 1)):
        c.score = score
        assert c.stars == stars
