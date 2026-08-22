"""
Entrypoint: one self-heal tick.

Run periodically (e.g. every few minutes from the always-on listener or a task):

  1. Attempt a VERIFIED auto-resume of a whitelisted transient HALT
     (stale_data / disconnect only) -- deterministic, gated, capped.
  2. If the halt is manual-only, or auto-resume is otherwise blocked/escalated,
     push a paste-into-Claude.ai incident brief to the phone -- plus the
     anomaly_triage agent's own diagnosis (src/agents/triage.py), best-effort,
     ONLY when ANTHROPIC_API_KEY is set. No key -> brief only, unchanged.

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


def _try_agent_diagnosis() -> str | None:
    """Best-effort: if ANTHROPIC_API_KEY is set, get the anomaly_triage
    agent's read (src/agents/triage.py -- diagnose-and-recommend ONLY, it
    never clears a halt or places a trade) alongside the deterministic
    brief. Returns None on a missing key or ANY failure, so escalation is
    byte-identical to before this existed when no key is configured -- the
    agent enriches the alert, it is never required for one to go out."""
    try:
        from src.common.secrets import load_anthropic_api_key

        load_anthropic_api_key()
    except Exception:
        return None  # no key configured -- not an error, just nothing to add

    try:
        from src.agents.triage import AnomalyTriage

        result = AnomalyTriage().diagnose()
        if not result.ok or not result.output:
            return None
        o = result.output
        return (
            f"🤖 Agent diagnosis ({o.get('severity', '?')}): {o.get('diagnosis', '?')}\n"
            f"Likely cause: {o.get('likely_cause', '?')}\n"
            f"Recommended: {o.get('recommended_action', '?')}"
        )
    except Exception as exc:  # never let an agent failure block the brief
        log.warning("anomaly_triage failed (falling back to brief only): %s", exc)
        return None


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

    # Manual-only or capped: push a paste-into-Claude.ai incident brief to the phone,
    # plus the anomaly_triage agent's own read when a Claude key is configured.
    info = HaltStore().halt_info() or {}
    brief = incident_brief(info, AuditLog().tail(20))
    diagnosis = _try_agent_diagnosis()
    if diagnosis:
        brief = f"{diagnosis}\n\n{brief}"
    notifier.alert("incident", brief)
    print("escalated: incident brief sent to phone" + (" (+ agent diagnosis)" if diagnosis else ""))


if __name__ == "__main__":
    main()
