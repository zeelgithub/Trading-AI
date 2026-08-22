# Daily Workflow — Human-in-the-Loop (no Anthropic API key)

You are the reasoning layer. The bot runs **deterministically and keyless**: it
proposes, summarizes, and halts on its own, but every judgment call comes from
**you** — usually after pasting a bot-generated brief into **Claude.ai** on your
phone. Then you act in Telegram.

```
   BOT (deterministic)  ──briefs/proposals──►  YOU + Claude.ai  ──decision──►  Telegram
   scans, sizes, halts                         (advice on phone)               approve / deny / rotate / reset
```

No automated agent calls out. No API key. Claude.ai is a human advisor, not an
agent in the loop.

---

## One-time setup

`.env` needs (see `.env.example`):

- `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` — paper trading + market data.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` — phone control.
- **No `ANTHROPIC_API_KEY`.** Leave it unset — natural-language parsing falls back
  to the local rule-based parser automatically.

Keep two things running:

- **Telegram listener (always-on):** `python -m scripts.run_telegram`,
  supervised to restart on failure — Windows Task Scheduler or a systemd
  service; see the README's "Running continuously" for both, with copy-paste
  examples.
- **Daily decision cycle (scheduled ~15:45 ET, weekdays):**
  `python -m scripts.run_paper --execute` — with `approval.require_approval: true`
  (default) this becomes *propose-and-approve*: it pushes trade proposals to your
  phone and places nothing until you tap Approve.
- **Optional — self-heal ticks:** schedule `python -m scripts.run_self_heal` every
  few minutes so transient halts auto-resume and manual-only halts send you an
  incident brief.
- **Daily discovery (scheduled ~15:45 ET, weekdays):**
  `python -m scripts.run_discovery` — gathers buy ideas from the enabled free
  sources (congress + technical by default), scores and ranks them, and pushes
  the top N to your phone as Approve/Deny suggestions. Like everything else it
  places nothing until you approve. (You can also pull ideas on demand any time
  with `/ideas`.)

---

## The daily loop

### 1. Morning — glance (1 min)
On your phone:
- `/status` — equity, day P&L, open positions, stops, halt state.
- If it says **🛑 HALTED**, jump to *§ When it halts*.

### 2. When the bot proposes a trade (≈15:45 ET)
You get a `🟡 PROPOSED` message with Approve / Deny buttons. Before deciding:
- Optionally `/brief SYM` (e.g. `/brief NVDA`) → copy the brief → paste into
  **Claude.ai** → ask "reasonable swing entry? key risk? stop?"
- Read the advice, then tap **✅ Approve** or **❌ Deny** on the proposal.

Approve re-runs the risk gate with fresh account state before placing anything.

### 3. When the bot suggests fresh ideas (≈15:45 ET, or `/ideas` on demand)
You get a `📊 DAILY IDEAS` header then one message per ranked idea — each with a
star rating, the reasons it surfaced (e.g. *Congress: Rep. X bought $1k–15k, filed
9d ago* · *Technical: trend_following setup*), a suggested size + stop, and
Approve/Deny buttons. New tickers you don't own can appear here — that's the
discovery layer. Optionally `/brief SYM` one first, then Approve the ones you like.
- `/sources` shows what each signal source is contributing over time. `/reweight`
  turns that into a concrete suggestion — a bounded, ledger-driven reweighting of
  `discovery.weights` — and pushes it Approve/Deny, same as a rotation proposal;
  it never applies without a tap.

### 4. Place a manual trade (any time)
- `/brief AAPL` → paste into Claude.ai → if it looks good:
- `/buy AAPL 5 --stop 8` (or just type "buy 5 Apple, 8% stop") → confirm.

### 4. Weekly — strategy review
- `/review` → copy the **strategy brief** → paste into **Claude.ai** → ask which
  strategies to keep / disable / reweight.
- Act on the advice:
  - `/rotate disable breakout` — propose disabling a noise strategy → Approve.
  - `/rotate reweight trend_following 0.5` — propose a weight (0–1) → Approve.
  - `/rotate enable mean_reversion` — re-enable one.
- `/strategies` any time to see the current scoreboard (verdicts + live P&L).

Approved rotations write to `state/rotation.json`, which the orchestrator reads
each cycle — a disabled strategy simply stops generating entries. Guardrails
prevent disabling the last active strategy.

### 5. Periodically — refresh the verdict
Strategies are unproven until they beat a noise baseline. On the laptop:
```
python -m scripts.evaluate_strategies          # refresh data + re-score
python -m scripts.evaluate_strategies --offline # use cached bars only
```
This updates the scoreboard `/review` and `/strategies` read. Today's verdict:
`breakout` = **NOISE** and it underperforms simply holding SPY — do **not** go live.

---

## When it halts

The bot **defaults to halt** on trouble and does not self-resume — except two
transient classes (stale data, disconnect) that the self-healer auto-resumes
**only after verifying the fault cleared** (with cooldown + a daily cap), then
notifies you. Everything else waits for you.

If you get a **🛑 INCIDENT** brief (or run `run_self_heal`):
1. Copy the incident brief → paste into **Claude.ai** → ask for likely cause +
   whether it's safe to reset.
2. **reconcile-mismatch** → check your positions at the broker first (local state
   diverged). **kill-switch** → review the day's loss; don't rush back.
3. When you're satisfied: `/reset` (clears the halt). The next cycle trades again.

`/halt` stops everything instantly; `/flatten` liquidates all positions (confirm).

---

## Command reference (Telegram)

| Command | Action |
|---|---|
| `/status` · `/positions` | equity, positions, stops, halt state |
| `/ideas` | discover & rank fresh buy ideas → Approve/Deny each |
| `/sources` | what each discovery signal is contributing |
| `/reweight` | suggest a bounded reweighting of discovery sources → Approve/Deny |
| `/brief SYM` | symbol brief → paste into Claude.ai |
| `/review` | strategy brief → paste into Claude.ai |
| `/strategies` | scoreboard (verdicts + live P&L) |
| `/rotate <enable\|disable\|reweight> SYM [w]` | propose a rotation → Approve/Deny |
| `/buy SYM QTY [--stop N]` | gated manual buy (confirm) |
| `/close SYM` · `/flatten` | close one / all |
| `/pending` | re-show pending trade proposals |
| `/run` | run a decision cycle now |
| `/halt` · `/reset` | stop everything / clear a HALT |

Natural language works too (rule-based): "buy 15 Tesla, 8% stop", "show my
positions", "sell everything", "halt".

---

## The golden rules (unchanged)

- Everything routes through the **risk gate**; nothing bypasses `src/risk/`.
- **No live trading** until a strategy beats its noise baseline. Stay on paper.
- The bot **proposes**; **you** decide. reconcile-mismatch / kill-switch halts are
  **always manual** — never auto-resumed.
- Secrets stay in `.env`. No Anthropic key needed anywhere.
