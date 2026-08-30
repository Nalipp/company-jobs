---
name: find-jobs
description: Atomically claim and complete one exact V4 career-source job search under the preserved search profile, using deterministic structured adapters before semantic review, or audit/recover the job queue. Use for `$find-jobs --claim-next luna|terra|sol`, `$find-jobs --audit-and-enqueue`, and `$find-jobs --recover-stale` in the V4 Daily Jobs repository.
---

# Find Jobs

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit queue JSON, `data/companies.json`, or canonical job snapshots directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue jobs audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue jobs recover-stale --json`, report recovered IDs, and stop.
- For `--claim-next MODEL`, require exactly `luna`, `terra`, or `sol`, then follow the workflow below.

## Claim and scope

Run exactly once:

```bash
.venv/bin/python scripts/v4-queue jobs claim-next --model MODEL --json
```

If `execution_status` is `no_task`, report it and stop. Otherwise retain the claim token, attempt ID, lease, exact company/source IDs, source revision, source instructions, previous snapshot, and exact profile from `task.context`.

Never scan another source. Do not relax the profile. The command label cannot verify the model selected in the Codex UI; ensure it matches `task.expected_model`.

## Prepare deterministic structured work

When `task.context.structured_adapter_available` is true, run:

```bash
.venv/bin/python scripts/v4-queue jobs prepare-result --task-id TASK_ID --claim-token CLAIM_TOKEN --attempt-id ATTEMPT_ID --model MODEL --json
```

This fetches the official structured endpoint, proves/records enumeration completeness, normalizes and deduplicates jobs, applies high-confidence profile predicates, and writes a result draft under `data/results/jobs/`. A fetch/access failure produces a ready-to-finalize `blocked` draft so retry classification remains deterministic.

For a ready non-structured source, follow its exact source instructions. Claim `complete` only when the official source was fully enumerated with stable identities. If completeness or access cannot be established, create a `blocked` result with the appropriate access failure instead of closing prior jobs. Read `schemas/job-result.schema.json`, `src/v4_daily_jobs/adapters/base.py`, and the prior snapshot contract before manually constructing an envelope.

Renew before the lease expires when necessary:

```bash
.venv/bin/python scripts/v4-queue jobs renew --task-id TASK_ID --claim-token CLAIM_TOKEN --attempt-id ATTEMPT_ID --model MODEL --json
```

## Review the result draft

Editing the draft result is allowed. Never modify `snapshot.adapter_result` provider facts, identities, URLs, fetch counts, or completeness metadata.

Review every `snapshot.unresolved_candidates` item against the exact profile:

- require exact San Francisco city eligibility, not only Bay Area/region;
- allow hybrid/on-site; include unknown workplace only because the profile explicitly does; exclude explicit remote;
- require full-time, using the profile's missing-type assumption but excluding explicit part-time/contract/temp/internship;
- require a published USD annual base maximum of at least $200,000;
- require product/frontend/full-stack/applications/growth/forward-deployed role fit and reject exact infrastructure/platform/SRE/DevOps exclusions;
- require job-specific official-posting evidence of relevant AI/ML work; employer AI focus alone is insufficient;
- treat seniority as informational.

For each reviewed candidate:

- move supported candidates into `snapshot.qualifying_jobs` using an object containing its `stable_id` plus concise semantic evidence/annotations;
- remove reviewed candidates from `snapshot.unresolved_candidates` whether qualifying or rejected;
- increment explicit semantic-rejection reason counts in `snapshot.rejection_counts` when rejected;
- add an `item_results` entry with `resolved`, `unchanged`, `inconclusive`, `blocked`, or `failed`.

Use `resolved` when every candidate was decided, including a valid zero-match scan. Finding zero qualifying jobs is not failure. Use `inconclusive` only when material semantic evidence remains unresolved; leave those candidates in the array so the next model can review the immutable result file. Access failures use `blocked` and stay at the same model.

## Finalize

Run:

```bash
.venv/bin/python scripts/v4-queue jobs finalize --result /ABSOLUTE/PATH/TO/RESULT.json --json
```

The finalizer verifies the token and target revision, reconciles lifecycle history, prevents incomplete fetches from closing jobs, commits through the journaled safe path, and handles retry/escalation. Never directly repair queue or canonical JSON after an error.

End with the finalizer's item results first, then source/run counts and current-progress footer. Do no extra searching merely for the summary.

