---
name: validate-companies
description: Atomically claim and complete one or a sequential batch of V4 company-validation tasks, including company identity/address validation and career-source discovery, or deterministically audit/recover the company queue. Use for `$validate-companies --claim-next luna|terra|sol [--batch-size 1..75]`, `$validate-companies --audit-and-enqueue`, and `$validate-companies --recover-stale` in the V4 Daily Jobs repository.
---

# Validate Companies

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit `data/companies.json` or either queue JSON directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue company audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue company recover-stale --json`, report recovered IDs, and stop.
- For `--claim-next MODEL`, require exactly `luna`, `terra`, or `sol`. Accept optional `--batch-size N`; default to 1 and require an integer from 1 through 75.

## Batch discipline

Process a batch as a sequential loop over independent queue tasks. Never claim the whole batch in advance and never combine companies into one result. Create one unique label such as `company-batch:<uuid>` and pass it as `--claimed-by` on every claim in the run. Repeat the complete claim, research, result, and finalize workflow until `N` items have finalized or `claim-next` returns `no_task`.

Finalize the current company before claiming the next. After a successfully finalized `completed`, `inconclusive`, `blocked`, or `failed` item, record its compact operation report and continue. Stop without claiming another item when finalization is rejected, a helper invariant fails, or a systemic tool/access problem prevents safe completion. An interrupted batch may therefore strand at most one running task.

## Claim and scope

For each batch iteration, run exactly once:

```bash
.venv/bin/python scripts/v4-queue company claim-next --model MODEL --claimed-by BATCH_ID --json
```

Replace `MODEL` and `BATCH_ID` with the requested lowercase model and unique run label. If `execution_status` is `no_task`, end the loop successfully. Otherwise retain the returned `task_id`, `attempt_id`, `claim_token`, target IDs, target revision, reason codes, context, and lease.

Before research, assert `task.workflow == "validate_companies"`, `task.claim.claimed_by == BATCH_ID`, exactly one company ID, and only source IDs belonging to that company. A mismatch is a systemic invariant failure: do not research or finalize it, and report the failure.

Treat `task.target.company_ids` and `task.target.source_ids` as an absolute boundary. Do not research or change another company/source. The command label cannot verify the model selected in the Codex UI; ensure it matches `task.expected_model`.

## Research

Address every `reason_code` in one bounded pass. Use task context and archived evidence first; verify current facts when needed.

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

After finalization, retain only the task ID, company ID/name, final status, concise canonical changes, follow-up model, and warning count in the batch accumulator. Do not carry full evidence or result bodies forward in conversation when the files already preserve them.

At batch end, report one line per attempted company first, then totals for requested, claimed, completed, inconclusive, blocked/retry, failed, Terra/Sol follow-ups, and unprocessed capacity. Use only accumulated finalizer reports; add no new research or full-database scan. For batch size 1, preserve the same output shape with one item.
