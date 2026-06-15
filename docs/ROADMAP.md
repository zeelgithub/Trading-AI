# Build Roadmap

De-risked order of construction. Each step is testable before the next.

1. **Data + quality pipeline** — daily bars, splits/dividends, gap detection, point-in-time
   correctness. (`src/data`)
2. **Risk layer + safeguards + PDT tracker** — built and unit-tested *before* any live
   strategy. Pure logic, deterministic. (`src/risk`)
3. **Execution layer against Alpaca paper** + reconciliation. (`src/execution`)
4. **Regime filter + one strategy per lane** — prove the plumbing end-to-end in
   paper-shadow (log what it *would* do). (`src/strategy`)
5. **Backtest -> walk-forward -> paper-trade** for a meaningful window. (`src/research`)
6. **Layer in sentiment gate** — forward-only, still non-executing.
7. **Small real capital**, semi-autonomous, all safeguards live.

## Current status

- [x] Architecture + blueprint
- [x] Strategy specs (3-strategy regime system + ratchet stop)
- [x] Connection verified (paper order placed)
- [x] Project scaffold (structure, config, docs, contracts)
- [x] Step 1: data pipeline (indicators, ingest, store, features) — verified end-to-end
- [x] Step 2: risk layer (gatekeeper, sizing, ratchet stop, breakers, PDT) — 30 unit tests pass
- [x] Step 3: execution layer (broker client, order manager, reconciler) — 45 tests pass; tested with fake broker, no live orders yet
- [x] Step 4: regime filter + 3 strategies + sentiment gate (paper-shadow) — 59 tests pass; scan_signals runs full chain on live data, keys rotated
- [x] Step 5: orchestrator (state machine, reconcile, kill switch, ratchet raise, exec guards, state persistence) — 69 tests pass; ran live in SHADOW, no orders
- [x] Step 6: backtester + metrics (event-driven, next-open fills, reuses live components) — 73 tests pass
- [ ] Step 6b: VALIDATION — more history (SIP/longer), more symbols, walk-forward. First run was weak (8 trades, PF 1.17, all breakout) — NOT proven. Do not --execute yet.
- [ ] Step 7: paper EXECUTE smoke (--execute) ONLY after validation passes; then small real capital

## Audit root-cause fixes (done, 84 tests pass)
- [x] Live take-profit exits — via broker bracket/OCO (stop + TP can't both fill)
- [x] Opposite-EMA exits — `Strategy.should_exit`; live `close_position` + backtester parity
- [x] Bracket/OCO support — `submit_protected_entry` (OTO/bracket); no naked positions
- [x] Fill / partial-fill handling — position lifecycle + `settle`; act on `filled_qty`
- [x] Exception-safe cycle — `run_cycle` try/except → HALT; consecutive-error breaker live
- [x] Pending-aware reconcile — unfilled entries no longer false-halt

## Still open (lower priority)
- Connectivity/stale-data/heartbeat guards; real alerts.
- `confidence` unused in sizing; sentiment scorer inert (no news source).
- Read-only account path (remove trading-cred construction in scan_signals / congress_copy).
- Move remaining hardcoded params to config.

## Findings
- First backtest (3 symbols, ~75 usable days after 200EMA warmup): trend-pullback and mean-reversion setups never fired — only breakout traded. Investigate whether pullback/MR entry conditions are too strict or the window too short.

## Open decisions

- Confirmation/rejection candle precise definitions.
- "Recent" S/R detection window for breakout.
- Shorts in v1 vs longs-only first.
- Stop vs stop-limit (gap-down risk).
- Sentiment-feed-down behavior (skip gate vs halt).
