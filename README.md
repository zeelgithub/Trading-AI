# Live Trading Bot

Autonomous, modular trading bot for **US equities** on the **Alpaca** API. Built as a
layered system where the *research/decision* path is strictly isolated from the
*order execution* path so a bug in one cannot drain the account through the other.

> **Status: scaffolding / paper only.** No live-money trading. Business logic is not
> implemented yet — this repo currently defines structure, contracts, config, and
> safety boundaries.

## Core principles

1. **Layer isolation.** Data/Research and Strategy layers emit *typed signals only* and
   never hold trading credentials. The Execution layer is the *only* code allowed to
   place orders.
2. **Risk has veto, not authority.** A deterministic Risk Gatekeeper sits between every
   intent and every order. The AI/sentiment layer can *propose and gate*, never *trigger*.
3. **Default to safe.** On any uncertainty — disconnect, stale data, reconciliation
   mismatch, unhandled exception — the bot halts and does nothing rather than guessing.
4. **Paper first.** Live trading is an explicit, deliberate config flag flipped only after
   safeguards are proven.

## System layers

```
Data/Research  ->  Strategy/Signal  ->  Risk Gatekeeper  ->  Execution  ->  Alpaca
 (read-only)        (signals only)       (veto, no I/O)      (orders only)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Layout

| Path | Role | Can place orders? |
|------|------|-------------------|
| `src/data/` | Market data, news, indicators, feature engineering | No |
| `src/research/` | Backtesting, metrics (offline) | No |
| `src/strategy/` | Regime filter + 3 strategies + sentiment gate | No |
| `src/risk/` | Risk manager, sizing, ratchet stop, circuit breakers, PDT | No (vetoes) |
| `src/execution/` | Alpaca broker client, order manager, reconciler | **Yes** |
| `src/core/` | Orchestrator, trade service, state machine, proposals | No (routes to execution) |
| `src/notify/` | Phone control surface (Telegram): alerts, propose/approve | No (holds notify token only) |
| `src/common/` | Typed models, config loader, logging, secrets | No |
| `config/` | YAML config (non-secret) | n/a |
| `docs/` | Design docs | n/a |
| `scripts/` | Entrypoints (paper run, manual order, telegram listener, backtest) | n/a |

## Phone control (Telegram)

Run the bot from your phone — view positions/stops, get alerts, and **approve or
deny** each trade before it reaches the broker. See
[docs/SAFEGUARDS.md](docs/SAFEGUARDS.md#phone-control-telegram--auth--boundaries).

1. Telegram → message **@BotFather** → `/newbot` → copy the token into
   `TELEGRAM_BOT_TOKEN` in `.env`.
2. Start the listener: `python -m scripts.run_telegram`. Send it `/start`; it
   replies with your chat id. Put that in `TELEGRAM_ALLOWED_CHAT_IDS` and
   restart (an empty allowlist denies everyone).
3. Daily run (`run_paper --execute`, default `approval.require_approval: true`)
   becomes **propose-and-approve**: trades are pushed to your phone with
   Approve / Deny and placed only on approval. Commands: `/status`, `/positions`,
   `/pending`, `/buy SYM QTY [--stop N]`, `/close SYM`, `/flatten`, `/halt`,
   `/reset`, `/run`. Every order still passes the risk gate; phone is paper-only.

Keep the listener always-on (Windows scheduled task "at logon, restart on failure").

## Configuration

- Secrets live **only** in `.env` (gitignored). Copy `.env.example` -> `.env`.
- Strategy/risk parameters live in `config/*.yaml`.
- Phone/alerts: `alerts` + `approval` in `config/settings.yaml`; token + chat-id
  allowlist in `.env`.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layer separation and message flow
- [docs/STRATEGIES.md](docs/STRATEGIES.md) — the 3-strategy regime system
- [docs/SAFEGUARDS.md](docs/SAFEGUARDS.md) — fail-safes and kill switches
- [docs/DATA.md](docs/DATA.md) — data pipeline requirements
- [docs/ROADMAP.md](docs/ROADMAP.md) — build order
