## What & why

<!-- What changed, and why -- the diff already shows what, focus on why. -->

## Layer(s) touched

<!-- src/data, src/strategy, src/risk, src/execution, src/core, src/discovery,
     src/notify, src/agents, config/*.yaml, docs, other -->

## Checklist

- [ ] `pytest` passes (offline, no credentials)
- [ ] `python -m scripts.check_config` passes if `config/*.yaml` changed
- [ ] New/changed behavior has a matching test in `tests/unit/`
- [ ] Relevant `docs/*.md` updated if this is a significant change
- [ ] If this touches `src/execution/` or the risk gate: explained below why
      it's still safe (no naked positions, risk still has veto only, etc.)

## Notes for the reviewer

<!-- Anything a reviewer should specifically look at -- edge cases you're
     unsure about, a design tradeoff you made, etc. -->
