"""
Entrypoint: independent watchdog probe.

Gathers local facts (audit trail, halt file, listener heartbeat, bar-cache
freshness), evaluates them with src/core/watchdog.py, and pushes a Telegram
alert when something is wrong -- with a fingerprint + cooldown so a persistent
problem alerts once, not every probe.

Detects what the bot itself cannot: a machine that slept through the 15:45
cycle, a dead Telegram listener, a lingering HALT nobody cleared.

Run it on its own schedule (e.g. every 15 minutes on weekdays):
    python -m scripts.healthcheck [--no-alert]

Exit code: 0 healthy, 1 issues found. Reads local files only -- needs no
trading credentials.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.common.config import load_config
from src.common.jsonio import atomic_write_json, load_json_or_quarantine
from src.common.logging import get_logger
from src.core import portfolio_view
from src.core.watchdog import ET, evaluate_health
from src.data import queries
from src.notify.telegram import build_notifier

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_PATH = PROJECT_ROOT / "state" / "listener_heartbeat.json"
WATCHDOG_STATE_PATH = PROJECT_ROOT / "state" / "watchdog.json"

log = get_logger("healthcheck")


def _heartbeat_ts() -> str | None:
    payload, _ = load_json_or_quarantine(HEARTBEAT_PATH)
    return payload.get("ts") if isinstance(payload, dict) else None


def _should_alert(fingerprint: str, now_utc: pd.Timestamp, cooldown_minutes: int) -> bool:
    """Alert when the issue set changed, or the cooldown for the same set passed."""
    payload, _ = load_json_or_quarantine(WATCHDOG_STATE_PATH)
    if isinstance(payload, dict) and payload.get("fingerprint") == fingerprint:
        try:
            last = pd.Timestamp(payload.get("ts"))
            if (now_utc - last).total_seconds() < cooldown_minutes * 60:
                return False
        except (ValueError, TypeError):
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent bot health probe.")
    parser.add_argument("--no-alert", action="store_true",
                        help="print findings only, never push to Telegram")
    args = parser.parse_args()

    config = load_config()
    wd = config.get("settings.watchdog", {}) or {}

    ops = portfolio_view.ops_snapshot()
    try:
        stale = queries.data_health()["stale"]
    except Exception as exc:  # cache unreadable is itself worth reporting
        log.warning("data_health failed: %s", exc)
        stale = ["<data cache unreadable>"]

    last_cycle = ops.get("last_cycle") or {}
    issues = evaluate_health(
        now_et=pd.Timestamp.now(tz=ET),
        halt=ops.get("halt"),
        last_cycle_ts=last_cycle.get("ts"),
        evaluate_at=str(config.get("settings.scheduling.evaluate_at", "15:45")),
        cycle_grace_minutes=int(wd.get("cycle_grace_minutes", 45)),
        heartbeat_ts=_heartbeat_ts(),
        listener_max_age_seconds=int(wd.get("listener_max_age_seconds", 300)),
        stale_symbols=stale,
    )

    if not issues:
        print("healthy: cycle on schedule, listener alive, data fresh, not halted")
        return 0

    lines = [i.text() for i in issues]
    print("\n".join(lines))

    if not args.no_alert:
        fingerprint = "|".join(sorted(i.kind + ":" + i.detail for i in issues))
        now_utc = pd.Timestamp.now(tz="UTC")
        cooldown = int(wd.get("alert_cooldown_minutes", 360))
        if _should_alert(fingerprint, now_utc, cooldown):
            build_notifier(config).alert("watchdog", "\n".join(lines))
            atomic_write_json(WATCHDOG_STATE_PATH,
                              {"fingerprint": fingerprint, "ts": now_utc.isoformat()})
        else:
            print("(same issues as last alert -- within cooldown, not re-alerting)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
