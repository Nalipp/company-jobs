# V5 Daily Jobs

Read `V5_DATA_MODEL.md` and `README.md` before changing database or migration
behavior.

## Current phase boundary

Phase 1's SQLite company/source foundation passed its acceptance gate. The user
approved unfiltered job collection behind the single Codex command
`$collect-jobs --run luna`. Do not add V4 job imports, profile filtering, MCP
services, JSON exports, or UI code without a further explicit phase decision.

## Source and mutation rules

- `/Users/natelipp/ai-code/_v4_daily-jobs` is read-only migration input.
- Never edit V4 canonical JSON, queues, results, locks, or research archives.
- V5 reads V4 `data/companies.json` under its existing shared lock.
- SQLite writes are transactional and must keep foreign keys enabled.
- Re-importing the same V4 source digest must be a database no-op.
- Current canonical V4 facts determine eligibility. Historical research is
  evidence only and must never create an eligible location by itself.
- V4 job snapshots, job results, job queues, and profile verdicts are excluded.
- Reuse `career_sources.instructions`. Tested V5 browser recipes are separate
  overlays and must not be overwritten by a later V4 source import.
- A scan must claim one exact source. Recheck the claim, source revision, and
  `collectible_sources` membership in the transaction that writes jobs.
- Collect every public posting from an eligible company's source, regardless
  of job location or user profile. Keep the full source capture.
- Only a proven complete scan may inactivate jobs it did not observe. Partial
  and blocked scans must preserve them.

## Eligibility invariant

A company is eligible for job collection only when:

1. its current company status is `verified`;
2. it has a current location whose validation status is `verified`;
3. that location is a physical workplace inside San Francisco; and
4. latitude and longitude are present.

Use the `eligible_companies` and `collectible_sources` views. Do not duplicate
this predicate in callers.

## Verification

Run:

```bash
cd /Users/natelipp/ai-code/_v5_daily-jobs
.venv/bin/python -m pytest -q
.venv/bin/python scripts/v5-data verify --database data/v5.sqlite --v4-root ../_v4_daily-jobs
```

Before the environment exists, the standard-library-only operational CLI may be
run with `python3`. Tests require the optional `test` dependencies from
`pyproject.toml`.
