"""Tests for the strategy scoreboard and verdict classifier."""

from __future__ import annotations

from src.research.scoreboard import Scoreboard, StrategyScore, classify

# --- classify (verdict thresholds) ---

def test_classify_noise_too_few_trades():
    assert classify(5, 0.01, 0.99, 0.8) == "noise"


def test_classify_noise_high_pvalue():
    assert classify(50, 0.20, 0.99, 0.8) == "noise"


def test_classify_noise_nonpositive_psr():
    assert classify(50, 0.01, 0.0, 0.8) == "noise"


def test_classify_validated():
    assert classify(40, 0.01, 0.97, 0.7) == "validated"


def test_classify_validated_allows_missing_consistency():
    assert classify(40, 0.01, 0.97, None) == "validated"


def test_classify_promising_when_significant_but_weak_psr():
    assert classify(40, 0.01, 0.80, 0.7) == "promising"


def test_classify_promising_when_inconsistent():
    assert classify(40, 0.01, 0.97, 0.4) == "promising"


def test_classify_inconclusive_marginal_pvalue():
    assert classify(40, 0.07, 0.97, 0.7) == "inconclusive"


# --- persistence + ranking ---

def test_scoreboard_roundtrip_and_rank(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    sb.upsert(StrategyScore(strategy="a", verdict="validated", psr=0.96, total_pnl=500, num_trades=40))
    sb.upsert(StrategyScore(strategy="b", verdict="noise", psr=0.20, total_pnl=-100, num_trades=5))
    sb.upsert(StrategyScore(strategy="c", verdict="promising", psr=0.80, total_pnl=200, num_trades=30))

    loaded = sb.load()
    assert set(loaded) == {"a", "b", "c"}
    assert [s.strategy for s in sb.rank()] == ["a", "c", "b"]


def test_scoreboard_preserves_live_attribution_on_reeval(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    sb.upsert(StrategyScore(strategy="a", total_pnl=100))
    sb.record_live_trade("a", 25.0)
    sb.record_live_trade("a", -10.0)

    # A fresh backtest re-evaluation must NOT wipe accumulated live numbers.
    sb.upsert(StrategyScore(strategy="a", total_pnl=999))
    a = sb.load()["a"]
    assert a.live_num_trades == 2
    assert a.live_total_pnl == 15.0
    assert a.total_pnl == 999


def test_scoreboard_clamps_infinite_profit_factor(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    sb.upsert(StrategyScore(strategy="a", profit_factor=float("inf")))
    assert sb.load()["a"].profit_factor == 999.0


def test_scoreboard_corrupt_file_is_quarantined_not_crashed(tmp_path):
    """Regression guard: save()/load() used to bypass this codebase's
    established atomic-write/quarantine pattern (unlike every sibling state
    file -- proposals, rotation, weight_advisor, equity_history) -- a crash
    mid-write left a truncated scoreboard.json that load() would raise
    json.JSONDecodeError on, uncaught, instead of recovering."""
    path = tmp_path / "sb.json"
    path.write_text('{"a": {"strategy": "a"', encoding="utf-8")  # truncated

    loaded = Scoreboard(path).load()

    assert loaded == {}
    assert not path.exists()  # moved aside, not left in place
    assert list(tmp_path.glob("sb.json.corrupt-*"))


def test_scoreboard_save_writes_atomically_no_tmp_litter(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    sb.upsert(StrategyScore(strategy="a"))
    assert not list(tmp_path.glob("*.tmp"))
