"""
Agents -- cognitive plane.

The runtime "cognitive plane" that sits BESIDE the deterministic core. Agents
are short-lived: each spins up with a fresh, minimal context (just its profile's
system prompt + this task's payload), does one job, and exits -- so context can
never accumulate. Durable facts live in stores, never in a growing conversation.

Hard boundaries (mirror the deterministic core's rules):
  - Agents hold NO trading credentials and never call the broker.
  - Any state-changing capability is a *write tool* that runs the risk gate
    inside its handler (see src/agents/tools), reachable only by trusted agents.
  - Control flow is deterministic: a plain-Python Dispatcher maps a trigger to
    exactly one agent profile. No LLM decides routing.
"""
