# Safeguards

Fail-safes come before strategy. A mediocre strategy with great risk controls survives;
a great strategy with no controls eventually does not. Config:
[`config/risk_limits.yaml`](../config/risk_limits.yaml).

## Tier 1 — Pre-trade gates (block bad orders before they send)

- **Max daily loss (kill switch):** realized + unrealized P&L vs start-of-day equity.
  Cross it -> state `HALTED`, cancel open orders, optionally flatten, **require manual reset**.
- **Max position / per-symbol exposure** caps.
- **Max gross exposure / leverage** cap.
- **Fat-finger band:** reject orders priced > 20% off last quote.
- **Rate limiting:** max orders per minute and per day (runaway-loop guard).
- **Duplicate-order guard:** idempotency keys so a retry never doubles a position.

## Tier 2 — Connectivity / runtime

- **Disconnect handler:** data or broker drop -> stop initiating trades (never trade on
  stale data).
- **Heartbeat / watchdog:** independent ping; unresponsive bot -> flatten-and-halt.
- **Stale-data detector:** quote older than `stale_seconds` => treated as disconnected.
- **Backoff with hard cap:** retry transient errors, then HALT rather than hammering.
- **Graceful shutdown:** cancel working orders, persist state on SIGINT/crash.

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
*raises* it as price climbs. If the bot crashes, the last stop stays active at Alpaca; a
wider catastrophe stop (entry -15%) sits behind it as a backstop.

## Golden rule

On any uncertainty, the default state is **HALT / do nothing**, never "guess and trade."
