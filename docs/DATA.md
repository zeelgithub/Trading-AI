# Data Requirements

You cannot trust a bot to trade on data you have not validated. Build and prove these
pipelines in research/backtest before any live trading.

## A. Market data (foundation)

- **Historical daily bars** for backtesting — depth >= 400 days for the 200-day EMA warmup.
- **Real-time / EOD bars** for live signals.
- **Corporate actions** (splits, dividends): unadjusted data silently corrupts both
  backtests and live signals.
- **Feed choice:** IEX (free, partial volume) vs SIP (paid, full consolidated tape). Volume
  filters and breakout volume-spikes are only accurate on SIP.

## B. Data quality layer (non-negotiable before trusting signals)

- Gap detection (missing bars), outlier/spike filtering, duplicate removal.
- Timezone + market-calendar normalization (half-days, holidays).
- **Point-in-time correctness / no look-ahead:** features at time t use only data available
  at t. This is the cardinal rule of honest backtesting.

## C. Supplementary research data

- News headlines for the sentiment gate (non-executing).
- Note: LLM sentiment **cannot be honestly backtested** on history (the model knows the
  future). Validate it forward-only in paper.

## D. Storage

- SQLite for v1 (`data/trading_bot.db`), consider DuckDB/Postgres later.
- Beware **survivorship bias** if assembling symbol universes (include delisted tickers for
  honest backtests).

## E. Validation gate before live

1. Backtest on clean history with realistic costs (commission, slippage, spread).
2. Walk-forward / out-of-sample testing — never trust in-sample results.
3. Paper-trade live for a meaningful window; compare paper fills vs backtest expectations.
4. Only then: small real capital, all Tier-1 safeguards active.
