# Strategy System

Three strategies on **daily bars** (swing holds), routed by a **regime filter** so that
only one strategy trades a given symbol at a time. Equal risk budget across strategies.
Config: [`config/strategies.yaml`](../config/strategies.yaml). Two further candidates
(cross-sectional momentum, 52-week-high anchoring momentum) are under research — not
live-wired, not config-registered; see the "Strategy candidate" sections below.

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

Updated 2026-08-24 (routine data refresh — see "2026-08-24 re-run" below for
what changed vs. the 2026-08-10 numbers this replaces; "Recent risk-layer
changes" and "Survivorship-bias fix" further below are the historical record
of THAT day's actual logic/risk changes, not this one).
`--full-refetch --lookback-days 1500` (~4.1 years, 1030 trading days), 34-symbol
research universe (27 still-listed large caps + 6 real US-equity delistings
inside the window — see "Survivorship-bias fix") + SPY:

| strategy | trades | win% | PF | p(adj) | verdict |
|---|---|---|---|---|---|
| trend_following | 43 | 46.5 | 3.60 | 0.040 | **VALIDATED** |
| breakout | 184 | 39.1 | 1.70 | 0.066 | INCONCLUSIVE |
| mean_reversion | 48 | 37.5 | 0.56 | 1.000 | NOISE — and net-negative (PF < 1); disabled via rotation |

Portfolio (all three combined): +38.1% return, Sharpe 1.29, maxDD -5.4%, 275 trades.
Buy-and-hold SPY over the same window: +110.9% return, Sharpe 1.21. The strategies
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
  (`config/strategies.yaml` → `sentiment_gate.neutral_confidence_haircut`,
  default 0.8 — the default today, since no sentiment source is wired). This was previously computed and
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

### 2026-08-24 re-run — routine data refresh, no logic changes

Re-run for one reason only: ~2 more weeks of real trading history accumulated
since 2026-08-10, and this file says to re-run "periodically to extend the
window" (below) as that happens. No strategy parameter, no risk-layer setting,
no config value was touched between the two runs — same `--full-refetch
--lookback-days 1500`, same 34-symbol universe, same code. Reported honestly,
in both directions, not just the flattering one:

- **trend_following**: p(adj) moved 0.037 → 0.040 — still comfortably
  VALIDATED, still the strongest profile of the three, but the two more weeks
  of data made the margin slightly thinner, not thicker. Worth watching, not
  acting on.
- **breakout**: p(adj) moved 0.054 → 0.066 — still INCONCLUSIVE, and further
  from the 0.05 bar than before, not closer. The "just above the bar"
  framing from the prior write-up no longer fits; this is now a clearer miss,
  even though PF (1.70) and trade count (184, barely changed from 186) look
  almost identical. More history didn't resolve the ambiguity in breakout's
  favor — an honest re-run doesn't get to assume it will.
- **mean_reversion**: unchanged (48 trades, PF 0.56, NOISE) — the extra two
  weeks contributed nothing new to this universe/window combination.

This is exactly what re-running periodically is *for*: two more weeks either
would have pushed breakout over the bar, confirmed it as noise, or (what
actually happened) left it genuinely ambiguous with a slightly weaker number.
All three outcomes are legitimate; only one of them is what showed up, and
that's the one reported above.

Reading:
- **trend_following** clears `min_trades=30` with a real, Sidak-adjusted
  p < 0.05 and the highest profit factor (3.60) of the three — the first
  strategy in this project with a statistically real edge on backtest data.
  Still "a floor to clear, not a green light to go live" (below).
- **breakout** remains a borderline case past the significance threshold —
  worth more history or live paper-trading data before promoting or dropping
  it, not more parameter tuning. Two more weeks of data made it marginally
  LESS convincing, not more — a reason for patience, not for forcing a verdict
  either direction.
- **mean_reversion** remains net-negative (PF 0.56) across every re-run so far,
  now including this one. A real candidate for `/review` → disable via
  rotation (`state/rotation.json`, human-approved, never automatic) rather
  than further tuning — already applied: disabled in `state/rotation.json` as
  of 2026-08-10, unchanged by this re-run.

None of this was tuned to look better than it is — no strategy *parameter* has
been touched since the 2026-08-10 risk-layer/universe changes described above;
only the history window moved, in both this re-run and the four on 2026-08-10,
and each was made because it was time to check the numbers again, not to move
them.

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

2026-08-24, 3 folds over the same 4.1-year/34-symbol run above (fold boundaries
shift slightly re-run to re-run since they're computed as equal slices of a
window that now ends ~2 weeks later; supersedes the 2026-08-10 table this
replaces):

| fold | window | strategy | trades | win% | PF | p(raw) |
|---|---|---|---|---|---|---|
| 1 | 2022-07-05..2023-11-16 | trend_following | 6 | 50.0 | 3.34 | 0.101 |
| 1 | 2022-07-05..2023-11-16 | breakout | 70 | 41.4 | 1.52 | 0.109 |
| 1 | 2022-07-05..2023-11-16 | mean_reversion | 20 | 40.0 | 0.63 | 0.878 |
| 2 | 2023-11-17..2025-04-07 | trend_following | 23 | 43.5 | 1.37 | 0.237 |
| 2 | 2023-11-17..2025-04-07 | breakout | 76 | 39.5 | 1.35 | 0.114 |
| 2 | 2023-11-17..2025-04-07 | mean_reversion | 16 | 50.0 | 0.90 | 0.605 |
| 3 (holdout) | 2025-04-08..2026-08-24 | trend_following | 16 | 43.8 | 6.11 | 0.049 |
| 3 (holdout) | 2025-04-08..2026-08-24 | breakout | 40 | 30.0 | 2.05 | 0.327 |
| 3 (holdout) | 2025-04-08..2026-08-24 | mean_reversion | 11 | 18.2 | 0.24 | 0.995 |

Per-fold trade counts are too small for a formal per-fold verdict (that's not
the point — read PF direction, not p(raw), which is deliberately NOT
Sidak-adjusted here). Reading:
- **trend_following**: PF > 1 in all three folds (3.34, 1.37, 6.11), including
  fold 3 — the only fold entirely outside the ~14-month window the regime
  thresholds were originally tuned against, i.e. the closest thing this project
  has to a genuine out-of-sample holdout. The in-sample VALIDATED verdict is
  not resting on a period the thresholds were fitted to. Fold 2's PF eased
  from 1.71 to 1.37 with two more weeks of data — still comfortably above 1,
  consistent with the in-sample p-value also thinning slightly (0.037 → 0.040)
  above.
- **breakout**: also PF > 1 in every fold (1.52, 1.35, 2.05) — still
  consistent, directionally, with its in-sample INCONCLUSIVE verdict; worth
  continued tracking, not promotion or dismissal on this alone. Unlike
  trend_following, this consistency across folds hasn't translated into the
  in-sample p-value actually crossing the significance bar even after two
  more re-runs' worth of data.
- **mean_reversion**: PF < 1 in every fold (0.63, 0.90, 0.24) — not a clean
  monotonic decline like the prior table suggested, but the holdout fold
  (0.24) is by far the weakest, and the pooled PF (0.56) hasn't moved.
  Sharper evidence for disabling it than the pooled number alone gives.
  Already disabled via rotation (see above).

`scripts/evaluate_strategies.py` defaults to a wider, sector-diverse validation universe
(`settings.research.backtest_universe`, 34 symbols, including delisted names — see
"Survivorship-bias fix" above) — separate from the live watchlist, which stays small
and explicit. Re-run after any strategy-logic OR risk-logic change, or periodically to
extend the window as more live history accumulates:

    python -m scripts.evaluate_strategies --full-refetch --lookback-days 1500

A "validated" verdict — in-sample or walk-forward — is a floor to clear, not
authorization to trade live.

## Strategy candidate — Cross-sectional Momentum (research-only, not live-wired)

2026-08-24. Structurally different from the three strategies above:
`Strategy.generate()`'s signature (`symbol, features`) only ever sees ONE symbol's
own history — none of the three above can ask "how is this symbol doing relative
to the rest of the universe today," which is the actual academic definition of
momentum (Jegadeesh & Titman 1993, "returns to buying winners and selling
losers"). Rather than change that interface — every live/discovery/backtester
caller depends on it — the cross-symbol comparison is precomputed as ordinary
feature columns, upstream of the strategy itself:

- `src/research/cross_sectional.add_cross_sectional_momentum` (offline, pure
  pandas over already-fetched bars): trailing `lookback`-day return (default
  126, ~6 months), ending `skip` trading days ago (default 21, ~1 month — the
  standard fix for short-term reversal contaminating a momentum signal). A
  symbol is in the "top bucket" on a date if its formation return ranks in the
  top `top_pct` (default 20%) of all symbols with valid data that date;
  `momentum_percentile` (0-1) is also kept, for confidence scaling and a
  symmetric bottom-bucket short leg (the academic "sell losers" side).
- `src/strategy/momentum.Momentum` then reads those precomputed columns
  through the EXACT SAME single-symbol `generate(symbol, features)` signature
  every other strategy uses — zero interface changes, zero live-path changes.
  **Entry:** fires the day a symbol NEWLY enters its bucket (a transition,
  not "already in bucket → buy the already-extended move"), confirmed by the
  same real-bodied confirmation candle every other strategy requires.
  **Exit:** the moment it falls back OUT of the bucket — mirrors
  trend_following's opposite-EMA-break design, a pure function of today's
  state, no remembered entry-time context needed.

**Isolation, by design:** not decorated with `@register`, no block in
`config/strategies.yaml` — `src/strategy/registry.build_strategies()` (used by
the live orchestrator, discovery, and the default backtester construction) can
never instantiate this without an explicit code change. Evaluated only via
`scripts/research_momentum.py`, which registers it into `REGISTRY` for that
process's own lifetime and never writes to `config/*.yaml` or touches the
broker.

**Regime routing doesn't apply:** the three strategies above are mutually
exclusive per symbol per day, picked by the regime filter. Momentum's edge
isn't regime-conditional in that sense — evaluating it inside the regime
router would mix its results into the trio's rather than isolating its own.
`Backtester.run(..., force_strategy="momentum")` (new — `src/research/
backtester.py`, also threaded through `evaluate_walk_forward`) bypasses regime
routing entirely and considers momentum for every symbol every day instead.
`force_strategy=None` (the default) preserves the regime-routed trio's
behavior exactly — this had zero effect on any number in "Validation status"
or "Walk-forward validation" above.

**Results** (`python -m scripts.research_momentum`, same survivorship-bias-
corrected universe and 1500-day/~4.1-year lookback as the validated study
above, formation=126d/skip=21d/bucket=20%):

| trial | trades | win% | PF | p-value | PSR | consistency | verdict |
|---|---|---|---|---|---|---|---|
| in-sample | 80 | 52.5 | 3.66 | 0.017 | 1.00 | 1.00 | **VALIDATED** |

| fold | window | trades | win% | PF | p(raw) |
|---|---|---|---|---|---|
| 1 | 2022-07-05..2023-11-16 | 13 | 38.5 | 10.38 | 0.056 |
| 2 | 2023-11-17..2025-04-07 | 42 | 52.4 | 2.14 | 0.252 |
| 3 (holdout) | 2025-04-08..2026-08-24 | 25 | 60.0 | 3.71 | 0.083 |

(Re-run 2026-08-24 alongside the three-strategy re-run above, on the same
freshly-refetched data — a routine data refresh, not a parameter change;
numbers moved marginally in momentum's favor this time, e.g. p 0.019→0.017,
but that direction isn't guaranteed on the next re-run either, same caveat as
trend_following's own re-run above.)

PF stays above 1 in every fold, including fold 3 — the true out-of-sample
holdout, entirely outside any window used to pick the 126/21/20% parameters
(which came from the academic literature, not fit to this data). The holdout
fold has the highest win rate (60%) of the three, and PF second only to
fold 1's small-sample spike — if anything the strongest slice, not the
weakest.

**Caveats — held to the same bar as trend_following, not a lower one:**
- The p=0.019 above is a STANDALONE trial (n=1) — the script itself prints a
  note that if this is treated as a 4th trial in the same family as the
  original 3-strategy study, the honest comparison is Sidak-adjusted for n=4,
  stricter than what's shown.
- "Validated" (in-sample or walk-forward) is a floor to clear, not
  authorization to trade live — see "Walk-forward validation" above. No live
  or paper-traded fill exists for this candidate at all.
- Structurally different from the other three in a way the risk layer didn't
  originally account for: cross-sectional momentum can open several positions
  from the SAME correlated bucket at once (e.g. a sector rally), which the
  flat aggregate open-risk cap (step 7.5) treats as independent risk. A
  correlation-aware tightening (`RiskManager.evaluate()` step 7.6,
  `src/risk/correlation.py`, `account.max_correlated_risk_pct` in
  `config/risk_limits.yaml`) was added ahead of this write-up specifically so
  the numbers above are scored under the same cap this strategy would face
  live — see docs/SAFEGUARDS.md. It did not change the numbers above
  (confirmed by instrumenting the real backtest run on the 2026-08-24
  refreshed data: the cap evaluated all 80 entries, fired on 7, peaking at
  $938 of correlated risk, but never actually bound at the current 2.5%
  threshold) — which is itself informative: the correlated-cluster scenario
  it exists for is real but rare on this particular universe/window, not a
  false alarm invented to justify the cap.
- Not yet promoted: no `@register`, no `config/strategies.yaml` block, and no
  decision made on how it would coexist with regime routing live (run
  alongside the trio? replace regime-gating with something else for this one
  symbol-set?). Promotion is an open decision, not a default next step — the
  same bar every strategy in this file has been held to.

## Strategy candidate — 52-Week-High Anchoring Momentum (research-only, not live-wired)

2026-08-24. A third, structurally distinct candidate: George & Hwang (2004)
"The 52-Week High and Momentum Investing" — investors anchor on a stock's own
trailing 52-week high as a reference price and are slow to bid above it even
on good news, so a stock reaching or breaking that anchor tends to keep
drifting for weeks as the market gradually re-rates it. Distinct from both
strategies above it in this file: trend_following reads EMA/ADX trend
structure; cross-sectional momentum ranks a symbol's trailing return AGAINST
THE REST OF THE UNIVERSE on a given date. This one is single-symbol and
absolute-threshold — the anchor is a stock's own price history, nothing else
— so unlike cross-sectional momentum it needed no cross-symbol precompute
step (`src/research/cross_sectional.py`); `src/strategy/week52_high.py`
computes its rolling 252-bar high/low directly off `features`'s own
high/low/close columns, the same inline style `src/strategy/breakout.py`
uses for its support/resistance window.

- **Entry:** fires the day a symbol NEWLY enters the "within `proximity_pct`
  of its trailing 252-bar high" zone (default 5%) — a transition, not
  "already near the high → buy the extended move" (same bucket-transition
  design as cross-sectional momentum, applied to a different measure).
  Confirmed by the same real-bodied confirmation candle every other strategy
  requires. Symmetric short leg: newly enters the near-252-bar-low zone (the
  anchoring literature documents a weaker but real symmetric downside
  effect), gated by `shorts_allowed()` like every other strategy's short
  leg.
- **Exit:** no signal-based exit — `should_exit` uses the base class's "no
  signal" default, riding the ATR ratchet alone (2x initial, 1.5x trail,
  reused from breakout's own tested defaults as a starting point, not yet
  independently tuned). Deliberate: the entry is a punctual breakthrough
  EVENT, not an ongoing state like momentum's bucket membership, so
  re-checking "still near the anchor" daily would exit on the very next small
  pullback and cut off the drift this strategy exists to capture — same
  exit philosophy as breakout, which is also event-triggered and also
  ATR-ratchet-only.

**Isolation, by design:** same as the momentum candidate above — not
decorated with `@register`, no block in `config/strategies.yaml`,
`src/strategy/registry.build_strategies()` can never instantiate this
without an explicit code change. Evaluated only via
`scripts/research_week52_high.py`, registering into `REGISTRY` for that
process's own lifetime, never writing to `config/*.yaml` or touching the
broker. Also uses `Backtester.run(..., force_strategy="week52_high")` to
bypass regime routing — this candidate isn't regime-conditional either.

**Results** (`python -m scripts.research_week52_high`, same
survivorship-bias-corrected universe and 1500-day/~4.1-year lookback as the
other studies above):

| trial | trades | win% | PF | p-value | PSR | consistency | verdict |
|---|---|---|---|---|---|---|---|
| in-sample | 345 | 42.0 | 1.62 | 0.002 | 1.00 | 0.75 | **VALIDATED** |

| fold | window | trades | win% | PF | p(raw) |
|---|---|---|---|---|---|
| 1 | 2022-07-05..2023-11-16 | 36 | 44.4 | 0.99 | 0.562 |
| 2 | 2023-11-17..2025-04-07 | 146 | 43.1 | 1.76 | 0.003 |
| 3 (holdout) | 2025-04-08..2026-08-24 | 164 | 40.8 | 1.61 | 0.040 |

A materially different shape from every other candidate in this file: far
more trades (345 vs. trend_following's 43 or momentum's 80 — a 5%-wide
proximity band on a 252-bar anchor fires often across 34 symbols) at a much
thinner per-trade edge (PF 1.62 vs. 3.60–3.66 for the others). High trade
count is exactly why it clears significance easily (p=0.002) despite the
thin edge — a real, if less dramatic-looking, statistical result, not a
weaker one.

**Caveats — held to the same bar as the others, and this one has a real
weak spot the others don't:**
- Fold 1 is flat-to-negative (PF 0.99, 36 trades) — the ONLY fold, across
  all three candidates evaluated in this file, where a fold's PF sits at or
  below 1. Folds 2 and 3 are solidly profitable (PF 1.76, 1.61), including
  the true holdout, but this is not the clean "PF > 1 in every fold" result
  trend_following and momentum both produced. Read as a real, reported
  weakness, not smoothed over by the strong in-sample number.
- The p=0.002 above is a STANDALONE trial (n=1) — if treated as a 5th trial
  alongside the original 3-strategy study and the momentum candidate, the
  honest comparison is Sidak-adjusted for n=5, stricter than what's shown.
- "Validated" (in-sample or walk-forward) is a floor to clear, not
  authorization to trade live — same as every other candidate here. No live
  or paper-traded fill exists for this one either.
- The ATR ratchet multiples (2x initial, 1.5x trail) are reused from
  breakout's tuned values, not independently fit for this strategy's own
  dynamics — a reasonable starting point given the shared ATR-ratchet-only
  exit philosophy, but genuinely untested as a choice specific to this
  candidate.
- The correlation-aware risk cap (`RiskManager.evaluate()` step 7.6,
  `src/risk/correlation.py`, see the momentum write-up above and
  docs/SAFEGUARDS.md) applies to ANY strategy's positions at the risk-gate
  level, not per-strategy — so it would already cover this candidate's
  entries too if it were ever live-wired, without further changes.
- Not yet promoted: no `@register`, no `config/strategies.yaml` block, no
  decision on regime-routing coexistence, no ATR-multiple tuning pass.
  Promotion is an open decision, not a default next step.

## Tested, rejected: two research-backed hypotheses that didn't hold up

2026-08-21, as part of an architecture/strategy review that also verified the
indicator formulas (RSI/ATR/ADX all trace through as standard Wilder
implementations; Bollinger std ddof=0 vs 1 has no clear official convention
either way and a negligible effect at period=20 -- both left as-is). Two
changes were well-grounded in general trading literature, implemented as
config-toggleable additions, and tested against this project's own 34-symbol/
4.1-year cached backtest via `evaluate_strategies --offline` before being kept
or discarded -- same discipline as every other change in this file test
first, keep only what the numbers support.

### Mean reversion trend filter (tested, rejected)

**Hypothesis:** mean_reversion's documented "falling knife" risk (buying an
oversold stock that's oversold because it's in a structural downtrend, not a
temporary stretch) is exactly what a long-term trend-direction filter is
supposed to fix in the broader mean-reversion literature -- require
`close > ema200` for a long dip-buy, `close < ema200` for a short rip-sell.
Distinct from the regime filter's ADX gate above (trend STRENGTH, not
DIRECTION).

**Result:** worse on both axes at once. Trade count collapsed 48 -> 10 and
win rate dropped 37.5% -> 30% (PF 0.56 -> 0.40). Inspecting the 10 surviving
trades: the losses were ordinary large-caps hitting a routine 2% stop (JNJ,
CAT, HD, GE, META), not the crisis/delisted names (SIVB, FRC, SBNY) the
filter was conceptually meant to screen out. ema200 alignment -- a long-term,
macro signal -- isn't the variable actually driving this SHORT-term (20-day
Bollinger) strategy's weakness on this universe; it just cut the sample to a
size too small to trust either way.

**Disposition:** `require_trend_alignment` (`config/strategies.yaml` ->
`mean_reversion.conditions`) defaults to `false`. Code kept as toggleable
infrastructure, not deleted, in case a better-targeted variant (a shorter MA,
a softer condition, a margin instead of a hard boundary) is worth trying once
mean_reversion has more live/paper history to test against. Does not change
mean_reversion's live status -- it's already disabled via rotation (NOISE,
net-negative) independent of this filter.

### Trend-following RSI band (tested, rejected)

**Hypothesis:** this strategy's own documented "known risk" -- *"RSI <= 70
cap can filter out the strongest trends"* -- is a real, literature-confirmed
tradeoff (RSI can stay pinned extended through a genuine sustained trend, not
just chop). Widening the band (40-70 -> 40-80 long, 30-60 -> 20-60 short)
should let more of those trades through.

**Result:** also worse on both axes. Trade count rose 43 -> 48 (+5), but win
rate fell 46.5% -> 39.6% and PF fell 3.64 -> 2.49 -- enough to drop the
verdict from VALIDATED to NOISE (p 0.037 -> 0.148, past the significance
bar). The 5 additional trades the wider band let through were lower-quality
on average, diluting the strategy's edge rather than capturing missed strong
trends. Portfolio-wide effects were negative too (Sharpe 1.27 -> 1.04, maxDD
-5.4% -> -6.9%) -- partly the strategy itself, partly the shared risk budget:
more trend_following entries competing for the same daily risk allocation
left less room for breakout's own signals (186 -> 183 trades).

**Disposition:** bands stay at their original, evidence-tuned 40-70 / 30-60.
Real, separate finding kept regardless of the rejected hypothesis: the bands
were DECLARED in `config/strategies.yaml` but hardcoded as literals in
`src/strategy/trend_following.py` -- editing the config silently did nothing.
Now genuinely wired through, so a future, better-targeted variant is
actually testable via config instead of requiring a code change.

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
