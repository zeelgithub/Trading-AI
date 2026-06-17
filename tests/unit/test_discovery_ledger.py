"""Discovery ledger: append surfaced ideas, summarise per-source contribution."""

from __future__ import annotations

from types import SimpleNamespace

from src.discovery.ledger import DiscoveryLedger


class _Cand:
    """Minimal stand-in exposing exactly what the ledger reads."""

    def __init__(self, symbol, score, sources, strategy="discovery"):
        self.symbol = symbol
        self.score = score
        self.sources = sources
        self.strategy = strategy


def test_records_and_summarises(tmp_path):
    ledger = DiscoveryLedger(path=tmp_path / "led.jsonl")
    candidates = [
        _Cand("BBB", 80, ["congress", "technical"]),
        _Cand("AAA", 40, ["congress"]),
    ]
    proposals = [SimpleNamespace(symbol="BBB", id="BBB-1")]
    ledger.record_surface(candidates, proposals)

    stats = {s.source: s for s in ledger.summarize()}
    assert stats["congress"].surfaced == 2
    assert stats["congress"].proposed == 1            # only BBB was proposed
    assert stats["technical"].surfaced == 1
    assert stats["congress"].avg_score == 60.0        # (80 + 40) / 2


def test_appends_across_runs(tmp_path):
    ledger = DiscoveryLedger(path=tmp_path / "led.jsonl")
    ledger.record_surface([_Cand("AAA", 50, ["congress"])], [])
    ledger.record_surface([_Cand("BBB", 70, ["congress"])], [SimpleNamespace(symbol="BBB", id="x")])
    assert len(ledger.rows()) == 2
    congress = next(s for s in ledger.summarize() if s.source == "congress")
    assert congress.surfaced == 2 and congress.proposed == 1


def test_empty_ledger_summarises_to_nothing(tmp_path):
    assert DiscoveryLedger(path=tmp_path / "none.jsonl").summarize() == []
