---
name: validate-companies
description: Run persistent, concurrency-safe V4 workers that drain company identity, address, and career-source validation tasks, or claim one diagnostic task, audit, and recover the queue. Use for `$validate-companies --run luna|terra|sol`, `$validate-companies --claim-next luna|terra|sol`, `$validate-companies --audit-and-enqueue`, and `$validate-companies --recover-stale`.
---

# Validate Companies

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit `data/companies.json` or either queue JSON directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue company audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue company recover-stale --json`, report recovered IDs, and stop.
- For `--run MODEL`, require exactly `luna`, `terra`, or `sol` and follow the persistent worker loop below.
- For `--claim-next MODEL`, process exactly one task and stop. Keep it only as a diagnostic escape hatch.

## Persistent worker

For `--run`, create one unique literal label such as `company-worker:<uuid>`. Immediately create a persisted Codex goal whose objective contains that label, model, and the requirement to continue until `queue_empty` or a systemic stop. Do not set a token budget. If goal creation is unavailable or fails, claim nothing and stop with `invariant_error`.

On automatic continuation, reuse the active goal and exact label. A rollout boundary, compaction, elapsed time, or task count is not a terminal condition. Do not send a final response while the goal remains active.

Claim and finalize only one company at a time. After a successful finalization, immediately claim again. Stop only for `queue_empty`, `permission_denied`, `finalization_error`, or `invariant_error`. A platform abandonment can strand at most the current company for stale recovery.

## Claim and scope

For each worker iteration, run exactly once:

```bash
.venv/bin/python scripts/v4-queue company claim-next --model MODEL --claimed-by WORKER_ID --json
```

Replace `MODEL` and `WORKER_ID` with the requested lowercase model and unique worker label. If `execution_status` is `no_task`, end successfully. Otherwise retain the returned ownership and scope fields.

Before research, assert `task.workflow == "validate_companies"`, `task.claim.claimed_by == WORKER_ID`, exactly one company ID, and only source IDs belonging to that company. A mismatch is a systemic invariant failure: do not research or finalize it, and report the failure.

Treat `task.target.company_ids` and `task.target.source_ids` as an absolute boundary. Do not research or change another company/source. The command label cannot verify the model selected in the Codex UI; ensure it matches `task.expected_model`.

## Research

Address every `reason_code` in one bounded pass. Use task context and archived evidence first; verify current facts when needed.

If `task.context.job_source_revalidation_requests` is present, treat every entry as required scope even when the task was augmented after its original audit. Re-establish the official careers relationship, current provider, board ID/listings URL, access mode, capabilities, and source status. A confirmed retired or moved feed must be repaired or marked accurately; do not return the obsolete source as ready.

- Prefer official company contact, about, privacy, terms, or careers pages. Then use maps/business listings or reputable reporting/directories.
- Record `verified` only for credible evidence of a physical San Francisco office. Missing suite numbers are acceptable.
- Record `outside_sf` only for a credible physical office outside San Francisco.
- Record `not_found` only after a bounded search finds no credible physical office. Virtual mailboxes, registered agents, and ambiguous identities are not physical-office evidence.
- Keep `pending` only when validation was not completed.
- Establish an employer-controlled relationship before accepting a job-board tenant as official.
- Prefer verified Ashby, Greenhouse, or Lever structured boards. Preserve source instructions and capabilities without inventing completeness.
- Do not overwrite a conflicting non-null fact silently. Include the conflict in evidence and use `inconclusive` when it prevents a supported decision.

Renew before the lease expires when necessary:

```bash
.venv/bin/python scripts/v4-queue company renew --task-id TASK_ID --claim-token CLAIM_TOKEN --attempt-id ATTEMPT_ID --model MODEL --json
```

## Write the result envelope

Read `schemas/company-result.schema.json` and `schemas/companies.schema.json`. Create one unique JSON result under `data/results/companies/`; editing a result envelope is allowed. Do not edit canonical or queue JSON.

Required decisions:

- `resolved`: every material reason is resolved. Include the exact allow-listed patch; an unchanged supported result may use empty changes.
- `inconclusive`: evidence remains genuinely insufficient or conflicting. Include any independently safe partial company/source changes; the finalizer applies them and creates the Terra/Sol child at the new record revision.
- `blocked`: access/network/authentication/rate-limit/robots/source failure prevented the work. Use the precise `failure_class`; this retries at the same model and never escalates.
- `failed`: the attempt/result itself is invalid. Do not use this for weak evidence.

Set `patch.company_id` and `patch.expected_record_revision` from the claim. `company_changes` may contain only the fields permitted by `src/v4_daily_jobs/companies.py`. Each source upsert must contain the complete source record fields expected by `schemas/companies.schema.json`, omit `record_revision`, and add `expected_record_revision` (`null` for a new source; current source revision for an update). It must keep the claimed company ID.

Include concise evidence entries with URL, retrieval time, evidence type, and finding. Include one `item_results` entry for each address/identity/source item actually evaluated. Never put secrets in a result.

## Finalize

Run:

```bash
.venv/bin/python scripts/v4-queue company finalize --result /ABSOLUTE/PATH/TO/RESULT.json --json
```

The finalizer alone verifies the claim token, locks queue then canonical data, applies keyed changes, records idempotency, commits the queue outcome, and enqueues any escalation. Do not compensate for a rejected finalization by directly editing JSON.

After finalization, retain only compact status/change/follow-up counts. Do not carry full evidence or result bodies forward after the files preserve them.

At worker end, mark the persisted goal complete, then report compact totals for claimed, completed, inconclusive, blocked/retry, failed, follow-ups, and warnings. Include the stop reason. Use accumulated reports only; add no summary-only research.
