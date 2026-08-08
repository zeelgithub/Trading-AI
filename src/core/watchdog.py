"""
Watchdog -- core layer.

Pure health-evaluation logic for the independent healthcheck probe. Answers the
questions nothing else asks: did the scheduled cycle actually run today? Is the
always-on Telegram listener alive? Is the bot sitting halted or on stale data
with nobody noticing? A laptop asleep at 15:45 produces NO error anywhere --
this is the layer that turns that silence into a phone alert.

Deterministic and clock-injectable; file/network IO lives in the entrypoint
(scripts/healthcheck.py), not here.

Boundary: read-only; places orders NO.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

ET = "America/New_York"


@dataclass(frozen=True)
class HealthIssue:
    kind: str      # halted | missed_cycle | listener_down | stale_data
    detail: str

    def text(self) -> str:
        return f"[{self.kind}] {self.detail}"


def _parse_ts(ts: str | None) -> pd.Timestamp | None:
    if not ts:
        return None
    try:
        t = pd.Timestamp(ts)
    except (ValueError, TypeError):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t


def evaluate_health(
    *,
    now_et: pd.Timestamp,
    halt: dict | None,
    last_cycle_ts: str | None,
    evaluate_at: str = "15:45",
    cycle_grace_minutes: int = 45,
    heartbeat_ts: str | None = None,
    listener_max_age_seconds: int = 300,
    stale_symbols: list[str] | None = None,
    is_trading_day: bool = True,
) -> list[HealthIssue]:
    """Evaluate bot health from already-gathered facts. Returns [] if healthy.

    `is_trading_day` should come from a real exchange calendar (see
    scripts/healthcheck.py) -- a bare weekday check would false-alarm
    "missed_cycle" on every NYSE holiday that falls Mon-Fri.
    """
    issues: list[HealthIssue] = []

    if halt:
        issues.append(HealthIssue(
            "halted", f"bot is HALTED ({halt.get('class')}): {halt.get('reason')}"))

    # Missed cycle: on a real trading day, once evaluate_at + grace has
    # passed, a cycle_complete must exist with today's (ET) date at/after
    # evaluate_at.
    hh, mm = (int(x) for x in evaluate_at.split(":"))
    deadline = now_et.normalize() + pd.Timedelta(hours=hh, minutes=mm + cycle_grace_minutes)
    if is_trading_day and now_et >= deadline:
        expected_after = now_et.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        last = _parse_ts(last_cycle_ts)
        ran_today = last is not None and last.tz_convert(ET) >= expected_after
        if not ran_today:
            seen = (f"last cycle {last.tz_convert(ET).isoformat()}" if last is not None
                    else "no cycle_complete event on record")
            issues.append(HealthIssue(
                "missed_cycle",
                f"no decision cycle since today's {evaluate_at} ET schedule ({seen}) -- "
                "is the machine awake and the scheduled task enabled?"))

    hb = _parse_ts(heartbeat_ts)
    if hb is None:
        issues.append(HealthIssue(
            "listener_down", "no listener heartbeat recorded -- is run_telegram running?"))
    else:
        age = (now_et.tz_convert("UTC") - hb.tz_convert("UTC")).total_seconds()
        if age > listener_max_age_seconds:
            issues.append(HealthIssue(
                "listener_down",
                f"listener heartbeat is {int(age)}s old (max {listener_max_age_seconds}s) -- "
                "phone control is dead; restart run_telegram"))

    if stale_symbols:
        issues.append(HealthIssue(
            "stale_data", f"bar cache stale for: {', '.join(sorted(stale_symbols))}"))

    return issues
