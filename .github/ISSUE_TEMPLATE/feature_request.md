---
name: Feature request
about: Propose a new capability or a change to existing behavior
title: ""
labels: enhancement
---

**What problem does this solve**
Describe the gap — what can't you do today, or what's awkward about the
current setup.

**Proposed approach**
If you have one. No need to design it fully; a rough shape is fine.

**Which layer does this touch**
`src/data` | `src/strategy` | `src/risk` | `src/execution` | `src/core` |
`src/discovery` | `src/notify` | `src/agents` | `config/*.yaml` | other

If it would need `src/execution/` to place orders from outside that layer, or
would change what the risk gate does, please say so explicitly — those areas
get the most scrutiny (see [CLAUDE.md](../../CLAUDE.md)'s non-negotiable rules).

**Alternatives considered**
Any other way to solve the same problem you thought about and ruled out.
