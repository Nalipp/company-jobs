---
name: validate-company-address
description: Research the next pending company addresses and write a replay-safe Daily Jobs JSON handoff. Use only when the user explicitly runs /validate-company-address.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Validate Company Address

Read `CLAUDE.md`, `docs/handoff-protocol.md`, and
`work-statements/validate-company-address.md` completely, then execute that work
statement.

Treat `$ARGUMENTS` as optional `--limit` or explicit retry instructions. Always
write the required handoff, including when no company is eligible, and never edit
`data/companies.json`.
