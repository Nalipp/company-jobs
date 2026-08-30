---
name: validate-companies
description: Atomically claim and complete one V4 company-validation task, including company identity/address validation and career-source discovery, or deterministically audit/recover the company queue. Use for `$validate-companies --claim-next luna|terra|sol`, `$validate-companies --audit-and-enqueue`, and `$validate-companies --recover-stale` in the V4 Daily Jobs repository.
---

# Validate Companies

Work only in `/Users/natelipp/ai-code/_v4_daily-jobs`. Never edit `data/companies.json` or either queue JSON directly.

## Dispatch

- For `--audit-and-enqueue`, run `.venv/bin/python scripts/v4-queue company audit-and-enqueue --json`, report its counts, and stop.
- For `--recover-stale`, run `.venv/bin/python scripts/v4-queue company recover-stale --json`, report recovered IDs, and stop.
- For `--claim-next MODEL`, require exactly `luna`, `terra`, or `sol`, then follow the workflow below.

## Claim and scope

Run exactly once:

```bash
.venv/bin/python scripts/v4-queue company claim-next --model MODEL --json
```

Replace `MODEL` with the requested lowercase model. If `execution_status` is `no_task`, report the message and stop. Otherwise retain the returned `task_id`, `attempt_id`, `claim_token`, target IDs, target revision, reason codes, context, and lease.

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

End with the finalizer's item results first, then its run summary and current-progress footer. Add no new research merely to make the summary.

