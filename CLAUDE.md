# CLAUDE.md

Autonomous **paper** trading bot for US equities on Alpaca (daily-swing). Layered
so the decision path reaches the order API only through the risk gate.

## Non-negotiable rules

1. Only `src/execution/` places orders or holds trading creds (via
   `secrets.load_trading_credentials()`). Other layers emit typed messages
   (`src/common/models.py`) and use `load_market_data_credentials()`. Never log secrets.
2. Risk has veto, not authority: every order passes `src/risk/`. The sentiment/AI
   layer can shrink or block, never originate.
3. Default to halt: on disconnect, stale data, reconcile mismatch, or unhandled
   exception, stop and do nothing. HALT persists across runs (`state/halt.json`);
   clear only with `run_paper --reset`.
4. No naked positions: every entry attaches a protective stop atomically
   (`submit_protected_entry`; bracket adds a take-profit OCO). Act on `filled_qty`.
5. Indicators/features must be causal -- row i uses only bars <= i (no look-ahead).
6. Live trading is a deliberate flag (`mode: live`); never flip it as a side
   effect. New entries/exits happen only when the market is open.
7. Message shapes live in `src/common/models.py` + `docs/CONTRACTS.md`, not here.

## Layout

`src/{common,data,strategy,risk,execution,core}/` · `config/*.yaml` · `docs/*.md`
· `scripts/`. Orders live only in `src/execution/`. `congress_copy/` is a
non-executing shadow copy-trader. Config drives behaviour; secrets only in `.env`.

## Run

```
pip install -r requirements.txt
pytest                                            # offline, no creds
python -m scripts.run_paper [--execute|--reset]   # one cycle (shadow default)
python -m scripts.manual_order SYM QTY --stop 10  # risk-gated manual buy
```

Autonomous: a scheduled task runs `run_paper --execute` each weekday ~15:45 ET.

## Status

Implemented + tested (86 tests). Running autonomously on PAPER; strategies not
yet validated. Details + open gaps in `docs/ROADMAP.md`.
