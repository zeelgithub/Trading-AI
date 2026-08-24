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
- [x] Step 6b: VALIDATION — re-run 2026-08-10 over ~4.1 years (`--full-refetch
  --lookback-days 1500`, up from the original 8-trade/14-month run). Latest numbers
  (post Step 6c/6d risk-layer changes below, plus a survivorship-bias fix to the
  universe and a backtester force-close bug fix — see docs/STRATEGIES.md
  "Survivorship-bias fix"): trend_following VALIDATED (43 trades, PF 3.64,
  Sidak-adjusted p=0.037); breakout INCONCLUSIVE (186 trades, PF 1.70, p=0.054 —
  just past the bar); mean_reversion NOISE and net-negative (48 trades, PF 0.56).
  Portfolio: +38.5% return, Sharpe 1.30, maxDD -5.4% vs. buy-and-hold SPY's
  +113.5%/1.24 — underperforms on raw return, beats SPY's Sharpe with a much
  smaller drawdown. Full detail in docs/STRATEGIES.md "Validation status".
  Do not --execute yet: one strategy clearing the bar is a floor, not a green light —
  see Step 7. Still missing at this point: walk-forward / out-of-sample testing
  (see Step 6e) and any live/paper track record at all (still true, see Step 7).
- [x] Step 6c: confidence-scaled position sizing (`src/risk/risk_manager.py`
  `evaluate()` step 7) — `intent.confidence` (strategy conviction + sentiment-gate
  haircut) now scales the risk budget instead of being computed and ignored; clamped
  so it only ever shrinks, never exceeds the configured per-strategy budget. Manual/
  phone buys pin `confidence=1.0` and are unaffected. See docs/STRATEGIES.md
  "Confidence-scaled sizing".
- [x] Step 6d: aggregate open-risk cap (`evaluate()` step 7.5, new
  `account.max_open_risk_pct` in `config/risk_limits.yaml`, defaults to
  `max_daily_loss_pct`) — bounds the sum of qty*|entry-stop| across ALL open
  positions, so a same-day selloff hitting every held stop is capped near the
  kill switch's own threshold. Previously `max_daily_loss_pct` only checked
  once/cycle against realized+unrealized loss and only blocked new entries;
  nothing capped simultaneous stop-outs across up to `max_open_positions` (10).
  See docs/SAFEGUARDS.md and docs/STRATEGIES.md "Recent risk-layer changes".
- [x] Step 6e: walk-forward / out-of-sample validation (`src/research/walkforward.py`
  `evaluate_walk_forward`, `Backtester.run(..., entries_start=...)`; wired into
  `scripts/evaluate_strategies.py` by default, `--folds N` / `--no-walk-forward`) —
  splits the window into N sequential folds, each an independent backtest (fresh
  equity, no carried-over positions, no entries before the fold's own start) that
  still sees full history for indicator warmup. Closes the specific gap Step 6b
  flagged: the in-sample verdict was partly scored on the same window used to
  hand-tune the regime/entry thresholds. Result (3 folds, same run as above):
  trend_following and breakout hold PF > 1 in every fold including the true
  holdout (fold 3, entirely outside the threshold-tuning window); mean_reversion
  is PF < 1 in every fold and worsening (0.63 -> 0.83 -> 0.21) — sharper evidence
  for the disable decision below than the pooled backtest alone gave. Full table
  in docs/STRATEGIES.md "Walk-forward validation". Does NOT re-fit parameters per
  fold (these strategies fit nothing); does not replace a live/paper track record.
- [x] Fixed a live-blocking bug found 2026-08-11 while preparing for Step 7: a
  real autonomous cycle had HALTED (class `exception`) on `int() argument ...
  not 'NoneType'`. Root cause: alpaca-py types `TradeAccount.daytrade_count` as
  `Optional[int]`, but `AlpacaBroker.get_account()` (`src/execution/broker_alpaca.py`)
  force-cast it with a bare `int(...)`, no None-guard — crashing the FIRST broker
  call of every cycle whenever Alpaca omits the field (a real, SDK-documented
  possibility, e.g. before it's been computed for an account). `AccountState.day_trade_count`
  downstream (`risk_manager.py`) already treated `None` as "skip broker
  reconciliation, trust the local PDT tracker" — the crash was pure missing
  null-handling, not a real safety question. `AccountView.daytrade_count` is now
  `int | None`, matching every downstream consumer, which already expected
  Optional. Also closed a real coverage gap this exposed: `AlpacaBroker` --
  the only module permitted to place orders -- had zero direct tests of its
  alpaca-py response mapping (everything else went through `FakeBroker`, which
  never runs the real translation code); new `tests/unit/test_broker_alpaca.py`
  covers it, mocking the SDK client the same way `test_alpaca_data.py` does.
- [x] Fixed a SECOND, more serious live-blocking bug found the same day, clearing
  the halt above: reconciliation flagged a real position UNPROTECTED. Investigation
  found ALL 8 real open positions had their "atomically attached" protective stop
  expire at the same day's close and sit completely naked for weeks -- Alpaca does
  not honor a requested GTC time-in-force on OTO/bracket child legs (confirmed
  against this account's real order history + Alpaca's own community forum).
  Root-cause fix, not a workaround: entries are no longer submitted as an atomic
  OTO/bracket. `OrderManager.open_position` now submits the entry alone
  (`AlpacaBroker.submit_market_entry`); `OrderManager.settle`, the instant it
  confirms the fill, attaches the stop as its own standalone GTC order
  (`submit_stop`, or `submit_oco_exit` for a GTC OCO when there's a take-profit)
  -- neither is nested under the entry, so neither is subject to the OTO/bracket
  TIF limitation. A position is never marked OPEN until that succeeds; if it
  fails, the exception propagates and HALTS the cycle (rule 3) rather than
  continuing with a filled, unprotected position. Retries are idempotency-safe
  across a halt spanning any amount of time: the protective order's
  client_order_id is derived from the position's own `open_tag`, fixed at
  `open_position` and never regenerated from "today". Full writeup:
  docs/SAFEGUARDS.md "How a protected entry actually works". New tests:
  `tests/unit/test_broker_alpaca.py` (asserts the exact TIF/order_class/side
  requested against a mocked SDK client -- this class of bug is invisible to
  `FakeBroker`, which just echoes back whatever's asked for) and
  `tests/unit/test_order_manager.py` (settle-triggers-protection, retry
  idempotency, propagate-not-swallow on failure). The 8 real positions
  themselves still need a one-time remediation pass using this same new
  mechanism -- not yet run, pending go-ahead.
- [x] Fixed a gap found 2026-08-21 via a research pass cross-referencing this
  project against Alpaca's own docs, the installed alpaca-py SDK, established
  OSS trading engines (NautilusTrader, Freqtrade, Hummingbot, QuantConnect
  LEAN), and regulatory guidance (SEC 15c3-5, FINRA 15-09, FIA's ATS guide) --
  see docs/SAFEGUARDS.md "GTC aged-order policy". Alpaca auto-cancels a GTC
  order 90 days after creation unless modified again (confirmed against the
  installed SDK's `Order.expires_at` field, not just the docs prose).
  `OrderManager.raise_stop` only replaces the resting stop when the ratchet
  actually advances, so a flat or losing position's stop -- realistic on a
  system that holds positions for weeks/months -- could silently expire
  hours after that day's cycle already ran, going naked until the next
  cycle's reconcile caught it. `OrderManager.refresh_stale_stop`, called each
  execute-mode cycle, replaces any resting stop within
  `settings.execution.stop_refresh_min_days_remaining` (default 15d) of
  `expires_at` at its own current price -- resets Alpaca's clock, no
  protection-level change, no new process. The same research pass confirmed
  the existing OTO/bracket fix (above) matches Alpaca's own community-forum
  workaround independently, and that this project's deterministic risk/
  execution core matches every source's definition of production-grade (not
  a gap) -- full findings in the session's plan file.
- [x] Removed the PDT tracker (2026-08-21) -- `src/risk/pdt_tracker.py`,
  `RiskManager`'s PDT gate, `config/risk_limits.yaml`'s `pdt:` block,
  `AccountView.daytrade_count`/`pattern_day_trader`, `AccountState.
  is_intraday`/`day_trade_count`, and every call site (orchestrator,
  discovery/pipeline, trade_service, backtester, scripts, congress_copy).
  Two independent, confirmed reasons: (1) FINRA retired the Pattern Day
  Trader rule (Regulatory Notice 26-10, effective 2026-06-04) for a dynamic
  intraday-margin framework, and Alpaca removed the underlying API fields on
  2026-07-06, recommending `buying_power` instead -- the $25k/4-day-trades
  rule this project enforced no longer exists at the broker. (2) The gate
  was independently dead code regardless: every caller hardcoded
  `is_intraday=False` (this is a daily-swing system, never a same-day
  round-trip), so it had never once fired. `RiskManager.evaluate()`'s step
  numbering is left as-is (step 4 retired, not renumbered) so existing
  "step 7"/"step 7.5" references in docs/STRATEGIES.md and
  docs/SAFEGUARDS.md stay accurate. The original Step 2 entry above and the
  2026-08-11 incident entry are left as written -- real history, not
  rewritten just because the code they describe is now gone.
- [ ] Step 7: paper EXECUTE smoke (--execute) ONLY after validation passes; then small real capital.
  Candidate next moves before that: let trend_following accumulate more live/backtest
  history before deciding; still no strategy has a live paper-trading track record
  backing the backtest verdict.
- [x] `mean_reversion` disabled — it was net-negative (PF 0.57) rather than just
  under-sampled, per Step 6b. Originally applied only via `state/rotation.json`
  (`RotationState.apply("disable", "mean_reversion")`), which is per-deployment
  and gitignored — meaning a fresh clone silently inherited the rejected
  strategy fully enabled, and `build_strategies` never even read the per-strategy
  `enabled:` flag in `config/strategies.yaml` that a new self-hoster would
  reach for instead. Fixed 2026-08-22: `RotationState.is_enabled()` now takes a
  `default` param, seeded from that `enabled:` flag by the orchestrator and the
  phone listener (`RotationService`'s guardrails use the same defaults, so
  "can't disable the last active strategy" still accounts for a
  config-disabled strategy correctly). `config/strategies.yaml` now ships
  `mean_reversion.enabled: false`, so a fresh clone starts matching this
  project's own validated decision; `/rotate enable mean_reversion` (or editing
  the yaml, for the next fresh deployment) both still work as the two ways to
  turn it back on.
- [x] Correlation-aware open-risk cap (2026-08-24, `src/risk/correlation.py`,
  `RiskManager.evaluate()` step 7.6, `account.max_correlated_risk_pct` /
  `correlated_risk_threshold` / `correlation_lookback_days` in
  `config/risk_limits.yaml`) -- added ahead of promoting the cross-sectional
  momentum candidate below, which (unlike the regime-routed trio) can open
  several positions from the same correlated bucket at once. The existing
  step 7.5 aggregate open-risk cap treats every dollar of open risk as
  independent; this tightens it further for a candidate symbol's correlated
  cluster specifically. Wired into the two callers where it matters --
  `src/core/orchestrator.py` (live cycle) and `src/research/backtester.py`
  (so momentum's own validation run is scored under the same cap it would
  face live) -- following the exact rollout precedent `open_risk_dollars`
  set (step 7.5): a new `AccountState.correlated_open_risk_dollars` field,
  default 0.0 (safe no-op) for `trade_service.py` / `discovery/pipeline.py`,
  which aren't yet wired to a price-history source and were left alone
  rather than adding new plumbing beyond this task's scope. Positions whose
  correlation can't be computed (missing/short history) fall back to the
  flat step 7.5 cap only, never silently losing protection. New tests:
  `tests/unit/test_correlation.py` (pure function), 4 new cases in
  `tests/unit/test_risk_manager.py` (step 7.6 resize/veto/no-op/tighter-
  than-7.5). Verified against real cached data, not just unit tests:
  instrumenting `correlated_open_risk` during a momentum backtest run showed
  it fires correctly (80/80 entry considerations evaluated, 7 found a
  correlated cluster, max $913 of correlated risk detected) but never
  actually bound at the current `max_correlated_risk_pct: 2.5` -- a working
  backstop that simply hadn't been tested by this data yet, not a no-op bug.

## Agentic orchestrator rebuild (in progress)

Two-plane architecture: the deterministic core stays the always-on safety spine;
a new short-lived **cognitive plane** (`src/agents/`) emits proposals only,
through the same risk gate. Eight architecture decisions are recorded in the
agentic-rebuild memory. Phase by phase, each shipped with tests:

- [x] **Phase 1 — strategy validation & scoreboard** (`src/research/{attribution,
  significance,scoreboard,evaluation}.py`, `scripts/evaluate_strategies.py`):
  per-strategy attribution, bootstrap p-value, Probabilistic Sharpe Ratio, Sidak
  multiple-testing correction, temporal consistency, buy-and-hold benchmark, and
  a persisted scoreboard with a noise/promising/validated verdict. Updated by the
  2026-08-10 Step-6b re-run: breakout on ~4.1 years of cached data = VALIDATED
  (p≈0.04); see docs/STRATEGIES.md "Validation status".
- [x] **Phase 0 — agent harness** (`src/agents/{model,tools,runtime,dispatch,
  profiles,catalog}.py`): generic short-lived tool-use loop, deterministic
  dispatcher, read/write tool tiers, fully offline-testable via a scripted model.
- [x] **Phase 2 — Telegram NL agent** (`src/agents/nl.py`, `nl_router` profile):
  replaces the regex parser in `run_telegram` with the nl_router agent; regex is
  the graceful fallback when `ANTHROPIC_API_KEY` is absent.
- [x] **Phase 3 — MCP read servers** (`mcp_servers/{market_data,portfolio_state}`,
  `src/data/queries.py`, `src/core/portfolio_view.py`, `src/agents/tools/reads.py`):
  read-only bars/indicators/positions/halt/scoreboard, exposed twice off one set
  of query functions — as MCP servers (Claude Code / Desktop) and as in-process
  read tools (write=False) for agents.
- [x] **Phase 4 — rotation proposals** (`src/core/rotation.py`, `src/agents/tools/writes.py`,
  `src/agents/analyst.py`, `strategy_analyst` profile): the first WRITE tool
  (`propose_rotation`, propose-only, guardrails enforced at propose+approve),
  the strategy_analyst agent (reads scoreboard → recommends enable/disable/reweight),
  and minimal orchestrator wiring (skips a disabled strategy; default = all enabled).
  Proves the full vertical slice: read tool → agent → gated write → approval → core honors it.
  Phone-wired: `/strategies` shows the scoreboard, `/review` runs the analyst and pushes
  rotation proposals with Approve/Deny buttons (run_telegram), approval applies to
  state/rotation.json which the orchestrator reads each cycle.
- [x] **Phase 5 — self-healing** (`src/core/self_heal.py`, `src/agents/triage.py`,
  `anomaly_triage` profile, `scripts/run_self_heal.py`): HALTs are now class-tagged
  (`HaltClass`); a DETERMINISTIC `SelfHealer` auto-resumes ONLY stale-data/disconnect
  halts, gated by verifier + cooldown + daily cap + phone notify. Reconcile-mismatch
  and kill-switch are excluded from the whitelist (never auto-resume). The
  anomaly_triage agent diagnoses + recommends but never clears a halt itself.
  Wiring note (2026-08-21): `scripts/run_self_heal.py`'s escalation path
  claimed to call this agent since Phase 5 shipped, but never actually did --
  a real doc/code gap found during an architecture review, not a design
  change. `_try_agent_diagnosis()` now actually calls it, best-effort,
  ONLY when `ANTHROPIC_API_KEY` is set; any failure (or no key) falls back
  to exactly the deterministic incident brief alone, byte-identical to
  before this existed. See "Human-in-the-loop reasoning" below for why the
  default deployment still doesn't need a key at all.
- [x] **Live attribution wiring** (`order_manager.realized_pnl`, `orchestrator._record_attribution`):
  real closes (signal exit, stop-fired auto-close) record realized PnL per strategy to the
  scoreboard's live columns (best-effort, never affects a cycle; exit price is a proxy --
  latest close / stop level -- good for ranking, not broker-exact). Closes the learning loop:
  the scoreboard now reflects live results, not just backtests. Visible via `/strategies`.
- [x] **Human-in-the-loop reasoning (no API key)** (`src/notify/briefs.py`): chosen operating
  mode. The bot is fully deterministic + keyless; cognitive jobs are emitted as structured
  paste-into-Claude.ai briefs. Telegram: `/review` (strategy brief), `/brief SYM` (symbol brief),
  `/rotate <action> <strategy> [w]` (propose → Approve/Deny); self-heal escalation sends an
  incident brief -- optionally enriched by the anomaly_triage agent (Phase 5, above) when a
  key is present. Claude.ai Pro remains the human-mediated advisor either way.
  Disposition, reviewed 2026-08-21 (architecture plan, see the session's plan file): `nl.py`
  (Telegram's smart parser, regex fallback) and now `triage.py` are live, gated purely by
  key presence -- not "dormant," genuinely optional. `analyst.py` (`StrategyAnalyst`,
  `/review`'s LLM path) remains unwired by deliberate choice: `/review`'s brief-based flow
  already works well and isn't broken, so it was left alone rather than wired for its own
  sake. `src/discovery/weight_advisor.py` (the source-reweighting advisor) deliberately does
  NOT route through this framework either, so the one adaptive feature this project has
  still needs no API key -- see "Phase D2" below.
- [ ] **Phase 6 — scale & observability** (data-driven registries, per-agent budgets, decision audit).

221 tests pass (offline, no creds).

## Discovery plane (idea generation) — built

A new `src/discovery/` plane answers "go *find* me ideas, don't just watch my
list." It gathers buy candidates from free signal sources, scores + ranks them
deterministically, and emits the top N as risk-gated Proposals to the phone
(Approve/Deny per name). It NEVER places an order — same gate, same approval as
everything else. Daily entrypoint `scripts.run_discovery`; on-demand `/ideas`.

- [x] **Phase A — pipeline + congress + technical** (`candidate`, `scorer`,
  `pipeline`, `sources/{congress,technical}`, `universe`, `builder`,
  `notify/digest`, `run_discovery`, `/ideas`): group → score (weighted blend,
  confluence rewarded, renormalised to active sources) → drop held/low-score →
  rank → risk-gate top N → Proposal. Congress source lets brand-new tickers
  surface (with `lag_days` shown); technical source reuses the regime filter +
  strategies and discounts by scoreboard verdict.
- [x] **Phase B — news source** (`data/providers/news.py` Alpaca free news,
  `sources/news.py`): deterministic keyword sentiment; contributes only on
  net-positive coverage. Off by default (`discovery.sources.news`).
- [x] **Phase C — fundamentals source** (`data/providers/fundamentals.py`
  yfinance, `sources/fundamentals.py`): coarse quality lens (profitable /
  growing / real-sized). Off by default (`discovery.sources.fundamentals`).
- [x] **Phase D — source learning** (`discovery/ledger.py`, `/sources`): append-only
  ledger of every surfaced idea + which sources voted; `/sources` summarises
  per-source contribution. Outcome P&L flows through the existing strategy
  scoreboard (congress ideas → `congress_copy` row).
- [x] **Phase D2 — bounded weight advisor** (`discovery/weight_advisor.py`,
  `/reweight`, 2026-08-21): `discovery.weights` is still never self-adjusting —
  that decision from Phase D stands — but it's no longer manual-analysis-only
  either. `/reweight` computes a SUGGESTED reweighting from the ledger's
  already-tracked per-source contribution stats (proposal rate × avg score,
  sources below a sample-size floor left untouched, any single suggestion
  capped to a bounded relative move) and pushes it Approve/Deny, exactly like
  `/rotate` for strategies. Nothing applies without a tap; the formula and its
  inputs are printed in the proposal text, not hidden in a model. This is the
  one place this project's architecture review (see the session's plan file)
  concluded "adaptive, not static" belongs — deliberately not the risk gate,
  sizing, or strategy entry/exit logic, which stay deterministic to match
  every source that review checked (SEC 15c3-5, FINRA 15-09, and how
  NautilusTrader/Freqtrade/Hummingbot/QuantConnect LEAN are all built).

Config: `settings.yaml` → `discovery:` (top_n, min_score, source toggles,
weights, congress filters, universe extras).

255 tests pass (offline, no creds).

## Audit root-cause fixes (done, 84 tests pass)
- [x] Live take-profit exits — via broker bracket/OCO (stop + TP can't both fill)
- [x] Opposite-EMA exits — `Strategy.should_exit`; live `close_position` + backtester parity
- [x] Bracket/OCO support — `submit_protected_entry` (OTO/bracket); no naked positions
- [x] Fill / partial-fill handling — position lifecycle + `settle`; act on `filled_qty`
- [x] Exception-safe cycle — `run_cycle` try/except → HALT; consecutive-error breaker live
- [x] Pending-aware reconcile — unfilled entries no longer false-halt

## Still open (lower priority)
- Connectivity/stale-data/heartbeat guards.
- [x] Real alerts + phone control (Telegram, `src/notify/`): propose-and-approve,
  view positions/stops, `/buy`, `/halt`/`/reset`/`/flatten`. 110 tests pass.
- [x] `confidence` now used in sizing (Step 6c above). Sentiment scorer still inert
  (no news/LLM source wired) — every automated entry gets the gate's neutral haircut
  by default until one is.
- [x] `on_feed_unavailable: skip_gate` implemented (`SentimentGate.apply()`, see
  docs/STRATEGIES.md): a wired scorer that raises now passes the intent through
  unchanged rather than propagating the exception — previously undefined in code
  despite being documented in `config/strategies.yaml`; a sentiment-feed outage on
  a multi-signal day could have tripped the consecutive-error breaker and halted
  with EXCEPTION (manual reset only) over an infra hiccup, not a logic error.
- [x] Read-only account path — `AlpacaAccountReader` (`src/execution/broker_alpaca.py`)
  wraps `AlpacaBroker` by composition and exposes only `get_account`/`list_positions`,
  with no order-placing method reachable. `scan_signals.py`, `congress_copy/run.py`,
  `run_discovery.py`, `run_self_heal.py`, and `demo_trade.py` (all read-only callers
  outside `src/execution/`) now construct this instead of the full `AlpacaBroker`.
  Still holds trading credentials under the hood (Alpaca ties account reads to the
  trading key, no separate read-only key type exists), but can never place, replace,
  or cancel an order even after a future edit to these call sites.
- [x] Moved remaining hardcoded strategy params to config (2026-08-22), same
  values, no behavior change: `regime_filter.atr_slope_lookback` (was
  `_ATR_SLOPE_LOOKBACK` in `regime_filter.py`), `trend_following.conditions.
  pullback_proximity_pct` (was `_PROXIMITY`), and each strategy's `confidence:`
  block/scalar (trend_following's ADX-scaled formula, mean_reversion's and
  breakout's flat 0.6/0.65) — all in `config/strategies.yaml`, schema-validated.
- [x] Fixed a real gap found 2026-08-22 during an architecture-flexibility pass:
  `mode: live` did not, and could not, actually connect to a real account.
  Every entrypoint constructed `AlpacaBroker()` with zero arguments, and
  `paper` defaults to `True` in the constructor — `config.is_live` and
  `--allow-live` only gated whether `Orchestrator.run_cycle()` let a cycle
  *proceed*, never which Alpaca environment the broker's `TradingClient`
  actually pointed at. So even with both flags set, a "live" cycle still
  talked to the paper API. New `build_broker(config, allow_live=...)`
  (`src/execution/broker_alpaca.py`) is now the one place that decides:
  paper unless BOTH `mode: live` in config AND the caller explicitly passes
  `allow_live=True` — only `scripts/run_paper.py`'s `--allow-live` flag ever
  does that, matching what was already documented as the intended mechanism.
  `manual_order.py`, `run_telegram.py` (phone orders are paper-only in v1,
  by construction now, not just by convention), and
  `reattach_missing_stops.py` go through the same factory but never pass
  `allow_live`, so they can never end up live by omission. The orchestrator's
  own `is_live and execute and not allow_live -> HALT` check is left in place
  as an independent second layer — two signals must agree, at two different
  points, before any order reaches a real account. New tests in
  `tests/unit/test_broker_alpaca.py` pin all four combinations (paper stays
  paper with either signal alone; live requires both).

## Findings
- (Resolved) First backtest (3 symbols, ~75 usable days after 200EMA warmup): trend-pullback
  and mean-reversion setups never fired. Root cause was the same-bar entry-timing bug fixed
  in docs/STRATEGIES.md "Entry timing" — both setups fire regularly now (50 and 48 trades
  respectively in the 4.1-year validation run above).
- mean_reversion fires and passes regime/confirmation checks, but is net-negative over 48
  trades (PF 0.62) on the current window, not just under-sampled — see Step 6b.

## Open decisions

None currently open.

Resolved (see docs/STRATEGIES.md "Decided" and "Recent risk-layer changes"):
confirmation/rejection candle definitions, "recent" S/R window for breakout,
shorts-vs-longs-only, sentiment-feed-down behavior. Stop vs stop-limit
(gap-down risk) — decided in favor of stop-market, with real-fill-price gap
detection to compensate; see docs/SAFEGUARDS.md "Stop order type (gap-down
risk) — decided".
