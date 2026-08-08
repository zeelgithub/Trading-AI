# CLAUDE.md

Autonomous **paper** trading bot for US equities on Alpaca (daily-swing). Layered
so the decision path reaches the order API only through the risk gate.

## Non-negotiable rules

1. Only `src/execution/` places orders or holds trading creds (via
   `secrets.load_trading_credentials()`). Other layers emit typed messages
   (`src/common/models.py`) and use `load_market_data_credentials()`. Never log secrets.
2. Risk has veto, not authority: every order passes `src/risk/`. The sentiment/AI
   layer can shrink or block, never originate.
3. Default to halt: on disconnect, stale data (`data.max_bar_age_days`),
   reconcile mismatch, or unhandled exception, stop and do nothing. HALT
   persists across runs (`state/halt.json`); clear only with `run_paper
   --reset`. State files are written atomically; corrupt ones are quarantined.
   New entries are refused while the market is closed — everywhere, including
   phone approvals (no overnight-queued market orders).
4. No naked positions: every entry attaches a protective stop atomically
   (`submit_protected_entry`; bracket adds a take-profit OCO). Act on `filled_qty`.
5. Indicators/features must be causal -- row i uses only bars <= i (no look-ahead).
6. Live trading is a deliberate flag (`mode: live`); never flip it as a side
   effect. New entries/exits happen only when the market is open.
7. Message shapes live in `src/common/models.py` + `docs/CONTRACTS.md`, not here.

## Layout

`src/{common,data,strategy,risk,execution,core,notify,discovery}/` · `config/*.yaml`
· `docs/*.md` · `scripts/`. Orders live only in `src/execution/`. `src/discovery/`
is the idea-generation plane: it gathers buy candidates from free signal sources
(congress disclosures, the technical strategies, news, fundamentals), scores +
ranks them, and emits risk-gated Proposals for the phone to approve — a
SUGGESTION layer that, like the sentiment gate, surfaces/ranks but never
originates an order (rule 2). `src/notify/` is
the phone control surface (Telegram): it holds the bot token only (a
notification credential, never trading creds) and routes commands through
`src/core/trade_service` → risk gate → execution; it never calls the broker
directly. `congress_copy/` is a non-executing shadow copy-trader. Config drives
behaviour; secrets only in `.env`.

## Run

```
pip install -r requirements.txt
pytest                                              # offline, no creds
python -m scripts.run_paper [--propose|--execute|--reset]  # one cycle (shadow default)
python -m scripts.run_discovery [--dry-run]         # rank fresh buy ideas → phone
python -m scripts.manual_order SYM QTY --stop 10    # risk-gated manual buy
python -m scripts.run_telegram                      # always-on phone listener
```

Autonomous: a scheduled task runs `run_paper --execute` each weekday ~15:45 ET.
With `approval.require_approval: true` (default) that becomes propose-and-approve
— it pushes trades to the phone and places nothing until you tap Approve. A
second daily task runs `run_discovery` to push ranked buy ideas (congress +
technical by default; news + fundamentals toggle on in `config` →
`discovery.sources`). Run `scripts.run_telegram` always-on (Windows task "at
logon, restart on failure") for phone control: view/approve/deny, `/ideas`,
`/sources`, `/buy`, `/halt`, `/reset`, `/flatten`.

## Status

Implemented + tested (318 tests). Running autonomously on PAPER; strategies not
yet validated. Phone control + propose-and-approve via Telegram (`src/notify/`).
Autonomous discovery (`src/discovery/`) surfaces ranked buy ideas from
congress/technical/news/fundamentals → phone Approve/Deny. Details + open gaps in
`docs/ROADMAP.md`.
