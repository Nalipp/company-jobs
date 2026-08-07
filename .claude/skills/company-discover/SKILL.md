---
name: company-discover
description: Discover new companies through qualifying official jobs and write a replay-safe Daily Jobs JSON handoff. Use only when the user explicitly runs /company-discover.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Company Discover

Read `CLAUDE.md`, `docs/handoff-protocol.md`, and
`work-statements/company-discover.md` completely, then execute that work statement.

Treat `$ARGUMENTS` as optional run-specific narrowing. It may not silently relax
`config/search.json`. Follow the bounded research and checkpoint rules in the work
statement; do not spend the entire context on additional browsing. Always write the
required handoff, including for a no-op, and never edit `data/companies.json`.

After writing files, give a brief status summary, exact handoff paths, and literal
copy-pastable `.venv/bin/python scripts/apply_handoff.py` commands. Never use a
placeholder path in the final response.
