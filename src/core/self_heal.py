"""
Self-healer -- core layer.

Guarded automatic resume after a HALT (decision #6). This is deliberately
DETERMINISTIC Python, not an agent: clearing a halt is too consequential to hang
on an LLM's say-so. The anomaly_triage agent diagnoses and recommends; THIS class
is the only thing that actually clears a halt, and only when every gate passes.

Gates, in order:
  1. Class whitelist -- ONLY stale_data and disconnect are ever resumable.
     reconcile_mismatch and kill_switch are absent here and stay manual forever.
  2. A verifier for that class must confirm the fault has actually cleared
     (fresh data re-fetched / broker reconnected). No verifier => no resume.
  3. Cooldown -- a minimum time must have elapsed since the halt.
  4. Daily cap -- at most N auto-resumes per day, persisted across runs.

On success it clears the halt, bumps the daily counter, audits, and notifies the
phone (the user can send /halt to re-halt instantly). Anything short of all four
gates leaves the bot HALTED.

Boundary: clears halts only via verified gates; places orders NO.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.common.logging import get_logger
from src.core.state_store import HaltClass, HaltStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COUNTER_PATH = PROJECT_ROOT / "state" / "self_heal.json"

# The ONLY halt classes eligible for automatic resume. Reconcile mismatch and
# kill-switch are intentionally NOT here -- they always require a manual /reset.
RESUMABLE = frozenset({HaltClass.STALE_DATA, HaltClass.DISCONNECT})

Verifier = Callable[[], bool]


@dataclass
class ResumeResult:
    resumed: bool
    halt_class: str | None
    detail: str
    escalate: bool = False   # True => hand to anomaly_triage / push to phone


class _DailyCounter:
    """Persisted per-UTC-day counter so the auto-resume cap survives restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def count(self) -> int:
        if not self.path.exists():
            return 0
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return int(data.get("count", 0)) if data.get("date") == self._today() else 0

    def increment(self) -> None:
        self.path.write_text(
            json.dumps({"date": self._today(), "count": self.count() + 1}),
            encoding="utf-8",
        )


class SelfHealer:
    def __init__(
        self,
        halt_store: HaltStore | None = None,
        verifiers: dict[str, Verifier] | None = None,
        *,
        cooldown_seconds: int = 300,
        max_per_day: int = 3,
        counter_path: str | Path = DEFAULT_COUNTER_PATH,
        notifier=None,
        audit=None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.halt_store = halt_store or HaltStore()
        self.verifiers = dict(verifiers or {})
        self.cooldown_seconds = int(cooldown_seconds)
        self.max_per_day = int(max_per_day)
        self.counter = _DailyCounter(counter_path)
        self.notifier = notifier
        self.audit = audit
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.log = get_logger("self_heal")

    def attempt_resume(self) -> ResumeResult:
        info = self.halt_store.halt_info()
        if info is None:
            return ResumeResult(False, None, "not halted")

        hc = info.get("class", HaltClass.UNKNOWN)
        if hc not in RESUMABLE:
            return ResumeResult(False, hc, f"{hc} is manual-only -- will not auto-resume",
                                escalate=True)

        verifier = self.verifiers.get(hc)
        if verifier is None:
            return ResumeResult(False, hc, f"no verifier configured for {hc}", escalate=True)

        if not self._cooldown_elapsed(info):
            return ResumeResult(False, hc, "within cooldown window")

        if self.counter.count() >= self.max_per_day:
            return ResumeResult(False, hc, "daily auto-resume cap reached", escalate=True)

        try:
            cleared = bool(verifier())
        except Exception as exc:
            return ResumeResult(False, hc, f"verification raised: {exc}", escalate=True)
        if not cleared:
            return ResumeResult(False, hc, "fault has not cleared yet")

        # Every gate passed -> deterministically clear the halt.
        self.halt_store.clear()
        self.counter.increment()
        self._audit(hc, info.get("reason"))
        self._notify(f"✅ Auto-resumed: {hc} cleared and verified. Send /halt to stop again.")
        return ResumeResult(True, hc, "auto-resumed (verified)")

    # --- internals ---
    def _cooldown_elapsed(self, info: dict) -> bool:
        ts = info.get("ts")
        if not ts:
            return True
        try:
            halted_at = datetime.fromisoformat(ts)
        except ValueError:
            return True
        return (self._now() - halted_at).total_seconds() >= self.cooldown_seconds

    def _audit(self, halt_class: str, reason) -> None:
        if self.audit is not None:
            self.audit.record("self_heal_resume", halt_class=halt_class, reason=reason)

    def _notify(self, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.alert("self_heal", text)
        except Exception as exc:  # never let a notify failure matter
            self.log.warning("self-heal notify failed: %s", exc)
