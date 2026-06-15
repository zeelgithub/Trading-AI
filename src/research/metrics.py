"""
Performance metrics -- research layer.

Pure functions over an equity curve and a list of closed trades. Annualization
assumes 252 trading days.

Boundary: pure functions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_TRADING_DAYS = 252


def compute_metrics(equity: pd.Series, trades: list) -> dict:
    """Return a dict of headline performance stats.

    `trades` items must expose a numeric `pnl` attribute.
    """
    out: dict[str, float | int] = {}
    if equity is None or len(equity) < 2:
        return {"num_trades": len(trades)}

    rets = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    n = len(equity)

    out["total_return"] = round(total_return, 4)
    out["cagr"] = round((1 + total_return) ** (_TRADING_DAYS / n) - 1, 4) if n > 0 else 0.0
    out["sharpe"] = (
        round(rets.mean() / rets.std() * np.sqrt(_TRADING_DAYS), 2)
        if rets.std() > 0
        else 0.0
    )
    out["max_drawdown"] = round((equity / equity.cummax() - 1.0).min(), 4)

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    out["num_trades"] = len(trades)
    out["win_rate"] = round(len(wins) / len(pnls), 4) if pnls else 0.0
    out["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")
    out["avg_win"] = round(gross_profit / len(wins), 2) if wins else 0.0
    out["avg_loss"] = round(-gross_loss / len(losses), 2) if losses else 0.0
    return out
