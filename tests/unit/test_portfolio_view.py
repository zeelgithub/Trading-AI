"""Tests for the portfolio/halt/scoreboard read views."""

from __future__ import annotations

from src.core import portfolio_view as pf
from src.core.state_store import HaltStore, StateStore
from src.research.scoreboard import Scoreboard, StrategyScore


def test_positions_snapshot_empty(tmp_path):
    ss = StateStore(tmp_path / "pos.json")
    assert pf.positions_snapshot(state_store=ss) == {"positions": [], "count": 0}


def test_halt_snapshot(tmp_path):
    hs = HaltStore(tmp_path / "halt.json")
    assert pf.halt_snapshot(halt_store=hs) == {"halted": False, "reason": None}

    hs.set("kill switch")
    out = pf.halt_snapshot(halt_store=hs)
    assert out["halted"] is True
    assert out["reason"] == "kill switch"


def test_scoreboard_snapshot_ranked(tmp_path):
    sb = Scoreboard(tmp_path / "sb.json")
    sb.upsert(StrategyScore(strategy="a", verdict="validated", psr=0.96, total_pnl=500))
    sb.upsert(StrategyScore(strategy="b", verdict="noise", psr=0.10, total_pnl=-50))

    out = pf.scoreboard_snapshot(scoreboard=sb)
    assert [s["strategy"] for s in out["strategies"]] == ["a", "b"]
    assert out["strategies"][0]["verdict"] == "validated"
