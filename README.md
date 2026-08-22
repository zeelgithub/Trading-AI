# Live Trading Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Autonomous paper-trading bot for US equities on Alpaca (daily-swing). The decision
path only reaches the broker through a risk gate that can veto a trade but never
originate one.

> **Status:** running autonomously on paper (no live money). 406 tests pass offline.
> Strategies are backtested and scored for statistical significance — one is
> validated, none has a live track record yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

> [!WARNING]
> **Not financial advice, and not proven.** This trades a real (paper) brokerage
> account autonomously. The strategies are unvalidated research, past backtest
> results don't predict future returns, and switching `mode: live` risks real
> money. Use at your own risk; the author takes no responsibility for losses.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Layout](#layout)
- [Getting started](#getting-started)
- [Running continuously](#running-continuously)
- [Phone control (Telegram)](#phone-control-telegram)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [License](#license)

## Features

- **Regime-routed strategies** — trend following, mean reversion, and breakout,
  each active only in the market regime it's suited for (ADX/EMA-based filter).
- **Risk gate with real fail-safes** — daily-loss kill switch, max position/gross
  exposure caps, fat-finger band, duplicate-order guard.
- **No naked positions** — a position is never marked open until its protective
  stop (GTC, standalone; OCO with a take-profit) is confirmed resting at the
  broker.
- **Default-to-halt safety** — disconnects, stale data, or a reconciliation
  mismatch stop the bot rather than let it guess; halts persist until you
  manually clear them.
- **Statistical validation, not vibes** — `scripts/evaluate_strategies.py` backtests
  each strategy and scores it (bootstrap p-value, Probabilistic Sharpe Ratio,
  temporal consistency) as noise / inconclusive / validated before it's trusted.
- **Phone control via Telegram** — view positions, get alerts, and approve or deny
  every trade before it reaches the broker.
- **Idea discovery** — ranks buy candidates from congressional disclosures,
  technical setups, news, and fundamentals into approve/deny suggestions.
- **Self-healing** — a deterministic, whitelisted auto-resume for stale-data/
  disconnect halts only; a kill-switch or reconciliation halt always needs you.

## How it works

```
Data/Research  ->  Strategy/Signal  ->  Risk Gatekeeper  ->  Execution  ->  Alpaca
 (read-only)        (signals only)       (veto, no I/O)      (orders only)
```

- Only `src/execution/` places orders or holds trading credentials.
- On disconnect, stale data, a reconciliation mismatch, or an unhandled exception, the
  bot halts and does nothing until you clear it manually.
- Live trading is a separate `mode: live` flag — the repo defaults to paper and stays
  there unless you change it yourself.

## Layout

| Path | Role | Can place orders? |
|------|------|-------------------|
| `src/data/` | Market data, news, indicators, feature engineering | No |
| `src/research/` | Backtesting, significance testing, scoreboard (offline) | No |
| `src/strategy/` | Regime filter + 3 strategies + sentiment gate | No |
| `src/risk/` | Sizing, ratchet stop, circuit breakers | No (vetoes) |
| `src/execution/` | Alpaca broker client, order manager, reconciler | **Yes** |
| `src/core/` | Orchestrator, trade service, state machine, proposals | No (routes to execution) |
| `src/discovery/` | Ranked buy-idea suggestions (congress/technical/news/fundamentals) | No (proposes only) |
| `src/notify/` | Phone control (Telegram): alerts, propose/approve | No (holds notify token only) |
| `src/agents/`, `mcp_servers/` | **Optional** cognitive plane: NL command parsing, self-heal triage, strategy analysis — reads state and can only *propose*, through the same risk gate. Keyless by default (deterministic fallback for every capability); activates only if `ANTHROPIC_API_KEY` is set. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#cognitive-plane-optional-keyless-by-default) | No |
| `src/common/` | Typed models, config loader, logging, secrets | No |
| `config/` | YAML config (non-secret) | n/a |
| `scripts/` | Entrypoints (paper run, discovery, manual order, telegram, backtest, self-heal, healthcheck) | n/a |

## Getting started

1. **Install**: Python 3.11+, `pip install -r requirements.txt`. For a
   reproducible install on the machine that actually runs the scheduled
   tasks, use `pip install -r requirements.lock.txt` instead (exact versions
   last verified together; regenerate it after intentionally upgrading a
   dependency).
2. **Credentials**: copy `.env.example` to `.env`. At minimum you need a free
   [Alpaca](https://alpaca.markets) account: sign up, then in the dashboard
   switch to **Paper Trading** (top-left account switcher) and open
   **API Keys** in the left sidebar — "Generate New Key" gives you
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`. Make sure you're generating
   a *paper* key (the dashboard clearly separates paper vs live keys) and that
   `ALPACA_TRADING_BASE_URL` in `.env` stays pointed at
   `paper-api.alpaca.markets` — the default in `.env.example` is already
   correct, just don't change it to the live URL by mistake. Telegram
   (`TELEGRAM_BOT_TOKEN`) and Anthropic (`ANTHROPIC_API_KEY`) are both
   optional and gate their own features — see the comments in `.env.example`
   for what each unlocks. Each clone of this repo is a fully independent
   deployment — its own `.env`, its own `state/` (positions, halt,
   scoreboard), its own Telegram allowlist. There's no shared server or
   account between deployments; if you're self-hosting your own copy, nothing
   here needs coordinating with anyone else's instance. Review
   `config/symbols.yaml` before your first real run — it ships with a small
   starter watchlist (AAPL, MSFT, SPY), not a recommendation. If you're
   starting from a small account, see the sizing note at the top of
   `config/risk_limits.yaml`'s `position:` block — whole-share-only sizing can
   silently veto entries on a very small account more often than the
   percentages alone would suggest.
3. **Run the tests** (offline, no credentials needed): `pytest`. A separate,
   opt-in integration tier exercises Alpaca's real paper API — it never runs
   by default; see [tests/integration/test_broker_alpaca_paper.py](tests/integration/test_broker_alpaca_paper.py)
   for what it covers and its safety notes before running it against an
   account your bot is actively managing. After changing any `config/*.yaml`,
   `python -m scripts.check_config` validates it standalone (same check every
   entrypoint runs at boot) with a clean pass/fail instead of a traceback.
4. **Try one cycle**: `python -m scripts.run_paper --propose` — reads the market,
   runs the strategies, and prints what it *would* do. Nothing reaches the broker
   without `--execute`, and even then `approval.require_approval: true` (the
   default) turns it into propose-and-approve instead of firing directly.
5. **Score the strategies**: `python -m scripts.evaluate_strategies` — backtests
   against a wide symbol universe and writes a verdict (noise / inconclusive /
   validated) per strategy to `state/scoreboard.json`.

## Running continuously

For hands-off operation, schedule these:

| Job | Schedule | What it does |
|---|---|---|
| `python -m scripts.run_telegram` | always-on (restart on failure) | phone listener |
| `python -m scripts.run_paper --execute` | weekdays, ~15:45 ET | decision cycle → proposes trades (or places them if `approval.require_approval: false`) |
| `python -m scripts.run_discovery` | weekdays, ~15:45 ET | ranks fresh buy ideas → pushes suggestions to your phone |
| `python -m scripts.run_self_heal` | every few minutes (optional) | auto-resumes stale-data/disconnect halts only; escalates everything else |

Nothing here places an order without passing the risk gate first, and with
`approval.require_approval: true` (the default) nothing reaches the broker
without a phone tap either. Full day-to-day flow (morning check, approving
proposals, weekly strategy review, handling a halt):
[docs/DAILY_WORKFLOW.md](docs/DAILY_WORKFLOW.md).

**Windows** — Task Scheduler. `scripts/install_listener_task.ps1` registers the
always-on listener (starts at logon, restarts on failure); the two ~15:45 ET
jobs and the optional self-heal tick are separate Basic Tasks pointed at
`.venv\Scripts\python.exe -m scripts.run_paper --execute` (etc.), Trigger =
"Weekly, weekdays, 15:45".

**Linux / macOS** — cron for the two scheduled jobs, a small `systemd` user
service for the always-on listener (cron isn't a good fit for "stay running
and restart on crash"). All times are ET; convert to the server's local
timezone or set `TZ=America/New_York` on the cron lines themselves:

```cron
# crontab -e  (weekdays only: Mon-Fri = 1-5)
45 15 * * 1-5 cd /path/to/Claude-livetradingbot && .venv/bin/python -m scripts.run_paper --execute >> logs/cron.log 2>&1
45 15 * * 1-5 cd /path/to/Claude-livetradingbot && .venv/bin/python -m scripts.run_discovery >> logs/cron.log 2>&1
*/5 * * * *   cd /path/to/Claude-livetradingbot && .venv/bin/python -m scripts.run_self_heal >> logs/cron.log 2>&1
```

```ini
# ~/.config/systemd/user/claude-trading-telegram.service
[Unit]
Description=Always-on Telegram listener for the trading bot

[Service]
WorkingDirectory=/path/to/Claude-livetradingbot
ExecStart=/path/to/Claude-livetradingbot/.venv/bin/python -m scripts.run_telegram
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```
Enable with `systemctl --user enable --now claude-trading-telegram.service`
(add `loginctl enable-linger $USER` so it survives logout). Either platform
is a normal Python process talking only to Alpaca and Telegram's public
APIs — nothing here is Windows-specific at the code level, only the two
included installer scripts (`scripts/install_listener_task.ps1`,
`scripts/run_telegram.bat`) are.

## Phone control (Telegram)

View positions, get alerts, and approve or deny each trade before it reaches the
broker — auth model and boundaries in
[docs/SAFEGUARDS.md](docs/SAFEGUARDS.md#phone-control-telegram--auth--boundaries).

1. In Telegram, message **@BotFather** → `/newbot` → put the token in
   `TELEGRAM_BOT_TOKEN`.
2. Run `python -m scripts.run_telegram`, then send `/start` — it replies with your
   chat id. Put that in `TELEGRAM_ALLOWED_CHAT_IDS` and restart (an empty allowlist
   denies everyone).

| Command | Action |
|---|---|
| `/status` · `/positions` | equity, positions, stops, halt state |
| `/ideas` · `/sources` | fresh ranked buy ideas · what each signal source contributed |
| `/brief SYM` · `/review` | symbol / strategy brief to paste into an LLM for a second opinion |
| `/strategies` | scoreboard (verdicts + live P&L) |
| `/rotate <enable\|disable\|reweight> SYM [w]` | propose a strategy rotation → Approve/Deny |
| `/buy SYM QTY [--stop N]` | gated manual buy (confirm) |
| `/close SYM` · `/flatten` | close one position / all |
| `/pending` · `/run` | re-show pending proposals · run a decision cycle now |
| `/halt` · `/reset` | stop everything / clear a HALT |

Natural language works too: "buy 15 Tesla, 8% stop", "show my positions",
"halt". A local regex parser handles this by default; set `ANTHROPIC_API_KEY`
for a smarter LLM-backed parser instead (same fallback if the key is absent or
a call fails). Every command still passes the risk gate; phone orders are
paper-only in v1.

**Voice messages** (optional): hold the mic and speak instead of typing.
Transcribed locally, no API key needed — but it does need two things this
repo doesn't install by default: `pip install openai-whisper` (see the
commented line in `requirements.txt`) and `ffmpeg` on your `PATH`. Without
both, voice messages are just ignored; typed/NL commands work regardless.

## Configuration

- Secrets live only in `.env` (gitignored) — never in `config/*.yaml`.
- Strategy/risk parameters: `config/*.yaml`.
- Alerts and approval behavior: `alerts` + `approval` in `config/settings.yaml`.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layer separation and message flow
- [docs/STRATEGIES.md](docs/STRATEGIES.md) — the 3-strategy regime system
- [docs/SAFEGUARDS.md](docs/SAFEGUARDS.md) — fail-safes and kill switches
- [docs/DATA.md](docs/DATA.md) — data pipeline
- [docs/CONTRACTS.md](docs/CONTRACTS.md) — message shapes between layers
- [docs/DAILY_WORKFLOW.md](docs/DAILY_WORKFLOW.md) — running it day to day
- [docs/ROADMAP.md](docs/ROADMAP.md) — build order and current status
- [congress_copy/README.md](congress_copy/README.md) — shadow congressional copy-trading module
- [CONTRIBUTING.md](CONTRIBUTING.md) — contributing to this project
- [SECURITY.md](SECURITY.md) — reporting a vulnerability

## License

[MIT](LICENSE) — see the LICENSE file. This is research/educational software for a
paper-trading account; nothing here is investment advice.
