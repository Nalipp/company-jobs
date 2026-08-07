---
name: company-job-fetch
description: Poll saved company job sources and write a replay-safe JSON handoff containing only qualifying unseen jobs. Use only when the user explicitly runs /company-job-fetch.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Company Job Fetch

Read `CLAUDE.md`, `docs/handoff-protocol.md`, and
`work-statements/company-job-fetch.md` completely, then execute that work statement.

Treat `$ARGUMENTS` as optional diagnostic narrowing. Always write the required
handoff, including when no new job exists, and never edit
`data/companies.json`.
