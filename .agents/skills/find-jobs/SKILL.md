---
name: find-jobs
description: Atomically claim and complete one or a sequential batch of exact V4 career-source job searches under the preserved search profile, using deterministic structured adapters before semantic review, or audit/recover the job queue. Use for `$find-jobs --claim-next luna|terra|sol [--batch-size 1..20]`, `$find-jobs --audit-and-enqueue`, and `$find-jobs --recover-stale` in the V4 Daily Jobs repository.
---

# Find Jobs

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit queue JSON, `data/companies.json`, or canonical job snapshots directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue jobs audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue jobs recover-stale --json`, report recovered IDs, and stop.
- For `--claim-next MODEL`, require exactly `luna`, `terra`, or `sol`. Accept optional `--batch-size N`; default to 1 and require an integer from 1 through 20.

## Batch discipline

Process a batch as a sequential loop over independent source tasks. Never reserve the entire batch or combine sources into one result/snapshot. Create one unique label such as `job-batch:<uuid>` and pass it as `--claimed-by` on every claim in the run. The loop counter enforces `N`; the queue atomically prevents source overlap.

Finalize the current source before claiming the next. Continue after a successfully finalized `completed`, `inconclusive`, `blocked`, or `failed` item unless its report sets `queue_delta.stop_batch` to `true`. Do not voluntarily end a healthy batch early. Stop before another claim only for one of these explicit reasons: `requested_limit_reached`, `queue_empty`, `semantic_candidate_budget`, `environment_network`, `permission_denied`, `finalization_error`, `invariant_error`, or `execution_budget`. If interrupted, at most the current source remains running.

The circuit breaker is mandatory: after any result or finalizer report marks `environment_network` or `stop_batch: true`, finalize/release only that current task and end the whole batch immediately. Never test the same systemic outage against another source.

Job sources vary from zero to hundreds of postings. Also stop the batch early after reviewing 25 semantic candidates total; report the unused batch capacity. This is a work-budget guard, not an error.

## Claim and scope

For each batch iteration, run exactly once:

```bash
.venv/bin/python scripts/v4-queue jobs claim-next --model MODEL --claimed-by BATCH_ID --json
```

If `execution_status` is `no_task`, end the loop successfully. Otherwise retain the claim token, attempt ID, lease, exact company/source IDs, source revision, source instructions, previous snapshot, and exact profile from `task.context`.

Before research, assert `task.workflow == "find_jobs"`, `task.claim.claimed_by == BATCH_ID`, exactly one company ID, and exactly one source ID. A mismatch is a systemic invariant failure: do not research or finalize it, and report the failure.

Never scan another source. Do not relax the profile. The command label cannot verify the model selected in the Codex UI; ensure it matches `task.expected_model`.

## Prepare deterministic structured work

When `task.context.structured_adapter_available` is true, run:

```bash
.venv/bin/python scripts/v4-queue jobs prepare-result --task-id TASK_ID --claim-token CLAIM_TOKEN --attempt-id ATTEMPT_ID --model MODEL --json
```

This command performs live network access. Run its tool call with escalated network permission from the outset; do not first try it in the restricted sandbox. Request narrowly scoped approval for the `.venv/bin/python scripts/v4-queue jobs prepare-result` command prefix when the execution surface supports persistent approval. If permission is denied, do not synthesize or finalize a source-access failure: stop the batch and report that the current claim will require explicit stale recovery after its lease expires.

This fetches the official structured endpoint, proves/records enumeration completeness, normalizes and deduplicates jobs, applies high-confidence profile predicates, and writes a result draft under `data/results/jobs/`. A fetch/access failure produces a ready-to-finalize `blocked` draft so retry classification remains deterministic.

A local DNS resolver failure is emitted as `failure_class: environment_network`. Finalize that draft exactly once: the helper releases the task back to Luna/Terra/Sol after a short delay without consuming its per-source access retry allowance and returns `stop_batch: true`. Then stop the batch.

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

- move supported candidates into `snapshot.qualifying_jobs` using an object containing its `stable_id` plus concise semantic evidence/annotations; list membership is authoritative and the finalizer adds `qualification_status: qualifying` when omitted;
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

After finalization, retain only the task/source/company IDs, provider, fetched/qualifying/unresolved counts, final status, follow-up model, and warning count in the batch accumulator. Do not retain full job descriptions or result bodies in conversation after they are persisted.

At batch end, report one line per attempted source first, then totals for requested, claimed, completed, inconclusive, blocked/retry, failed, sources scanned, jobs fetched, qualifying jobs, semantic candidates reviewed, follow-ups, and unused capacity. Always include exactly one `Stop reason:` using the explicit reason names above; `execution_budget` is a safe partial batch, not a data failure. Use only accumulated helper/finalizer reports and do no extra searching for the summary.
