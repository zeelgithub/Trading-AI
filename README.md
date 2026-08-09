# Live Trading Bot

Autonomous paper-trading bot for US equities on Alpaca (daily-swing). The decision
path only reaches the broker through a risk gate that can veto a trade but never
originate one.

> **Status:** running autonomously on paper (no live money). 334 tests pass offline.
> Strategies are backtested and scored for statistical significance — one is
> validated, none has a live track record yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

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
| `scripts/` | Entrypoints (paper run, discovery, manual order, telegram, backtest) | n/a |

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

## Phone control (Telegram)

Optional. View positions, get alerts, and approve or deny each trade before it
reaches the broker — auth model and boundaries in
[docs/SAFEGUARDS.md](docs/SAFEGUARDS.md#phone-control-telegram--auth--boundaries).

1. In Telegram, message **@BotFather** → `/newbot` → put the token in
   `TELEGRAM_BOT_TOKEN`.
2. Run `python -m scripts.run_telegram`, then send `/start` — it replies with your
   chat id. Put that in `TELEGRAM_ALLOWED_CHAT_IDS` and restart (an empty allowlist
   denies everyone).
3. Commands: `/status`, `/positions`, `/pending`, `/buy SYM QTY [--stop N]`,
   `/close SYM`, `/flatten`, `/halt`, `/reset`, `/run`, `/ideas`, `/review`,
   `/rotate`. Every order still passes the risk gate; phone orders are paper-only.

Keep the listener always-on (Windows scheduled task "at logon, restart on failure").

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
