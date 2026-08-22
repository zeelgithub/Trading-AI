# Contributing

This is a personal research project (paper-trading only, no strategy has a live
track record) that's open for others to self-host, read, and improve.
Contributions are welcome, with a few things worth knowing going in.

## Before you start

- Read [CLAUDE.md](CLAUDE.md) first — it's the actual constitution of this
  codebase: the non-negotiable rules (risk has veto only, no naked positions,
  default-to-halt, causal indicators, live trading is a deliberate flag) apply
  to every PR, not just the original author's.
- This is a single-operator design (see README "Getting started") — each
  deployment is independent, its own `.env`/`state/`/Telegram allowlist.
  Changes that assume a shared server or a central database are out of scope.
- No strategy here has a live trading track record (see docs/ROADMAP.md).
  Don't treat backtest numbers as a performance claim, and don't add anything
  that makes that confusion more likely.

## Setup

```
pip install -r requirements.txt
pytest                                     # offline, no creds -- must pass before any PR
python -m scripts.check_config              # validate config/*.yaml after touching it
ruff check .                                # informational in CI today; please don't add new findings
mypy src --ignore-missing-imports           # same
```

## Making a change

1. **Tests first.** Every layer (`src/risk`, `src/execution`, `src/strategy`,
   ...) has a matching `tests/unit/test_*.py` that runs offline against a
   `FakeBroker` — no real Alpaca account needed. A PR touching
   `src/execution/` should also touch `test_broker_alpaca.py` /
   `test_order_manager.py` if it changes what gets sent to the broker.
2. **Respect the layering.** Only `src/execution/` places orders or holds
   trading credentials (`src/common/secrets.py`'s `load_trading_credentials()`).
   If a change needs the broker from anywhere else, that's usually a sign it
   belongs in `src/execution/` instead, exposed through a narrower read-only
   interface (see `AlpacaAccountReader`, already used by `scan_signals.py`,
   `congress_copy/`, `run_discovery.py`).
3. **Root-cause fixes, not workarounds.** `docs/ROADMAP.md` has a running
   history of bugs traced back to their actual cause (a missing null-check,
   a broker API limitation, a stale assumption) rather than papered over.
   Same bar applies to new PRs.
4. **Config over hardcoding for anything risk-relevant** (thresholds,
   position/exposure limits, strategy parameters) — with schema validation in
   `src/common/config_schema.py`. Not everything needs a config knob; a purely
   internal constant doesn't need one just to have one.
5. Update the relevant `docs/*.md` if the change is significant enough that a
   future reader — including a future AI coding session working on this repo —
   would need to know it happened and why.

## Pull requests

- Keep them scoped to one change.
- `pytest` must pass. `pytest -m integration tests/integration/` (hits
  Alpaca's real paper API) is opt-in and not required, but mention in the PR
  description if you ran it.
- Describe *why*, not just *what* — the diff already shows what changed.

## Reporting bugs / proposing features

Open a GitHub issue using the templates. If it's a safety-relevant bug
(something that could lead to a naked position, a bypassed halt, or a leaked
credential), see [SECURITY.md](SECURITY.md) instead.
