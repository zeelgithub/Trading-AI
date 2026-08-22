# Safeguards

Fail-safes come before strategy. A mediocre strategy with great risk controls survives;
a great strategy with no controls eventually does not. Config:
[`config/risk_limits.yaml`](../config/risk_limits.yaml).

## Tier 1 — Pre-trade gates (block bad orders before they send)

- **Max daily loss (kill switch):** realized + unrealized P&L vs start-of-day equity,
  checked once per cycle (a daily-swing system, not a continuous intraday monitor).
  Cross it -> state `HALTED`, cancel open orders, flatten (in execute mode),
  **require manual reset**. On its own this bounds *new* risk added since the last
  check, not what already-held positions could cost if they all stopped out the
  same day between cycles — that's the next guard.
- **Max aggregate open risk:** `account.max_open_risk_pct` (defaults to
  `max_daily_loss_pct`) caps the sum of `qty * |entry - stop|` across every open
  position, enforced at entry time (`RiskManager.evaluate()` step 7.5, before a
  new position is even opened). This is what makes the kill-switch percentage a
  real worst-case ceiling: even if every held position's resting stop fired on
  the same day (e.g. a broad market selloff, each position's stop is independent
  and unrelated to the portfolio-level kill switch — see "hybrid execution" at
  the bottom of `config/risk_limits.yaml`), the realized loss is bounded near
  that percentage rather than scaling unbounded with `max_open_positions`.
- **Max position / per-symbol exposure** caps.
- **Max gross exposure / leverage** cap.
- **Fat-finger band:** reject orders priced > 20% off last quote.
- **Rate limiting:** max orders per minute and per day (runaway-loop guard).
- **Duplicate-order guard:** idempotency keys so a retry never doubles a position.

## Tier 2 — Connectivity / runtime

- **Disconnect handler:** any data/broker API failure raises and the cycle's
  default-to-halt catch persists a HALT — the bot never keeps trading through
  an outage.
- **Stale-data detector:** a symbol whose newest daily bar is older than
  `data.max_bar_age_days` is skipped for entries, exits, and stop updates; if
  *every* checked symbol is stale the cycle HALTs (`stale_data`, auto-resumable
  by the verified self-healer once data is fresh again).
- **Watchdog:** `scripts/healthcheck.py` is an independent liveness check
  (schedule it separately from the bot itself). Known gap: it is itself just
  another scheduled task, so it can't detect "the machine is asleep/off" or
  "Task Scheduler never fired the probe at all" -- set
  `watchdog.dead_mans_switch_url` (e.g. a free healthchecks.io check) to close
  that gap; a HEALTHY probe pings it, and that external service alerts you if
  the expected ping itself goes missing.
- **Crash-safe state files:** all `state/*.json` writes are atomic
  (temp + rename); a corrupt file is quarantined, never trusted — recovery goes
  through reconciliation, which halts on anything unexpected.

## Tier 3 — Reconciliation / state integrity

- **Broker is source of truth:** on startup and on a timer, compare internal positions/orders
  vs Alpaca. Mismatch -> HALT + alert. Catches "bot thinks it's flat but isn't."
- **Crash recovery:** persisted state in `state/` so restart doesn't double-trade.
- **Idempotent submission:** survive a mid-order crash without duplicating.

## Tier 4 — Human-in-the-loop / observability

- **Alerts** on halts, fills, reconciliation mismatch, daily summary — pushed to
  the phone over Telegram (`src/notify/`).
- **Propose-and-approve** (`approval.require_approval: true`, default): the daily
  cycle decides but places **nothing**; each risk-approved entry is pushed to the
  phone with Approve / Deny and only reaches the broker on approval. `--execute`
  is converted to propose while this is on.
- **Mandatory manual reset** after a kill-switch trip — the bot does not self-resume.
- **Paper/dry-run default**; live trading is an explicit flag.
- **Append-only audit trail**: every signal -> risk verdict -> order -> fill is logged.

### Phone control (Telegram) — auth & boundaries
- The Telegram token is a **notification** credential (`load_notification_credentials`),
  never a trading credential. `src/notify/` holds the token only and never calls
  the broker; phone commands route through `src/core/trade_service`, which runs
  every order through the **risk gate** before execution.
- **Chat-ID allowlist** (`TELEGRAM_ALLOWED_CHAT_IDS`) is enforced on every
  update; an empty allowlist denies everyone. An un-allowlisted `/start` only
  ever reveals the caller's chat id (for setup), nothing else.
- **Approve, /buy, /flatten** require a confirm tap (anti-fat-finger). Every
  order path **refuses while HALTED** (default-to-halt). Phone orders are
  **paper-only** in v1 — live still needs `mode: live` + the explicit flag.
- A proposal **expires** after `approval.proposal_expiry_minutes` so a stale
  idea can't be approved into a moved market; approval **re-runs the risk gate**
  with fresh account state.

## Hybrid stop execution

The current ratchet stop **always rests as a real order at the broker**. The bot only
*raises* it as price climbs. If the bot crashes, the last stop stays active at Alpaca —
the resting stop *is* the backstop; no separate catastrophe order exists (or is needed).

## How a protected entry actually works (fixed 2026-08-11 — real incident, not theoretical)

Entries are **not** submitted as an atomic OTO/bracket (entry + stop in one call). An
earlier version was, on the theory that submitting the parent with `time_in_force=GTC`
would make the attached stop leg persist. It doesn't: **Alpaca does not honor a
requested GTC time-in-force on OTO/bracket child legs** — confirmed against this
account's real order history (a GTC-requested entry+stop came back from Alpaca with
`time_in_force=DAY` on both the parent and the stop leg) and matches Alpaca's own
community forum, which documents this exact limitation and its accepted workaround.
The practical result: 8 real positions had their "protective" stop silently expire at
the same day's close and sat completely naked for weeks before the reconciler's
UNPROTECTED check happened to catch one of them.

The fix (`src/execution/broker_alpaca.py`, `src/execution/order_manager.py`):
1. `OrderManager.open_position` submits the entry **alone**
   (`AlpacaBroker.submit_market_entry`, plain market order, DAY — DAY is fine here,
   a market order fills immediately or not at all, so TIF on the entry was never the
   problem).
2. `OrderManager.settle`, the moment it observes `filled_qty > 0` for the first time,
   attaches protection as a **standalone GTC order** — `submit_stop` (plain GTC stop)
   or `submit_oco_exit` (GTC OCO: stop + take-profit, one cancels the other) when the
   intent has a target. Neither is nested inside the entry, so neither is subject to
   the OTO/bracket per-leg TIF limitation above. The position is only ever marked
   `OPEN` *after* this succeeds.
3. If it fails, the exception propagates out of `settle()` — the position stays
   `PENDING_ENTRY`, `stop_order_id` stays `None`, and the cycle HALTS (rule 3) instead
   of silently continuing with a filled, unprotected position.
4. Retries of step 2 (e.g. after a halt) are safe: the protective order's
   `client_order_id` is derived from the position's own `open_tag` — set once at
   `open_position` and never regenerated — so a retry submits the byte-identical
   client_order_id no matter how much time has passed, and the broker's own
   idempotency guard rejects a true duplicate rather than stacking a second real
   stop order on top of the first.

Test coverage: `tests/unit/test_broker_alpaca.py` (asserts the exact TIF/order_class/
side requested on each of the three order paths against a mocked SDK client — the
class of bug that shipped here is invisible to `FakeBroker`, which just echoes back
whatever's asked for; only a client-level test would have caught it) and
`tests/unit/test_order_manager.py` (settle-triggers-protection, retry idempotency,
propagate-not-swallow on failure).

## Stop order type (gap-down risk) — decided

The protective stop is a **stop-market**, not a stop-limit. Deliberate, not an
oversight: a stop-limit caps the worst-case fill price, but on a large gap it can
go **completely unfilled** if the price never trades back up to the limit — which
leaves the position naked exactly like the OTO-TIF incident above, except
indefinitely rather than for a day. Rule 4 ("no naked positions") and that incident
both weigh toward guaranteed-but-imprecise over precise-but-possibly-never.

The real cost of that choice is a gap CAN still fill worse than the intended stop.
Rather than switch order types and reintroduce naked-position risk, the orchestrator
fetches the stop's **real fill price** from the broker when a position auto-closes
(`Orchestrator._resolve_auto_close_exit`, `src/core/orchestrator.py`) instead of
assuming the intended stop level, and fires a distinct `incident` alert — not just
the routine `fill` one — when the fill is `alerts.gap_slippage_alert_pct` (default
2.0%) or worse than intended (`config/settings.yaml`). This also fixed a smaller,
separate accuracy gap: live strategy attribution used to assume every auto-close
exited exactly at the stop level; it now uses the real fill when the broker reports
one. The existing aggregate open-risk cap (`account.max_open_risk_pct`, Tier 1 above)
still bounds the portfolio-level worst case regardless of any single gap's slippage.

## GTC aged-order policy (90 days) — resting stops are proactively refreshed

Alpaca auto-cancels a GTC order 90 days after creation unless it's modified
again, which resets the clock
([docs.alpaca.markets/us/docs/orders-at-alpaca](https://docs.alpaca.markets/us/docs/orders-at-alpaca);
confirmed directly against the installed alpaca-py SDK's `Order.expires_at`
field). `OrderManager.raise_stop` only replaces the resting stop when the
ratchet actually advances — a flat or losing position's stop can otherwise sit
unmodified indefinitely, and this is a daily-swing system that holds positions
for weeks or months (docs/STRATEGIES.md), so a hold well past 90 days is
realistic, not theoretical.

Each cycle, `Orchestrator._refresh_stale_stops` →
`OrderManager.refresh_stale_stop` checks every OPEN position's resting stop
against its broker-reported `expires_at`; once it's within
`settings.execution.stop_refresh_min_days_remaining` (default 15 days) of
expiring, the stop is replaced at its OWN current price — identical
protection level, but Alpaca treats the replace as a modification and resets
the 90-day clock. Execute-mode only (shadow/propose place nothing); a failed
refresh doesn't halt the cycle (the existing resting stop still protects the
position, just with its aging clock unchanged) but does count toward the
consecutive-error breaker, same posture as a failed `raise_stop`.

## Market-hours rule

New entries are **refused while the market is closed** — everywhere, including
manual `/buy` and phone approvals. A market order queued overnight would fill at
the next open against a stop priced off the previous close, so an overnight gap
could multiply the sized risk. Approve during regular hours instead.

## Golden rule

On any uncertainty, the default state is **HALT / do nothing**, never "guess and trade."
