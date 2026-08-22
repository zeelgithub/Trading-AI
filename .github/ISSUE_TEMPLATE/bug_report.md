---
name: Bug report
about: Something behaves differently than the code/docs say it should
title: ""
labels: bug
---

**What happened**
A clear description of the incorrect behavior.

**Expected behavior**
What you expected instead, ideally with a pointer to the doc/comment that led
you to expect it.

**Steps to reproduce**
- Command run (e.g. `python -m scripts.run_paper --propose`)
- Relevant `config/*.yaml` values (redact nothing sensitive is ever in config,
  but double-check anyway)
- `pytest` output if a test fails, or the traceback if a script crashes

**Environment**
- OS:
- Python version:
- `paper` or `live` mode (should always be `paper` unless you deliberately changed it):

**Safety-relevant?**
If this could lead to a naked position, a bypassed halt, or a leaked
credential, please use [SECURITY.md](../../SECURITY.md) instead of a public
issue.
