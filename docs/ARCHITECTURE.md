# Architecture

## Design goal

The layer that **decides** must never share a failure domain with the layer that
**acts**. A bug in research code must be structurally incapable of sending an order.

## Layers

```
+-----------------------------------------------------------+
|                  CORE / ORCHESTRATOR                       |
|     state machine . scheduler . heartbeat . clock          |
+-------------------+--------------------+-------------------+
                    |                    |
         +----------v------+    +--------v-----------+
         | DATA / RESEARCH |    | EXECUTION / TRADE  |
         |   (read-only)   |    | (order auth only)  |
         +----------+------+    +--------+-----------+
                    |                    |
                    +----> SIGNAL BUS <--+
                       (typed messages)
```

| Layer | Module | Responsibility | Orders? | Credentials |
|-------|--------|----------------|---------|-------------|
| Data/Research | `src/data`, `src/research` | Pull data, compute features, backtest | No | market-data (read) |
| Strategy/Signal | `src/strategy` | Regime routing + signals -> typed intents | No | none |
| Risk/Gatekeeper | `src/risk` | Validate every intent; veto power | No (vetoes) | none |
| Execution | `src/execution` | Approved intents -> Alpaca orders, reconcile | **Yes** | trading (write) |
| Core | `src/core` | Orchestration, state, scheduling, health | No | none |
| Common | `src/common` | Typed models, config, logging, secrets | No | gatekeeps secrets |

## Boundary rules (enforced by convention + review)

1. The Research and Strategy layers emit **signals/intents**, never orders. They do not
   import the broker client and do not hold trading credentials.
2. Every intent passes through the **Risk Gatekeeper**. It is pure logic, no network I/O,
   and is the only path to execution.
3. The Execution layer is **dumb on purpose**: it knows *how* to transact an already
   approved, already sized order — not *what* to trade.
4. **Credential isolation**: market-data keys and trading keys are different objects loaded
   in different modules. The research process cannot construct an order client.
5. Communication is via **typed messages** (see `src/common/models.py`). No layer reaches
   into another's internal state. In-process event bus to start; upgradeable to Redis/ZeroMQ.

## Message flow

```
MarketData ->  Features ->  Strategy(Regime->Signal) ->  Intent
   ->  SentimentGate(adjust confidence) ->  RiskGatekeeper(approve/veto/size)
   ->  OrderManager ->  AlpacaBroker ->  Fill ->  Reconciler ->  State
```

## Failure philosophy

On any uncertainty — disconnect, stale data, reconciliation mismatch, unhandled
exception — the default state is **HALT / do nothing**, never "guess and trade."
See [SAFEGUARDS.md](SAFEGUARDS.md).
