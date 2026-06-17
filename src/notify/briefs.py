"""
Decision briefs -- notify layer.

The human-in-the-loop reasoning surface. Instead of an automated agent calling an
LLM API, the bot assembles its cognitive jobs into structured, copy-paste-ready
briefs. You paste a brief into Claude.ai (or any advisor) on your phone, read the
advice, then act back in Telegram (approve/deny, /rotate, /reset). No API key,
no automated agent -- you are the reasoning layer.

Every function here is pure: it takes already-computed snapshots (dicts/lists)
and returns a formatted string. It places no orders and calls nothing external.

Boundary: formatting only; places orders NO.
"""

from __future__ import annotations

_RULE = "————————————————————"


def strategy_review_brief(scoreboard: dict, positions: dict, benchmark: dict | None = None) -> str:
    """A paste-into-Claude.ai prompt asking for keep/disable/reweight advice."""
    lines = ["📋 STRATEGY REVIEW — copy everything below into Claude.ai", _RULE, ""]
    lines.append(
        "I run an autonomous *paper* trading bot (US equities, daily swing). Below is my "
        "strategy scoreboard from backtests + live attribution. These strategies are "
        "unproven. For EACH strategy tell me keep / disable / reweight (0–1) with a "
        "one-line reason. Be conservative."
    )
    lines.append("\nSCOREBOARD:")
    rows = scoreboard.get("strategies", [])
    if not rows:
        lines.append("  (empty — run `python -m scripts.evaluate_strategies` first)")
    for s in rows:
        live = (f", live {s['live_num_trades']}t ${s['live_total_pnl']:+.0f}"
                if s.get("live_num_trades") else "")
        lines.append(
            f"  • {s['strategy']}: {s['verdict'].upper()} — {s['num_trades']} trades, "
            f"PSR {s['psr']:.2f}, p {s['p_value']:.2f}, total ${s['total_pnl']:+.0f}{live}"
        )
    if benchmark:
        lines.append(
            f"\nBenchmark (same window): SPY buy & hold {benchmark['total_return'] * 100:+.1f}%, "
            f"Sharpe {benchmark['sharpe']:.2f}"
        )

    lines.append("\nOPEN POSITIONS:")
    pos = positions.get("positions", [])
    if not pos:
        lines.append("  (none)")
    for p in pos:
        lines.append(
            f"  • {p['symbol']} {p['side']} {p['qty']:g} @ ${p['entry']:.2f}, "
            f"stop ${p['stop']:.2f} [{p['strategy']}]"
        )

    lines.append(_RULE)
    lines.append("After advising, I'll act in Telegram: "
                 "/rotate <enable|disable|reweight> <strategy> [weight]")
    return "\n".join(lines)


def incident_brief(halt_info: dict, recent_events: list) -> str:
    """A paste-into-Claude.ai prompt for diagnosing a HALT."""
    lines = ["🛑 INCIDENT — copy everything below into Claude.ai", _RULE, ""]
    lines.append(
        "My paper trading bot HALTED. Diagnose the likely cause and tell me the safest next "
        "step. Note: reconcile-mismatch and kill-switch require me to manually review "
        "positions / the day's loss before any /reset."
    )
    lines.append("")
    lines.append(f"HALT class : {halt_info.get('class')}")
    lines.append(f"HALT reason: {halt_info.get('reason')}")
    lines.append(f"HALT time  : {halt_info.get('ts')}")

    lines.append("\nRECENT EVENTS (oldest first):")
    if not recent_events:
        lines.append("  (none)")
    for e in recent_events[-15:]:
        extra = {k: v for k, v in e.items() if k not in ("ts", "event")}
        ts = str(e.get("ts", ""))[:19]
        lines.append(f"  • {ts} {e.get('event', '?')} {extra if extra else ''}".rstrip())

    lines.append(_RULE)
    lines.append("Tell me: likely cause, severity, and whether it's safe to /reset (or what to check first).")
    return "\n".join(lines)


def symbol_brief(symbol: str, indicators: dict, strategy_score: dict | None = None) -> str:
    """A paste-into-Claude.ai prompt for a quick read on one symbol."""
    lines = [f"📈 {symbol.upper()} — copy everything below into Claude.ai", _RULE, ""]
    lines.append(
        f"My bot is considering {symbol.upper()} (daily swing). Here's the latest causal "
        "snapshot. Give me a brief read: is this a reasonable swing entry, the key risk, "
        "and a sensible stop?"
    )
    lines.append("\nINDICATORS (latest bar):")
    for k in ("date", "close", "ema20", "ema50", "ema200", "rsi", "atr", "adx", "regime"):
        if k in indicators and indicators[k] is not None:
            lines.append(f"  {k}: {indicators[k]}")
    if strategy_score:
        lines.append(
            f"\nRouting strategy '{strategy_score['strategy']}' scoreboard verdict: "
            f"{strategy_score['verdict'].upper()} (PSR {strategy_score['psr']:.2f})"
        )
    lines.append(_RULE)
    lines.append("If it looks good I'll place it in Telegram: /buy SYM QTY --stop N")
    return "\n".join(lines)
