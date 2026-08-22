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
| Discovery | `src/discovery` | Rank buy candidates (congress/technical/news/fundamentals) into Proposals | No (proposes only) | market-data (read) |
| Risk/Gatekeeper | `src/risk` | Validate every intent; veto power | No (vetoes) | none |
| Execution | `src/execution` | Approved intents -> Alpaca orders, reconcile | **Yes** | trading (write) |
| Core | `src/core` | Orchestration, state, scheduling, health, rotation | No | none |
| Notify | `src/notify` | Phone control (Telegram): alerts, propose/approve | No | notify token only |
| Cognitive (optional) | `src/agents`, `mcp_servers` | NL parsing, self-heal triage, strategy analysis -- reads + propose-only writes | No | Anthropic (optional) |
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

## Cognitive plane (optional, keyless by default)

The deterministic core above is the always-on safety spine; `src/agents/` is a
short-lived, stateless layer that reads state and can *propose* -- never
execute -- through the exact same risk gate and approval flow as everything
else. It only activates when `ANTHROPIC_API_KEY` is set (`.env`); every
capability it adds has a deterministic, keyless fallback, so the bot is fully
functional without it.

```
Telegram command --(no key: regex parser)---------------------------> intent
                 \-(key set: nl_router agent, src/agents/nl.py)------/

HALT (stale_data/disconnect) --(no key: incident brief only)--------> phone
                              \-(key set: anomaly_triage agent)-----/
                                 src/agents/triage.py, best-effort,
                                 never clears a halt itself

/review (phone) --> strategy_analyst agent (unwired by choice; brief-
                     based /review already works) --> rotation Proposal
                     --(any path)--> Approve/Deny --> state/rotation.json
```

Pieces, each independently optional:
- `src/agents/{model,runtime,dispatch,catalog,profiles}.py` -- a generic,
  offline-testable tool-use loop and dispatcher. A "profile" (`profiles.py`)
  pairs a model tier with a read/write tool set for one job (`nl_router`,
  `anomaly_triage`, `strategy_analyst`).
- `src/agents/tools/{reads,writes}.py` -- read tools are pure lookups (bars,
  indicators, positions, halt state, scoreboard); the one write tool
  (`propose_rotation`) can only create a Proposal, gated the same way a phone
  `/rotate` is (guardrails enforced server-side at both propose and approve).
- `mcp_servers/{market_data,portfolio_state}` -- the same read functions
  (`src/data/queries.py`, `src/core/portfolio_view.py`) exposed twice: as
  in-process tools for the agents above, and as MCP servers for Claude Code /
  Claude Desktop to query this bot's state directly during development.
  `mcp_servers/trading_research` is a separate, standalone MCP server (news/
  SEC-filings/congressional-trades lookups) used by the discovery plane's
  research, not part of the agent-dispatch loop above.

None of this can originate a trade any more than the sentiment gate can (see
rule 2) -- it can only rank, summarize, or propose, always behind the same
gate and the same human approval as a manual `/buy`.

## Failure philosophy

On any uncertainty — disconnect, stale data, reconciliation mismatch, unhandled
exception — the default state is **HALT / do nothing**, never "guess and trade."
See [SAFEGUARDS.md](SAFEGUARDS.md).
