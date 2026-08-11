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

## Regime thresholds (evidence-based, revised)

The regime router used to gate mean-reversion behind `ADX <= 20` ("ranging") -- the
textbook definition. Measured against the research universe (27 symbols, ~14 months),
ADX at the exact moment mean-reversion's own trigger fires (price >=2 sigma from the
20-day mean + RSI extreme) has a **median of ~27**: reaching a real 2-sigma stretch is
itself a directional thrust, which pushes ADX up, not down. The old gate admitted only
38 of 243 raw signals (15.6%). `ranging_adx_max` is now 28 and `trending_adx_min` is 32
(a narrower 28-32 dead zone, ADX values that are genuinely ambiguous either way) --
raising overlap to 101/243 (41.6%) at ADX<=25 and further at the new 28 ceiling.

This alone was not enough: after widening, actual backtest trades were still zero. The
deeper bug was **entry timing**, described next.

## Entry timing: extreme/pullback and confirmation need not be the same bar

Both directional strategies originally required the "event" (a pullback touching the
20 EMA; a Bollinger-band extreme + RSI reading) and the "confirmation candle" to be the
**identical bar**. Measured against the research universe, this is a severe, largely
unintentional restriction:

| strategy | same-bar-only | windowed (lookback=3) |
|---|---|---|
| trend_following (pullback -> reclaim) | 468 | 635 (+36%) |
| mean_reversion (extreme -> reversal), regime-gated | 1 | 34 (34x) |

A real pullback-and-reclaim, or a real oversold-and-reversal, typically plays out over a
couple of days -- requiring both halves on one bar was asking for a coincidence, not a
pattern. Both strategies now allow the event to have occurred anywhere in the last
`pullback_lookback_bars` / `reversion_lookback_bars` bars (default 3, config-driven);
the confirmation candle itself must still be **today's** bar -- that precision is what
makes it a trigger, not a loosening of the signal itself.

Net effect on the real evaluation run (27-symbol universe, ~14 months, both fixes plus
the regime widening): mean_reversion went from 0 trades to 9; trend_following's own
remaining bottleneck (volume filter 78->31 days, then the confirmation-candle body-ratio
31->6) reflects genuine, deliberate selectivity, not a further timing bug -- it was left
as-is rather than loosened further, to avoid curve-fitting the parameters to manufacture
trade count instead of fixing an actual defect.

## Confirmation candle (shared, precise)

Every "confirmation candle" / "rejection candle" check across all three strategies uses
the same rule (`src/strategy/base.py`, config: `strategies.yaml -> confirmation_candle`):
the candle's real body (`|close - open|`) must be **at least 50% of its own high-low
range**, in addition to closing in the signal direction and beyond the prior close. A
green tick with a tiny body (a doji) does not confirm — it takes a real-bodied candle.
This is the single precision bar `initial_stop_pct`-style tuning applies to; raise
`min_body_ratio` for fewer, more selective entries, lower it for more.

## Strategy 1 — Trend Following (core)

- **Bias:** price above 50 & 200 EMA = bullish; below both = bearish.
- **Filters:** RSI 40-70 (long) / 30-60 (short); volume > 20-day average.
- **Entry:** pullback to 20 EMA (within 2%) + confirmation candle that closes back
  above the 20 EMA (long); rejection from 20 EMA + confirmation candle closing back
  below it (short).
- **Exit:** opposite EMA break OR ratchet stop (10% initial / +10% lock @ +20%).
- **Known risk:** RSI <= 70 cap can filter out the strongest trends; whipsaw in chop.
  Not yet backtested at meaningful sample size (see Validation status below).

## Strategy 2 — Mean Reversion (revised)

- **Trigger:** Bollinger Bands — price >= 2 sigma from 20-day SMA (replaces VWAP, which is
  intraday-only and meaningless on daily bars) + RSI < 30 (long) / > 70 (short).
- **Entry:** oversold + confirmation candle (long); overbought + confirmation candle (short).
- **Exit:** revert to mean (SMA20) or +2% target; **hard 2% stop** (~1:1 R:R).
- **Why tight:** a 10% stop against a 1-3% target is ~1:5 reverse risk/reward — a
  guaranteed bleed. Mean-reversion uses its own tight ratchet params.
- **Known risk:** falling knife (oversold can stay oversold) — bounded by the 2% stop.

## Strategy 3 — Breakout

- **Trigger:** break of recent support/resistance (20-bar high/low) + volume >= 1.5x
  average + rising ATR **+ a real-bodied confirmation candle beyond the level**.
- **Entry:** the confirmation-candle requirement above was previously undocumented-but-
  missing from the code (a plain `close > resistance` fired with no candle-shape check
  at all); it is now enforced, closing the gap between the documented design and the
  implementation.
- **Exit:** ATR-based ratchet stop (2x initial, 1.5x trail).
- **Known risk:** false breakouts and slippage into momentum. The confirmation-candle
  fix directly targets false breakouts; realistic slippage is modeled via
  `settings.backtest.slippage_bps` (see docs/CONTRACTS.md / config/settings.yaml).

## Validation status

Updated 2026-08-10 (latest of three re-runs same day — widened history, then two
risk-layer changes that both affect which trades clear the gate; see "Recent
risk-layer changes" below). `--full-refetch --lookback-days 1500` (~4.1 years,
1029 trading days, vs. the original 14-month/8-trade window), 27-symbol research
universe + SPY:

| strategy | trades | win% | PF | p(adj) | verdict |
|---|---|---|---|---|---|
| trend_following | 42 | 45.2 | 3.51 | 0.045 | **VALIDATED** |
| breakout | 179 | 39.7 | 1.71 | 0.055 | INCONCLUSIVE (just above the 0.05 bar) |
| mean_reversion | 47 | 38.3 | 0.57 | 1.000 | NOISE — and net-negative (PF < 1) |

Portfolio (all three combined): +38.4% return, Sharpe 1.34, maxDD -5.4%, 268 trades.
Buy-and-hold SPY over the same window: +113.5% return, Sharpe 1.24. The strategies
underperform on raw return but beat SPY's Sharpe with roughly a third of its
drawdown — expected, since the risk gate caps exposure (max 10 positions, a
fraction of budget risked each, further rationed by the aggregate open-risk cap
below) rather than staying 100%-invested like a passive benchmark; it is not
evidence the bot is broken, but it does mean "beats SPY on return" is not a claim
this system can make.

### Recent risk-layer changes (both same day, both affect the numbers above)

- **Confidence-scaled sizing** (`src/risk/risk_manager.py` `evaluate()` step 7):
  every strategy computes a `confidence` on its Intent (trend_following scales it
  with ADX strength; breakout and mean_reversion currently emit a fixed value),
  and the sentiment gate applies a haircut when sentiment is neutral
  (`_NEUTRAL_HAIRCUT = 0.8` in `src/strategy/sentiment_gate.py` — the default
  today, since no sentiment source is wired). This was previously computed and
  threaded through the whole pipeline but never consumed — sizing always risked
  the flat per-strategy budget regardless. Now the risk budget is scaled by
  `intent.confidence` (clamped to [0,1], so it only ever shrinks, never amplifies
  — consistent with rule 2: signal/sentiment layers shrink or block, never
  originate or enlarge). A manual/phone buy (`trade_service.place_manual`) always
  passes `confidence=1.0` and is unaffected.
- **Aggregate open-risk cap** (`evaluate()` step 7.5, `account.max_open_risk_pct`
  in `config/risk_limits.yaml`, defaults to `max_daily_loss_pct`): bounds the sum
  of `qty * |entry - stop|` across ALL open positions, so a broad selloff hitting
  every held position's stop the same day is capped near the kill switch's own
  4% threshold — previously the kill switch (`max_daily_loss_pct`) only checked
  once per cycle against realized+unrealized loss and only blocked *new* entries;
  nothing capped how much simultaneous stop-outs across up to `max_open_positions`
  (10) could cost. See docs/SAFEGUARDS.md.

Together these shifted WHICH trades clear the portfolio-level risk budget on a
given day (fewer breakout trades got through, 203→179; trend_following dropped
from 50→42 similarly) — not a strategy-logic change. The maxDD improvement
(-8.3% pre-change → -5.4%) is the aggregate-risk cap doing its job.

Reading:
- **trend_following** now clears `min_trades=30` with a real, Sidak-adjusted
  p < 0.05 and the highest profit factor (3.51) of the three — the first strategy
  in this project with a statistically real edge on backtest data. Still "a floor
  to clear, not a green light to go live" (below).
- **breakout** is a genuine borderline case sitting just past the significance
  threshold — worth more history or live paper-trading data before promoting or
  dropping it, not more parameter tuning.
- **mean_reversion** remains net-negative (PF 0.57) across both re-runs. A real
  candidate for `/review` → disable via rotation (`state/rotation.json`,
  human-approved, never automatic) rather than further tuning.

None of this was tuned to look better than it is — no strategy parameter was
touched in any of today's three re-runs; only the history window and (separately)
two risk-layer fixes changed, and the fixes were made because they were real gaps,
not to move these numbers.

`scripts/evaluate_strategies.py` defaults to a wider, sector-diverse validation universe
(`settings.research.backtest_universe`, ~27 symbols) — separate from the live watchlist,
which stays small and explicit. Re-run after any strategy-logic OR risk-logic change,
or periodically to extend the window as more live history accumulates:

    python -m scripts.evaluate_strategies --full-refetch --lookback-days 1500

A "validated" verdict is a floor to clear, not authorization to trade live.

## Sentiment gate (non-executing)

LLM scores headlines -1 / 0 / +1. Longs require >= 0, shorts <= 0; conflicting sentiment
reduces confidence (and thus size). **The AI never triggers a trade.**

> Backtest integrity: LLM sentiment cannot be honestly backtested on history (look-ahead).
> Plan: backtest the price strategies clean, then layer sentiment forward-only in paper.

## Decided (previously open definitions)

- **Confirmation/rejection candle:** precise now — see "Confirmation candle" above.
- **"Recent" support/resistance for breakout:** the prior `sr_lookback` bars (default 20,
  `strategies.yaml -> strategies.breakout.indicators.sr_lookback`), excluding the current bar.
- **Shorts:** longs-only for this validation pass (`symbols.yaml -> defaults.allow_short:
  false`). All three strategies implement short-side logic and `shorts_allowed()` is
  per-symbol overridable, but it has not traded — live, paper, or backtest — under any
  config to date. Revisit as a deliberate follow-up once the long side has a real verdict.
