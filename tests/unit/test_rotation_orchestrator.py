"""The orchestrator honors an approved rotation: a disabled strategy is skipped.

Default (no rotation file) leaves every strategy enabled -- so this feature is a
no-op until a rotation has actually been approved.
"""

from __future__ import annotations

from src.core.rotation import RotationState, RotationStateStore
from tests.unit.fakes import FakeBroker
from tests.unit.test_orchestrator import make_orch


def test_enabled_by_default_still_trades(tmp_path):
    report = make_orch(FakeBroker(), tmp_path, execute=False).run_cycle()
    assert len(report.opened) == 3


def test_disabled_strategy_is_skipped(tmp_path):
    store = RotationStateStore(tmp_path / "rotation.json")
    state = RotationState()
    state.apply("disable", "trend_following")   # the strategy approve_frame triggers
    store.save(state)

    report = make_orch(FakeBroker(), tmp_path, execute=False, rotation_store=store).run_cycle()
    assert not report.halted
    assert report.opened == []
    assert any("disabled by rotation" in why for _sym, why in report.skipped)
