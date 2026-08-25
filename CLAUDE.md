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
4. No naked positions: the entry alone is submitted first (`submit_market_entry`);
   `OrderManager.settle` attaches the protective stop the instant a fill is
   confirmed (`submit_stop`, or `submit_oco_exit` for a take-profit OCO) and a
   position is never marked OPEN until that succeeds -- NOT an atomic OTO/bracket
   (Alpaca doesn't honor GTC on OTO/bracket child legs; see docs/SAFEGUARDS.md
   "How a protected entry actually works"). Act on `filled_qty`.
5. Indicators/features must be causal -- row i uses only bars <= i (no look-ahead).
6. Live trading is a deliberate flag (`mode: live`); never flip it as a side
   effect. New entries/exits happen only when the market is open.
7. Message shapes live in `src/common/models.py` + `docs/CONTRACTS.md`, not here.

## Layout

`src/{common,data,strategy,risk,execution,core,notify,discovery}/` · `config/*.yaml`
· `docs/*.md` · `scripts/`. Orders live only in `src/execution/`. `src/discovery/`
is the idea-generation plane: it gathers buy candidates from free signal sources
(congress disclosures, technical strategies, news, fundamentals, a volatility
re-ranker, and a Reddit $TICKER-mention buzz source, off by default until
REDDIT_CLIENT_ID/SECRET are set) across a wide universe (watchlist + S&P
500/400/600 + data-derived small/micro-cap and volatile-stock screens, ~4,000
symbols; `discovery.min_price` is 0 -- NO penny-stock floor, a deliberate
2026-08-24 reversal, see docs/SAFEGUARDS.md), scores + ranks them, and emits
risk-gated Proposals
for the phone to approve — a SUGGESTION layer that, like the sentiment gate,
surfaces/ranks but never originates an order (rule 2). `src/notify/` is
the phone control surface (Telegram): it holds the bot token only (a
notification credential, never trading creds) and routes commands through
`src/core/trade_service` → risk gate → execution; it never calls the broker
directly. `congress_copy/` is a non-executing shadow copy-trader. Config drives
behaviour; secrets only in `.env`.

## Run

```
pip install -r requirements.txt
pytest                                              # offline, no creds
pytest -m integration tests/integration/            # opt-in: hits REAL Alpaca paper API, needs creds
python -m scripts.check_config [--config-dir path]  # validate config/*.yaml, no trading logic
python -m scripts.run_paper [--propose|--execute|--reset]  # one cycle (shadow default)
python -m scripts.run_discovery [--dry-run]         # rank fresh buy ideas → phone
python -m scripts.manual_order SYM QTY --stop 10    # risk-gated manual buy
python -m scripts.run_telegram                      # always-on phone listener
python -m scripts.equity_report [--days N]          # daily equity/P&L track record so far
```

Autonomous: a scheduled task runs `run_paper --execute` each weekday ~15:45 ET.
With `approval.require_approval: true` (default) that becomes propose-and-approve
— it pushes trades to the phone and places nothing until you tap Approve. A
second daily task runs `run_discovery` to push ranked buy ideas (technical by
default; congress + news + fundamentals toggle on in `config` →
`discovery.sources` — congress needs its own disclosure ingestion set up
first, see `congress_copy/README.md` "Scheduled ingestion"). Run
`scripts.run_telegram` always-on (restart on failure — Windows Task
Scheduler or a systemd service, see README "Running continuously") for phone
control: view/approve/deny, `/ideas`, `/sources`, `/buy`, `/halt`, `/reset`,
`/flatten`. Each deployment is independent (own `.env`, own `state/`, own
Telegram allowlist) — this is a single-operator design, not a shared service.

## Status

Implemented + tested (563 tests). Running autonomously on PAPER; trend_following
is statistically validated (backtest), breakout inconclusive, mean_reversion
disabled (net-negative) -- see docs/STRATEGIES.md. Paper EXECUTE started
2026-08-24 (see docs/ROADMAP.md Step 7); no live/paper track record yet, no
real capital. Phone control + propose-and-approve via Telegram (`src/notify/`).
Autonomous discovery (`src/discovery/`) surfaces ranked buy ideas from
congress/technical/news/fundamentals → phone Approve/Deny. Details + open gaps in
`docs/ROADMAP.md`.
