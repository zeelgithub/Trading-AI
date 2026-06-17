# Message Contracts

The typed messages that cross layer boundaries. **Source of truth is
[`src/common/models.py`](../src/common/models.py)** — this doc is the human
reference. Do not redefine these shapes anywhere else (not in CLAUDE.md, not in
config); import them.

## Trade signal (`Intent`)

What a strategy emits as a proposed trade. Validated by `Intent.from_dict()` and
serialized by `Intent.to_dict()`.

```json
{
  "symbol": "AAPL",
  "signal": "BUY",
  "confidence": 0.72,
  "strategy": "trend_following",
  "entry_price": 190.2,
  "stop_loss": 185.0,
  "take_profit": 205.0
}
```

| Field | Type | Notes |
|-------|------|-------|
| `symbol` | string | required |
| `signal` | enum | required. `BUY` (open long), `SHORT` (open short). `SELL`/`COVER` are exits; `HOLD` is no-op. Only `BUY`/`SHORT` open a position. |
| `confidence` | float | 0.0–1.0 |
| `strategy` | string | `trend_following` / `mean_reversion` / `breakout` |
| `entry_price` | float | strategy's intended entry; risk layer falls back to last market price if absent |
| `stop_loss` | float | must be below entry for longs, above for shorts |
| `take_profit` | float | must be above entry for longs, below for shorts |

`signal` maps to the internal `Side` (BUY→LONG, SHORT→SHORT). Validation rejects
out-of-range confidence and stops/targets on the wrong side of entry.

## Flow

```
Strategy -> Intent -> SentimentGate (adjusts confidence) -> RiskManager.evaluate()
   -> RiskDecision {APPROVE | RESIZE | VETO, approved_qty} -> OrderManager -> Broker
```

The risk layer owns final sizing; a strategy's `entry_price`/`stop_loss` are
inputs, and `approved_qty` on the `RiskDecision` is authoritative for execution.

## Proposal (`src/core/proposals.py`) — propose-and-approve

In propose mode a risk-approved entry is persisted (`state/proposals.json`) and
pushed to the phone instead of being opened. It wraps the `Intent` wire dict
above plus what's needed to rebuild the protective stop on approval. On approval
the `intent` is re-validated and **re-run through the risk gate** with fresh
account state before any order is placed.

```json
{
  "id": "NVDA-20260615-a1b2c3",
  "intent": { "symbol": "NVDA", "signal": "BUY", "...": "Intent wire dict" },
  "approved_qty": 18,
  "strategy": "trend_following",
  "ratchet_params": { "initial_stop_pct": 10.0, "lock_trigger_pct": 10.0 },
  "atr": null,
  "status": "pending",
  "created_ts": "2026-06-15T19:45:00+00:00",
  "expiry_ts": "2026-06-16T13:45:00+00:00"
}
```

| Field | Notes |
|-------|-------|
| `id` | `SYMBOL-YYYYMMDD-<rand>`; used in Approve/Deny callback data |
| `intent` | the `Intent` wire dict (with computed `entry_price`/`stop_loss`) |
| `approved_qty` | qty the risk gate approved at propose time (a ceiling, re-checked on approval) |
| `ratchet_params` / `atr` | per-strategy ratchet config to rebuild the stop |
| `status` | `pending` → `approved` / `denied` / `expired` |
