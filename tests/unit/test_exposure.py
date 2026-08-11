"""Unit tests for src/risk/exposure.py -- the shared exposure aggregation used
by every caller that builds an AccountState for RiskManager.evaluate()."""

from __future__ import annotations

from types import SimpleNamespace

from src.risk.exposure import ExposureSnapshot, compute_exposure


def _pos(qty=None, filled_qty=None, entry=100.0, stop=90.0):
    """A minimal stand-in covering both shapes compute_exposure supports:
    live ManagedPosition (has .filled_qty) and the backtester's _Position
    (doesn't)."""
    ns = SimpleNamespace(ratchet=SimpleNamespace(entry=entry, stop=stop))
    if qty is not None:
        ns.qty = qty
    if filled_qty is not None:
        ns.filled_qty = filled_qty
    return ns


def test_empty_positions_is_all_zero():
    snap = compute_exposure([])
    assert snap == ExposureSnapshot(gross_value=0.0, open_count=0, open_risk_dollars=0.0)


def test_single_position_gross_and_open_risk():
    snap = compute_exposure([_pos(qty=10, filled_qty=10, entry=100.0, stop=90.0)])
    assert snap.open_count == 1
    assert snap.gross_value == 1000.0       # 10 * 100
    assert snap.open_risk_dollars == 100.0  # 10 * |100 - 90|


def test_falls_back_to_qty_when_filled_qty_is_zero_or_absent():
    """A PENDING_ENTRY position has filled_qty=0.0 (falsy) until settled --
    must size against the ordered qty, not zero."""
    pending = _pos(qty=10, filled_qty=0.0, entry=100.0, stop=90.0)
    snap = compute_exposure([pending])
    assert snap.gross_value == 1000.0
    assert snap.open_risk_dollars == 100.0


def test_backtester_shaped_position_without_filled_qty_attribute():
    """The backtester's _Position has no filled_qty at all -- must not raise,
    must use qty directly."""
    bt_pos = _pos(qty=5, entry=50.0, stop=48.0)  # no filled_qty= given
    assert not hasattr(bt_pos, "filled_qty")
    snap = compute_exposure([bt_pos])
    assert snap.gross_value == 250.0
    assert snap.open_risk_dollars == 10.0  # 5 * |50 - 48|


def test_short_position_open_risk_is_absolute():
    # Short: stop sits ABOVE entry; open risk must still be positive.
    short = _pos(qty=4, filled_qty=4, entry=100.0, stop=110.0)
    snap = compute_exposure([short])
    assert snap.open_risk_dollars == 40.0  # 4 * |100 - 110|


def test_sums_across_multiple_positions():
    positions = [
        _pos(qty=10, filled_qty=10, entry=100.0, stop=90.0),   # gross 1000, risk 100
        _pos(qty=5, filled_qty=5, entry=50.0, stop=48.0),      # gross 250, risk 10
    ]
    snap = compute_exposure(positions)
    assert snap.open_count == 2
    assert snap.gross_value == 1250.0
    assert snap.open_risk_dollars == 110.0
