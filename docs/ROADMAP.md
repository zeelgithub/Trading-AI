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

## Agentic orchestrator rebuild (in progress)

Two-plane architecture: the deterministic core stays the always-on safety spine;
a new short-lived **cognitive plane** (`src/agents/`) emits proposals only,
through the same risk gate. Eight architecture decisions are recorded in the
agentic-rebuild memory. Phase by phase, each shipped with tests:

- [x] **Phase 1 — strategy validation & scoreboard** (`src/research/{attribution,
  significance,scoreboard,evaluation}.py`, `scripts/evaluate_strategies.py`):
  per-strategy attribution, bootstrap p-value, Probabilistic Sharpe Ratio, Sidak
  multiple-testing correction, temporal consistency, buy-and-hold benchmark, and
  a persisted scoreboard with a noise/promising/validated verdict. Confirms the
  Step-6b finding statistically: breakout on cached data = NOISE (p≈0.46).
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
- [x] **Live attribution wiring** (`order_manager.realized_pnl`, `orchestrator._record_attribution`):
  real closes (signal exit, stop-fired auto-close) record realized PnL per strategy to the
  scoreboard's live columns (best-effort, never affects a cycle; exit price is a proxy --
  latest close / stop level -- good for ranking, not broker-exact). Closes the learning loop:
  the scoreboard now reflects live results, not just backtests. Visible via `/strategies`.
- [x] **Human-in-the-loop reasoning (no API key)** (`src/notify/briefs.py`): chosen operating
  mode. The bot is fully deterministic + keyless; cognitive jobs are emitted as structured
  paste-into-Claude.ai briefs. Telegram: `/review` (strategy brief), `/brief SYM` (symbol brief),
  `/rotate <action> <strategy> [w]` (propose → Approve/Deny); self-heal escalation sends an
  incident brief. Claude.ai Pro is the human-mediated advisor; the Anthropic-SDK agents stay
  built+tested but DORMANT (future upgrade via API key or a local LLM through ModelClient).
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
  scoreboard (congress ideas → `congress_copy` row). Weight changes stay manual
  (`discovery.weights`) — no self-adjusting black box.

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
