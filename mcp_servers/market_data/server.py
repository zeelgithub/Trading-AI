"""Market-data MCP server.

Read-only tools over the LOCAL bar cache -- no broker, no trading creds. Exposes
the same query functions the in-process read tools use (src/data/queries.py), so
a Claude Code session, Claude Desktop, or the runtime agents all see one surface.

Run:  python -m mcp_servers.market_data.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.data import queries

mcp = FastMCP("market-data")


@mcp.tool()
def get_recent_bars(symbol: str, days: int = 20) -> dict:
    """Recent daily OHLCV bars for a symbol (oldest first).

    Returns {"symbol", "bars": [{date, open, high, low, close, volume}, ...]}.
    """
    return queries.recent_bars(symbol, days)


@mcp.tool()
def get_indicators(symbol: str) -> dict:
    """Latest causal indicator snapshot + regime for a symbol.

    Returns {symbol, date, close, ema20/50/200, rsi, atr, adx, bb_upper,
    bb_lower, regime}. Indicator values still warming up are null.
    """
    return queries.indicator_snapshot(symbol)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
