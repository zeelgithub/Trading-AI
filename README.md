# Live Trading Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Autonomous paper-trading bot for US equities on Alpaca (daily-swing). The decision
path only reaches the broker through a risk gate that can veto a trade but never
originate one.

> **Status:** running autonomously on paper (no live money). 334 tests pass offline.
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
  exposure caps, PDT tracking, fat-finger band, duplicate-order guard.
- **No naked positions** — every entry submits with a protective stop atomically
  (bracket/OCO for take-profit).
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
| `src/risk/` | Sizing, ratchet stop, circuit breakers, PDT tracker | No (vetoes) |
| `src/execution/` | Alpaca broker client, order manager, reconciler | **Yes** |
| `src/core/` | Orchestrator, trade service, state machine, proposals | No (routes to execution) |
| `src/discovery/` | Ranked buy-idea suggestions (congress/technical/news/fundamentals) | No (proposes only) |
| `src/notify/` | Phone control (Telegram): alerts, propose/approve | No (holds notify token only) |
| `src/common/` | Typed models, config loader, logging, secrets | No |
| `config/` | YAML config (non-secret) | n/a |
| `scripts/` | Entrypoints (paper run, discovery, manual order, telegram, backtest, self-heal, healthcheck) | n/a |

## Getting started

1. **Install**: Python 3.11+, `pip install -r requirements.txt`.
2. **Credentials**: copy `.env.example` to `.env`. At minimum you need a free
   [Alpaca paper account](https://alpaca.markets) for `ALPACA_API_KEY_ID` /
   `ALPACA_API_SECRET_KEY`. Telegram and news/sentiment keys are optional.
3. **Run the tests** (offline, no credentials needed): `pytest`.
4. **Try one cycle**: `python -m scripts.run_paper --propose` — reads the market,
   runs the strategies, and prints what it *would* do. Nothing reaches the broker
   without `--execute`, and even then `approval.require_approval: true` (the
   default) turns it into propose-and-approve instead of firing directly.
5. **Score the strategies**: `python -m scripts.evaluate_strategies` — backtests
   against a wide symbol universe and writes a verdict (noise / inconclusive /
   validated) per strategy to `state/scoreboard.json`.

## Running continuously

For hands-off operation, schedule these (Windows Task Scheduler, cron, etc.):

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

Natural language works too (rule-based): "buy 15 Tesla, 8% stop", "show my
positions", "halt". Every command still passes the risk gate; phone orders are
paper-only in v1.

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

## License

[MIT](LICENSE) — see the LICENSE file. This is research/educational software for a
paper-trading account; nothing here is investment advice.
