# Congress Copy-Trading (shadow)

Mirrors a chosen member of Congress's disclosed **stock** trades onto the Alpaca
**paper** account, routed through the main bot's risk gate. Currently **shadow**:
it decides and logs, places no orders.

## The two realities this is built around

1. **Disclosure lag.** Members have up to ~30–45 days to file (STOCK Act). We are
   copying trades that already happened weeks ago, at different prices. This is a
   lag strategy, not real-time mirroring. Each logged action shows `lag_days`.
2. **Equities only.** The bot has no options support, so option disclosures are
   skipped. The famous performers (e.g. Pelosi) trade options — revisit once an
   options module exists.

## Data flow

```
[ingestion job: Chrome reads CapitolTrades]  ->  congress_copy/data/disclosures.json
        (or official feed fallback)                       |
                                                          v
   JSONFileProvider  ->  CopyTrader (filter: politician, stock, recent, unseen)
        ->  mirror BUY as Intent (+ protective stop)  ->  RiskManager.evaluate
        ->  log decision (SHADOW)  ->  state/seen.json (dedupe)
```

Ingestion and copy logic are **decoupled on purpose**: the fragile, site-specific
scraping lives in the scheduled Chrome job (which writes the JSON); the copy
logic stays clean and unit-tested.

## Files

- `models.py` — `DisclosedTrade` (normalized PTR row)
- `providers.py` — `JSONFileProvider` (reads the ingested JSON)
- `copy_trader.py` — filtering + mirroring + risk-gating + dedupe (`SeenStore`)
- `config.yaml` — politician, filters, stop %, shadow/execute
- `run.py` — shadow runner
- `data/disclosures.json` — written by the ingestion job (seeded with samples)
- `state/seen.json` — already-mirrored disclosure ids

## Config (`config.yaml`)

- `politician` — whose trades to mirror (default `Ro Khanna`). One-line change.
- `equities_only` — skip option disclosures (true).
- `max_disclosure_age_days` — ignore stale filings (60).
- `initial_stop_pct` — protective stop attached to each mirrored buy (we manage
  the exit with our ratchet rather than waiting for their delayed sell filing).
- `execute` — **false** (shadow). When true, approved buys route to the order
  manager (paper).

## Run

```
python -m congress_copy.run          # shadow: decide + log, no orders
```

## Scheduled ingestion

A scheduled task uses the Chrome extension to read the politician's CapitolTrades
page, normalize new rows into `data/disclosures.json`, then runs the shadow
copy. See the "Scheduled" sidebar (task `congress-copy-watch`).

## Recommendation log

Researched 2026-06-13: Davidson too concentrated/inactive; Pelosi = options
(excluded); Khanna = most active equities → chosen as the v1 default for signal
volume. Re-point via `config.yaml` anytime.
