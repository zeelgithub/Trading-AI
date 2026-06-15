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
