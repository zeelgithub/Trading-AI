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

Updated 2026-08-10 (latest of four re-runs same day — widened history, two
risk-layer changes, then a survivorship-bias fix + a backtester bug fix; see
"Recent risk-layer changes" and "Survivorship-bias fix" below).
`--full-refetch --lookback-days 1500` (~4.1 years, 1029 trading days), 34-symbol
research universe (27 still-listed large caps + 6 real US-equity delistings
inside the window — see "Survivorship-bias fix") + SPY:

| strategy | trades | win% | PF | p(adj) | verdict |
|---|---|---|---|---|---|
| trend_following | 43 | 46.5 | 3.64 | 0.037 | **VALIDATED** |
| breakout | 186 | 39.2 | 1.70 | 0.054 | INCONCLUSIVE (just above the 0.05 bar) |
| mean_reversion | 48 | 37.5 | 0.56 | 1.000 | NOISE — and net-negative (PF < 1); disabled via rotation |

Portfolio (all three combined): +38.5% return, Sharpe 1.30, maxDD -5.4%, 277 trades.
Buy-and-hold SPY over the same window: +113.5% return, Sharpe 1.24. The strategies
underperform on raw return but beat SPY's Sharpe with roughly a third of its
drawdown — expected, since the risk gate caps exposure (max 10 positions, a
fraction of budget risked each, further rationed by the aggregate open-risk cap
below) rather than staying 100%-invested like a passive benchmark; it is not
evidence the bot is broken, but it does mean "beats SPY on return" is not a claim
this system can make.

### Survivorship-bias fix (both changes same day, both affect the numbers above)

- **Universe**: `settings.research.backtest_universe` (`config/settings.yaml`) added
  6 symbols that actually delisted inside the backtest window — SIVB, FRC, SBNY
  (Mar-May 2023 regional-bank failures), RAD (Rite Aid, Ch. 11 Oct 2023), YELL
  (Yellow Corp, ceased operations Aug 2023), PRTY (Party City, Ch. 11 Jan 2023) —
  confirmed via Alpaca to have clean history through their actual last trading day
  (172-682 rows each, vs. 1029 for a symbol that traded the whole window).
  The prior 27-symbol universe was exclusively still-listed survivors, which
  `docs/DATA.md` names as a specific risk ("beware survivorship bias... include
  delisted tickers for honest backtests") that the universe then walked straight
  into. Deliberately excludes take-private/M&A delistings (TWTR, ATVI) — an
  ownership change isn't a value-destroying failure, so it wouldn't correct
  the bias this list exists to fix.
- **Backtester bug fix** (`src/research/backtester.py` `Backtester.run`/`_manage`):
  found while adding the symbols above. A position still open when its symbol's
  data stream ends (delisting -- or previously, just the backtest window ending)
  used to silently vanish: `today not in df.index` skipped it on every later date,
  so it never became a `Trade` and never counted toward win%/PF/significance --
  the most optimistic possible outcome for exactly the trades a survivorship-bias
  fix exists to capture. Now the position force-closes at the last available price
  on the last bar its symbol has (`reason="data_end"`), whether that's a delisting
  or the ordinary end of the window. Regression test:
  `tests/unit/test_backtester.py::test_position_still_open_when_data_ends_force_closes_at_last_price`.

Net effect: small (trend_following 42->43 trades, breakout 179->186, mean_reversion
47->48) -- the 6 added symbols are a small slice of 34, and each strategy's own
selectivity limits how many signals any one symbol contributes. All three verdicts
are unchanged (trend_following still VALIDATED, marginally better p=0.037;
breakout still INCONCLUSIVE, p=0.054; mean_reversion still NOISE). That's a
meaningful result in itself: the prior "VALIDATED" verdict wasn't quietly resting
on excluding the failures, at least not within this 6-symbol correction -- but the
universe is still not a rigorously complete delisted-ticker set, so treat this as
a bias *reduced*, not a bias *eliminated*.

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
  p < 0.05 and the highest profit factor (3.64) of the three — the first strategy
  in this project with a statistically real edge on backtest data. Still "a floor
  to clear, not a green light to go live" (below).
- **breakout** is a genuine borderline case sitting just past the significance
  threshold — worth more history or live paper-trading data before promoting or
  dropping it, not more parameter tuning.
- **mean_reversion** remains net-negative (PF 0.56) across every re-run so far. A
  real candidate for `/review` → disable via rotation (`state/rotation.json`,
  human-approved, never automatic) rather than further tuning — already applied:
  disabled in `state/rotation.json` as of 2026-08-10.

None of this was tuned to look better than it is — no strategy *parameter* was
touched in any of today's four re-runs; only the history window, two risk-layer
fixes, the validation universe, and (separately) a backtester bug fix changed,
and each was made because it was a real gap, not to move these numbers.

The one gap the above still doesn't touch: every number above comes from ONE
in-sample backtest over the whole window — scored, in part, with the same data
used to pick the regime/entry thresholds in the first place (see "Regime
thresholds" above). "Walk-forward validation" below addresses that. The other
remaining gap — no strategy has a single live or paper-traded fill yet — is
still open; see `docs/ROADMAP.md` Step 7.

## Walk-forward validation

`src/research/walkforward.py` (`evaluate_walk_forward`, wired into
`scripts/evaluate_strategies.py` by default — pass `--no-walk-forward` to skip
it, `--folds N` to change the split) answers a narrower, honest question than
the table above: does the SAME fixed logic (nothing is re-fit per fold — these
strategies fit nothing) hold up across chronological slices it wasn't hand-tuned
to fit, instead of one pooled in-sample number? It splits the window into
`n_folds` sequential chunks and runs an independent backtest per fold — fresh
equity, no carried-over positions, no entries before the fold's own start date
(`Backtester.run(..., entries_start=...)`) — while still feeding each fold the
FULL preceding history for indicator warmup (a fold doesn't get a stunted
EMA200). A position still open at a fold boundary force-closes there via the
same "data_end" mechanism from the survivorship-bias fix, so nothing leaks
across folds.

2026-08-10, 3 folds over the same 4.1-year/34-symbol run above:

| fold | window | strategy | trades | win% | PF | p(raw) |
|---|---|---|---|---|---|---|
| 1 | 2022-07-05..2023-11-10 | trend_following | 6 | 50.0 | 3.33 | 0.102 |
| 1 | 2022-07-05..2023-11-10 | breakout | 71 | 36.6 | 1.36 | 0.153 |
| 1 | 2022-07-05..2023-11-10 | mean_reversion | 20 | 40.0 | 0.63 | 0.878 |
| 2 | 2023-11-13..2025-03-27 | trend_following | 22 | 50.0 | 1.71 | 0.109 |
| 2 | 2023-11-13..2025-03-27 | breakout | 70 | 41.4 | 1.46 | 0.123 |
| 2 | 2023-11-13..2025-03-27 | mean_reversion | 15 | 46.7 | 0.83 | 0.607 |
| 3 (holdout) | 2025-03-28..2026-08-10 | trend_following | 15 | 46.7 | 6.28 | 0.030 |
| 3 (holdout) | 2025-03-28..2026-08-10 | breakout | 41 | 31.7 | 2.14 | 0.324 |
| 3 (holdout) | 2025-03-28..2026-08-10 | mean_reversion | 12 | 16.7 | 0.21 | 0.999 |

Per-fold trade counts are too small for a formal per-fold verdict (that's not
the point — read PF direction, not p(raw), which is deliberately NOT
Sidak-adjusted here). Reading:
- **trend_following**: PF > 1 in all three folds (3.33, 1.71, 6.28), including
  fold 3 — the only fold entirely outside the ~14-month window the regime
  thresholds were originally tuned against, i.e. the closest thing this project
  has to a genuine out-of-sample holdout. The in-sample VALIDATED verdict is
  not resting on a period the thresholds were fitted to.
- **breakout**: also PF > 1 in every fold (1.36, 1.46, 2.14) and, like
  trend_following, strongest in the holdout fold — consistent with its
  in-sample INCONCLUSIVE-but-close verdict; worth continued tracking, not
  promotion or dismissal on this alone.
- **mean_reversion**: PF < 1 in every fold, and getting WORSE over time (0.63 ->
  0.83 -> 0.21) — sharper evidence for disabling it than the pooled PF 0.56
  alone gave. Already disabled via rotation (see above).

`scripts/evaluate_strategies.py` defaults to a wider, sector-diverse validation universe
(`settings.research.backtest_universe`, 34 symbols, including delisted names — see
"Survivorship-bias fix" above) — separate from the live watchlist, which stays small
and explicit. Re-run after any strategy-logic OR risk-logic change, or periodically to
extend the window as more live history accumulates:

    python -m scripts.evaluate_strategies --full-refetch --lookback-days 1500

A "validated" verdict — in-sample or walk-forward — is a floor to clear, not
authorization to trade live.

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
