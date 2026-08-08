"""Portfolio-state MCP server.

Read-only tools over the bot's OWN state files -- managed positions (intent
state), HALT status, and the strategy scoreboard. No broker, no trading creds.
The live broker account is intentionally not exposed: the deterministic core
owns the broker.

Run:  python -m mcp_servers.portfolio_state.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.core import portfolio_view

mcp = FastMCP("portfolio-state")


@mcp.tool()
def get_positions() -> dict:
    """Open managed positions: {positions: [{symbol, side, qty, entry, stop,
    strategy, status}, ...], count}."""
    return portfolio_view.positions_snapshot()


@mcp.tool()
def get_halt_state() -> dict:
    """Whether the bot is HALTED and why: {halted: bool, reason: str|null}."""
    return portfolio_view.halt_snapshot()


@mcp.tool()
def get_scoreboard() -> dict:
    """Ranked strategy scoreboard with verdicts + live attribution:
    {strategies: [{strategy, verdict, num_trades, sharpe, psr, p_value,
    total_pnl, live_num_trades, live_total_pnl}, ...]}."""
    return portfolio_view.scoreboard_snapshot()


@mcp.tool()
def get_ops_status() -> dict:
    """Operational health: halt record (reason/class/ts), the last completed
    cycle (when + what it did), the last halt-worthy event, recent audit
    events, and pending trade proposals. Use this first when diagnosing
    "why didn't the bot trade?"."""
    return portfolio_view.ops_snapshot()


@mcp.tool()
def get_audit_tail(n: int = 20) -> dict:
    """The most recent `n` audit-trail events (oldest first): signals, risk
    verdicts, orders, halts, reconcile results."""
    return portfolio_view.audit_tail(n)


@mcp.tool()
def get_pending_proposals() -> dict:
    """Trade proposals awaiting phone approval (id, symbol, qty, strategy,
    created/expiry timestamps)."""
    return {"proposals": portfolio_view.ops_snapshot()["pending_proposals"]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
