---
name: find-jobs
description: Run persistent, concurrency-safe V4 structured or manual job-source workers under the preserved profile, or claim one diagnostic task, audit, and recover the queue. Use for `$find-jobs --run luna|terra|sol --lane structured|manual`, `$find-jobs --claim-next luna|terra|sol --lane structured|manual`, `$find-jobs --audit-and-enqueue`, and `$find-jobs --recover-stale`.
---

# Find Jobs

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit queue JSON, `data/companies.json`, or canonical job snapshots directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue jobs audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue jobs recover-stale --json`, report recovered IDs, and stop.
- For `--run MODEL --lane LANE`, require one model (`luna`, `terra`, or `sol`) and one lane (`structured` or `manual`), then follow the persistent worker loop.
- For `--claim-next MODEL --lane LANE`, process exactly one task in that lane, then report and stop. This is a diagnostic escape hatch.

## Persistent worker

For `--run`, create one unique literal label such as `job-worker:<uuid>`. Immediately create a persisted Codex goal whose objective contains that label, model, lane, and the requirement to continue until that lane is empty or a permitted systemic stop. Do not set a token budget. If goal creation is unavailable or fails, claim nothing and stop with `invariant_error`.

On automatic continuation, reuse the active goal and exact worker label. A rollout boundary, compaction, elapsed time, source count, candidate count, or token usage is not a terminal condition. Do not send a final response while the goal remains active.

Claim and finalize only one source at a time. Claim only when ready to begin that source immediately in the current continuation. Never pre-claim a source merely to preserve it for the next continuation, and never end an update with “next claimed.” After finalization, claim again only when immediately continuing into its processing; otherwise yield unclaimed and let the next continuation claim first. Stop only for `queue_empty`, `environment_network`, `permission_denied`, `finalization_error`, or `invariant_error`.

The circuit breaker is mandatory: after any result or finalizer report marks `environment_network` or `stop_worker: true`, finalize/release the current task and stop the worker. Never test a systemic outage against another source.

Job sources vary from zero to hundreds of postings. Fully review the current source even when it has many candidates; renew its lease as needed. Never use source size, elapsed time, token usage, a semantic-candidate budget, or an execution budget as an inconclusive reason or as a voluntary worker stop.

Keep intermediate narration minimal. Persist source details in result files and retain only compact totals in conversation.

## Claim and scope

For each worker iteration, run exactly once:

```bash
.venv/bin/python scripts/v4-queue jobs claim-next --model MODEL --lane LANE --claimed-by WORKER_ID --json
```

If `execution_status` is `no_task`, end the lane successfully. Otherwise retain the claim token, attempt ID, lease, exact company/source IDs, source revision, source instructions, previous snapshot, and exact profile from `task.context`. Assert that `structured_adapter_available` is true exactly when `LANE` is `structured`.

Before research, assert `task.workflow == "find_jobs"`, `task.claim.claimed_by == WORKER_ID`, exactly one company ID, and exactly one source ID. A mismatch is a systemic invariant failure: do not research or finalize it, and report the failure.

Never scan another source. Do not relax the profile. The command label cannot verify the model selected in the Codex UI; ensure it matches `task.expected_model`.

## Structured lane

Only in the `structured` lane, run:

```bash
.venv/bin/python scripts/v4-queue jobs prepare-result --task-id TASK_ID --claim-token CLAIM_TOKEN --attempt-id ATTEMPT_ID --model MODEL --json
```

This command performs live network access. Run its tool call with escalated network permission from the outset; do not first try it in the restricted sandbox. Request narrowly scoped approval for the `.venv/bin/python scripts/v4-queue jobs prepare-result` command prefix when the execution surface supports persistent approval. If permission is denied, do not synthesize or finalize a source-access failure: stop the worker and report that the current claim requires explicit stale recovery after its lease expires.

This fetches the official structured endpoint, proves/records enumeration completeness, normalizes and deduplicates jobs, applies high-confidence profile predicates, and writes a result draft under `data/results/jobs/`. A fetch/access failure produces a ready-to-finalize `blocked` draft so retry classification remains deterministic.

A local DNS resolver failure is emitted as `failure_class: environment_network`. Finalize that draft exactly once: the helper releases the task back to Luna/Terra/Sol after a short delay without consuming its per-source access retry allowance and returns `stop_worker: true`. Then stop the worker.

Never call `prepare-result` in the manual lane. The helper rejects that call and it must never be finalized as a source-access failure.

## Manual lane

Use browser/web research to follow the claimed source's exact official instructions. Exhaust pagination, lazy loading, location filters, and stable job-detail links. Do not treat the lack of a deterministic adapter as failure; it is the reason this lane exists.

Read `schemas/job-result.schema.json`, `src/v4_daily_jobs/adapters/base.py`, and the prior snapshot contract. Construct one result envelope with a complete manual `adapter_result`: provider facts, normalized jobs, stable source-scoped identities, and truthful fetch counts/completeness. Every individual entry in `adapter_result.jobs` must repeat the enclosing `company_id`, `source_id`, and `provider`, and must include its own non-empty `source_job_id`, correctly derived `stable_id`, and canonical detail URL. Apply the same deterministic profile evaluation before semantic review.

Manual snapshots must include `semantic_decisions`, including an empty array when no viable candidates remain. Add exactly one entry for every candidate not deterministically rejected: its `stable_id`, decision (`qualifying`, `rejected`, or `inconclusive`), and non-empty job-specific `review_notes`. Keep qualifying decisions in `qualifying_jobs`, inconclusive decisions in `unresolved_candidates`, and rejected decisions in neither. Remember that a published maximum exactly equal to $200,000 passes the `at least $200,000` constraint.

Claim `resolved` only after complete enumeration and complete candidate accounting. If a finite enumeration is merely unfinished at a continuation or usage boundary, do not finalize it as blocked; renew when possible and continue later. If the official portal is structurally unbounded, has unusable mandatory filtering, or cannot provide a provably complete target set after a bounded investigation, use `reason_code: manual_source_enumeration_unsupported`, `failure_class: source_access`, and `retryable: false` so the identical attempt is not repeated. Confirmed official 404/410 or retired sources are also non-retryable. Ordinary timeouts, rate limits, and transient access failures remain retryable.

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

Use `resolved` when every candidate was decided, including a valid zero-match scan. Finding zero qualifying jobs is not failure. Never finalize the untouched prepared draft. Use `inconclusive` only after reviewing every retained candidate and finding a genuine evidence gap: add a non-empty `review_notes` explanation to each unresolved candidate and use the **only allowed escalation reason** `reason_code: semantic_evidence_inconclusive`. Unreviewed candidates, partial review, source size, work budget, incompatible detail pages, or source access may not escalate to Terra/Sol; continue/retry at Luna or use `blocked` at the same model as appropriate.

## Finalize

Before every finalization, run this read-only deterministic preflight against
the exact result file:

```bash
.venv/bin/python scripts/v4-queue jobs preflight-result --result /ABSOLUTE/PATH/TO/RESULT.json --json
```

It verifies schema, the current claim, target/profile scope, every normalized
job's source ownership and stable identity, and manual candidate accounting.
It changes neither the queue nor canonical data. If it fails, do **not** call
`finalize`; repair the same result while the claim remains valid, then rerun
preflight. If the result cannot be repaired, stop with `finalization_error`
and leave the task for explicit `abandon`/recovery rather than attempting a
different source.

Run:

```bash
.venv/bin/python scripts/v4-queue jobs finalize --result /ABSOLUTE/PATH/TO/RESULT.json --json
```

The finalizer verifies the token and target revision, reconciles lifecycle history, prevents incomplete fetches from closing jobs, commits through the journaled safe path, and handles retry/escalation. Never directly repair queue or canonical JSON after an error.

After finalization, retain only provider, fetched/qualifying/unresolved counts, final status, follow-up model, and warning count. Do not retain descriptions or result bodies after they are persisted.

At worker end, mark the persisted goal complete, then report compact totals for claimed, completed, genuinely inconclusive, blocked/retry, failed, sources scanned, jobs fetched, qualifying jobs, semantic candidates reviewed, follow-ups, and warnings. Include exactly one stop reason from `queue_empty`, `environment_network`, `permission_denied`, `finalization_error`, or `invariant_error`. Use accumulated reports only; do no summary-only research.
