"""The orchestrator honors an approved rotation: a disabled strategy is skipped.

Default (no rotation file) falls back to config/strategies.yaml's own per-strategy
`enabled:` flag -- so a strategy that ships disabled there (mean_reversion, as of
2026-08-22) stays inactive until an explicit /rotate enable, while strategies that
ship enabled trade exactly as before.
"""

from __future__ import annotations

from src.core.rotation import RotationState, RotationStateStore
from tests.unit.fakes import FakeBroker
from tests.unit.synth import make_features
from tests.unit.test_orchestrator import make_orch


def ranging_frame(_symbol=None):
    """Oversold mean-reversion setup (same fixture as
    test_strategies.test_mean_reversion_long_entry): default adx=20/atr=1.0
    from synth.py route the regime filter to RANGING -> mean_reversion."""
    rows = [
        {"close": 91, "open": 91.5, "bb_lower": 92, "bb_upper": 110, "rsi": 45, "ema200": 80},
        {"close": 89, "open": 90, "bb_lower": 92, "bb_upper": 110, "rsi": 28, "ema200": 80},
        {"close": 89.9, "open": 88.9, "high": 90.1, "low": 88.9,
         "bb_lower": 92, "bb_upper": 110, "bb_mid": 101, "rsi": 25, "ema200": 80},
    ]
    return make_features(rows)


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


def test_config_disabled_strategy_skipped_with_no_rotation_file(tmp_path):
    # mean_reversion ships enabled: false in config/strategies.yaml; with no
    # rotation.json at all, that config default should apply -- not the old
    # blanket "everything enabled" fallback.
    report = make_orch(FakeBroker(), tmp_path, execute=False,
                        feature_provider=ranging_frame).run_cycle()
    assert not report.halted
    assert report.opened == []
    assert any("mean_reversion disabled by rotation" in why for _sym, why in report.skipped)


def test_config_disabled_strategy_can_be_enabled_via_rotation(tmp_path):
    store = RotationStateStore(tmp_path / "rotation.json")
    state = RotationState()
    state.apply("enable", "mean_reversion")
    store.save(state)

    report = make_orch(FakeBroker(), tmp_path, execute=False,
                        feature_provider=ranging_frame, rotation_store=store).run_cycle()
    assert not report.halted
    assert len(report.opened) == 3
