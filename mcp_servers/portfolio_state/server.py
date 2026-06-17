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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
