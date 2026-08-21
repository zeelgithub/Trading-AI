"""
Entrypoint: one self-heal tick.

Run periodically (e.g. every few minutes from the always-on listener or a task):

  1. Attempt a VERIFIED auto-resume of a whitelisted transient HALT
     (stale_data / disconnect only) -- deterministic, gated, capped.
  2. If the halt is manual-only, or auto-resume is otherwise blocked/escalated,
     run the anomaly_triage agent and push a diagnosis to the phone.

Never clears reconcile-mismatch or kill-switch halts (those stay manual).

    python -m scripts.run_self_heal
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.common.config import load_config
from src.common.logging import AuditLog, get_logger
from src.core.self_heal import SelfHealer
from src.core.state_store import HaltClass, HaltStore
from src.data import store
from src.notify.briefs import incident_brief
from src.notify.telegram import build_notifier

log = get_logger("self_heal")


def _data_is_fresh(symbols, max_age_days: int) -> bool:
    """True if every enabled symbol has a recent bar in the local cache."""
    if not symbols:
        return False
    conn = store.connect()
    today = datetime.now(timezone.utc).date()
    for sym in symbols:
        bars = store.load_bars(conn, sym)
        if bars.empty:
            return False
        last = bars.index[-1]
        last_date = last.date() if hasattr(last, "date") else None
        if last_date is None or (today - last_date).days > max_age_days:
            return False
    return True


def _broker_reachable() -> bool:
    """True if a broker account read succeeds (i.e. we are reconnected)."""
    try:
        from src.execution.broker_alpaca import AlpacaAccountReader

        AlpacaAccountReader().get_account()
        return True
    except Exception as exc:  # still down
        log.info("broker still unreachable: %s", exc)
        return False


def main() -> None:
    config = load_config()
    notifier = build_notifier(config)
    symbols = config.enabled_symbols()
    # Same threshold the orchestrator halts on (settings.data.max_bar_age_days)
    # -- this used to be a separately hardcoded "4" that could silently drift
    # from the halt condition it's supposed to verify has cleared.
    max_bar_age_days = int(config.get("settings.data.max_bar_age_days", 4))

    healer = SelfHealer(
        HaltStore(),
        verifiers={
            HaltClass.STALE_DATA: lambda: _data_is_fresh(symbols, max_bar_age_days),
            HaltClass.DISCONNECT: _broker_reachable,
        },
        cooldown_seconds=int(config.get("settings.self_heal.cooldown_seconds", 300)),
        max_per_day=int(config.get("settings.self_heal.max_per_day", 3)),
        lock_timeout=float(config.get("settings.concurrency.action_lock_timeout_seconds", 15)),
        notifier=notifier,
        audit=AuditLog(),
    )

    result = healer.attempt_resume()
    print(result.detail)
    if result.resumed:
        log.info("self-heal: auto-resumed (%s)", result.halt_class)
        return
    if not result.escalate:
        return  # transient / cooldown -- will retry on the next tick, no noise

    # Manual-only or capped: push a paste-into-Claude.ai incident brief to the phone.
    info = HaltStore().halt_info() or {}
    notifier.alert("incident", incident_brief(info, AuditLog().tail(20)))
    print("escalated: incident brief sent to phone")


if __name__ == "__main__":
    main()
