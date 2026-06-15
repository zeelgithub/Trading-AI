# Strategy System

Three strategies on **daily bars** (swing holds), routed by a **regime filter** so that
only one strategy trades a given symbol at a time. Equal risk budget across strategies.
Config: [`config/strategies.yaml`](../config/strategies.yaml).

## Regime filter (traffic cop)

| Regime | Detection (daily) | Active strategy |
|--------|-------------------|-----------------|
| Trending | ADX > 25, price aligned with 50 & 200 EMA | Trend Following |
| Ranging | ADX < 20, flat EMAs | Mean Reversion |
| Expansion | ATR rising, range breaks on volume | Breakout |
| Dead zone | 20 <= ADX <= 25 | stand aside |

## Strategy 1 — Trend Following (core)

- **Bias:** price above 50 & 200 EMA = bullish; below both = bearish.
- **Filters:** RSI 40-70 (long) / 30-60 (short); volume > 20-day average.
- **Entry:** pullback to 20 EMA + confirmation candle (long); rejection from 20 EMA (short).
- **Exit:** opposite EMA break OR ratchet stop (10% initial / +10% lock @ +20%).
- **Known risk:** RSI <= 70 cap can filter out the strongest trends; whipsaw in chop.

## Strategy 2 — Mean Reversion (revised)

- **Trigger:** Bollinger Bands — price >= 2 sigma from 20-day SMA (replaces VWAP, which is
  intraday-only and meaningless on daily bars) + RSI < 30 (long) / > 70 (short).
- **Entry:** oversold + bullish confirmation (long); overbought + bearish rejection (short).
- **Exit:** revert to mean (SMA20) or +2% target; **hard 2% stop** (~1:1 R:R).
- **Why tight:** a 10% stop against a 1-3% target is ~1:5 reverse risk/reward — a
  guaranteed bleed. Mean-reversion uses its own tight ratchet params.
- **Known risk:** falling knife (oversold can stay oversold) — bounded by the 2% stop.

## Strategy 3 — Breakout

- **Trigger:** break of recent support/resistance + volume >= 1.5x average + rising ATR.
- **Entry:** breakout confirmation candle close.
- **Exit:** ATR-based ratchet stop (2x initial, 1.5x trail).
- **Known risk:** false breakouts and slippage into momentum — needs close-confirmation and
  realistic slippage modeling in backtest.

## Sentiment gate (non-executing)

LLM scores headlines -1 / 0 / +1. Longs require >= 0, shorts <= 0; conflicting sentiment
reduces confidence (and thus size). **The AI never triggers a trade.**

> Backtest integrity: LLM sentiment cannot be honestly backtested on history (look-ahead).
> Plan: backtest the price strategies clean, then layer sentiment forward-only in paper.

## Open definitions (pin down before coding each strategy)

- Precise "confirmation candle" / "rejection candle" rules.
- Exact "recent" support/resistance detection for breakout.
- Shorts in v1 vs longs-only first.
