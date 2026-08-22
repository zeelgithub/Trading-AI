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
[ingestion: bring your own -- see "Scheduled ingestion" below]  ->  congress_copy/data/disclosures.json
                                                                              |
                                                                              v
   JSONFileProvider  ->  CopyTrader (filter: politician, stock, recent, unseen)
        ->  mirror BUY as Intent (+ protective stop)  ->  RiskManager.evaluate
        ->  log decision (SHADOW)  ->  state/seen.json (dedupe)
```

Ingestion and copy logic are **decoupled on purpose**: whatever writes fresh
rows into `disclosures.json` is entirely separate from the copy logic, which
stays clean and unit-tested regardless of where the data came from.

## Files

- `models.py` — `DisclosedTrade` (normalized PTR row)
- `providers.py` — `JSONFileProvider` (reads the ingested JSON)
- `copy_trader.py` — filtering + mirroring + risk-gating + dedupe (`SeenStore`)
- `config.yaml` — politician, filters, stop %
- `run.py` — shadow runner
- `data/disclosures.json` — written by the ingestion job (seeded with samples)
- `state/seen.json` — already-mirrored disclosure ids

## Config (`config.yaml`)

- `politician` — whose trades to mirror (default `Ro Khanna`). One-line change.
- `equities_only` — skip option disclosures (true).
- `max_disclosure_age_days` — ignore stale filings (60).
- `initial_stop_pct` — protective stop attached to each mirrored buy (we manage
  the exit with our ratchet rather than waiting for their delayed sell filing).

Always shadow — decide + log only, no `execute` toggle exists. Turning this
into a real (paper) order path would mean routing through a propose/approve
flow like `src/discovery/` does, not a config flag; that's a real feature,
not something to bolt on here casually.

## Run

```
python -m congress_copy.run          # shadow: decide + log, no orders
```

## Scheduled ingestion

**Not shipped.** Nothing in this repo automatically populates
`data/disclosures.json` — the file you get on a fresh clone is a small,
frozen set of sample rows for tests and demos, not a live feed. `python -m
congress_copy.run` will keep "mirroring" that same stale sample data forever
unless you add real ingestion yourself. Two ways to do that:

1. **Manual**: periodically append new rows to `data/disclosures.json`
   yourself, matching `DisclosedTrade`'s fields (`models.py`) — House/Senate
   disclosures (PTRs) are public at https://disclosures-clerk.house.gov and
   https://efdsearch.senate.gov, just not offered as a free structured API.
2. **Automated**: write a small scheduled job (cron / Task Scheduler) that
   fetches fresh disclosures from a data provider and writes them into this
   same JSON shape, then runs `python -m congress_copy.run` after. Congressional-
   trading data providers exist (e.g. QuiverQuant has a congressional-trades
   API, paid beyond a small free tier); a general scraping API (e.g. Firecrawl)
   against CapitolTrades' public pages is a free-tier-friendly alternative if
   you'd rather not depend on a dedicated financial-data vendor. Whichever you
   pick, keep it a single well-tested script that only writes JSON — don't
   let scraping/API logic leak into `copy_trader.py`.

Because there's no ingestion out of the box, `discovery.sources.congress`
(a separate consumer of this same `disclosures.json`, see
`src/discovery/sources/congress.py`) defaults to `false` in
`config/settings.yaml` — turn it on once you've wired real ingestion,
otherwise it just re-surfaces the same frozen sample rows as if they were
fresh ideas.

## Recommendation log

Researched 2026-06-13: Davidson too concentrated/inactive; Pelosi = options
(excluded); Khanna = most active equities → chosen as the v1 default for signal
volume. Re-point via `config.yaml` anytime.
