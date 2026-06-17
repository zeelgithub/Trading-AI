"""Chaos tests for the guarded self-healer -- the safety-critical auto-resume.

Each halt class is injected and the resume behavior asserted: only stale_data /
disconnect ever resume (and only when verified + within cooldown + under the cap);
reconcile-mismatch and kill-switch never do.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.self_heal import RESUMABLE, SelfHealer
from src.core.state_store import HaltClass, HaltStore


class _Notifier:
    def __init__(self):
        self.alerts = []

    def alert(self, event, detail):
        self.alerts.append((event, detail))


def _healer(tmp_path, verifiers=None, **kw):
    hs = HaltStore(tmp_path / "halt.json")
    healer = SelfHealer(
        hs, verifiers or {},
        counter_path=tmp_path / "sh.json",
        cooldown_seconds=kw.pop("cooldown_seconds", 0),
        **kw,
    )
    return healer, hs


def test_whitelist_excludes_dangerous_classes():
    assert RESUMABLE == frozenset({HaltClass.STALE_DATA, HaltClass.DISCONNECT})
    assert HaltClass.RECONCILE_MISMATCH not in RESUMABLE
    assert HaltClass.KILL_SWITCH not in RESUMABLE


def test_not_halted_is_noop(tmp_path):
    healer, _ = _healer(tmp_path)
    r = healer.attempt_resume()
    assert not r.resumed and r.detail == "not halted"


def test_reconcile_mismatch_never_resumes(tmp_path):
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: True})
    hs.set("reconcile mismatch: TSLA", HaltClass.RECONCILE_MISMATCH)
    r = healer.attempt_resume()
    assert not r.resumed and r.escalate
    assert "manual-only" in r.detail
    assert hs.is_halted() is not None          # STILL halted


def test_kill_switch_never_resumes(tmp_path):
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: True})
    hs.set("kill switch: -5%", HaltClass.KILL_SWITCH)
    r = healer.attempt_resume()
    assert not r.resumed and r.escalate
    assert hs.is_halted() is not None


def test_stale_data_resumes_when_verified(tmp_path):
    notifier = _Notifier()
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: True}, notifier=notifier)
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert r.resumed and r.halt_class == HaltClass.STALE_DATA
    assert hs.is_halted() is None              # cleared
    assert notifier.alerts and "Auto-resumed" in notifier.alerts[0][1]


def test_disconnect_resumes_when_verified(tmp_path):
    healer, hs = _healer(tmp_path, {HaltClass.DISCONNECT: lambda: True})
    hs.set("broker disconnect", HaltClass.DISCONNECT)
    assert healer.attempt_resume().resumed
    assert hs.is_halted() is None


def test_stale_data_blocked_when_fault_persists(tmp_path):
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: False})
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert not r.resumed and not r.escalate
    assert "not cleared" in r.detail
    assert hs.is_halted() is not None          # still halted, quietly retry later


def test_missing_verifier_escalates(tmp_path):
    healer, hs = _healer(tmp_path, {})         # no verifier for stale_data
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert not r.resumed and r.escalate
    assert "no verifier" in r.detail


def test_verifier_exception_escalates(tmp_path):
    def boom():
        raise RuntimeError("probe down")

    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: boom})
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert not r.resumed and r.escalate
    assert "verification raised" in r.detail
    assert hs.is_halted() is not None


def test_cooldown_blocks_resume(tmp_path):
    now = datetime.now(timezone.utc)  # ~ halt time => ~0 elapsed
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: True},
                         cooldown_seconds=300, now=lambda: now)
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert not r.resumed and "cooldown" in r.detail
    assert hs.is_halted() is not None


def test_daily_cap_blocks_and_escalates(tmp_path):
    healer, hs = _healer(tmp_path, {HaltClass.STALE_DATA: lambda: True}, max_per_day=1)
    healer.counter.increment()                 # today's allowance already spent
    hs.set("data went stale", HaltClass.STALE_DATA)
    r = healer.attempt_resume()
    assert not r.resumed and r.escalate
    assert "cap" in r.detail
    assert hs.is_halted() is not None
