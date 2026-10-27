# V4 Daily Jobs

Read `V4_IMPLEMENTATION_PLAN.md` and `README.md` before changing workflow behavior.

## Canonical state

- `data/companies.json` is the canonical company and embedded career-source database.
- `data/profiles/ai-product-frontend-v1.json` is the active immutable search profile.
- `data/jobs/sources/*.json` are canonical per-source job snapshots.
- `data/queues/company-queue.json` and `data/queues/job-queue.json` are readable operational history.
- `archive/v3/` records migration provenance and preserved V3 research. It is not a queue trigger by itself.

Never directly edit canonical or queue JSON during operational work. All mutations go through `.venv/bin/python scripts/v4-queue`. Agents may create or edit their own immutable result envelope under `data/results/`, then must finalize it through the helper.

## User workflows

- `$validate-companies --run luna|terra|sol`
- `$validate-companies --claim-next luna|terra|sol` (single-task diagnostic)
- `$validate-companies --audit-and-enqueue`
- `$validate-companies --recover-stale`
- `$find-jobs --run luna|terra|sol --lane structured|manual`
- `$find-jobs --claim-next luna|terra|sol --lane structured|manual` (single-task diagnostic)
- `$find-jobs --audit-and-enqueue`
- `$find-jobs --recover-stale`
- `$daily-jobs-status`
- `.venv/bin/v4-queue next-commands` (read-only terminal planner)
- `.venv/bin/v4-queue telemetry` (read-only local worker-history summary)

Each claimed task is an absolute scope boundary. A worker must possess the current claim token and target revision to finalize. Never copy work from another target into a result.

Run commands are persistent workers, not multi-target queue tasks. Before the
first claim, create a persisted Codex goal containing one unique worker label,
workflow, model, and the requirement to continue until `queue_empty` or a
documented systemic stop. Reuse that goal and label across automatic
continuations. A rollout boundary is not worker completion.

Finalize the current task before the next claim. Successfully finalized item
outcomes must continue. Run structured `jobs prepare-result` calls with
approved network access. `environment_network`/`stop_worker: true` is a
mandatory circuit breaker and does not consume a source retry.

Before every `jobs finalize`, run `jobs preflight-result --result RESULT_PATH`.
It is read-only and validates the active claim, target/profile scope, normalized
job ownership and stable IDs, and manual candidate accounting. If it fails,
repair that exact result or stop; never submit the malformed result to finalize.

Job workers must stay in their claimed lane. The structured helper must never
be used for a manual source. Claim a task only when ready to process it in the
same continuation; never pre-claim the next task before yielding.

## Safety invariants

- Global lock order is company queue, job queue, company database, then job snapshots. A workflow finalizer takes only its queue before the relevant canonical locks.
- Lock stable sibling `.lock` files, not replaceable JSON inodes.
- Reload after locking; validate before/after mutation; save atomically; preserve no-op bytes/revisions.
- Company and source `record_revision` values are target-scoped concurrency guards. Do not use the root document revision as a task precondition.
- Incomplete/failed job enumeration cannot close prior jobs.
- Luna/Terra/Sol escalation is only for `inconclusive`. Access failures retry or block at the same model.
- Sol inconclusive requires human review and creates no further task.
- Replayed enqueue, audit, and result operations must be idempotent.
- Release a known stopped agent's unexpired claim only through the exact-task `abandon` command; use `recover-stale` after expiry.
- Do not mutate `/Users/natelipp/ai-code/_v3_daily-jobs`; it is migration provenance only.

## Verification

Run:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/python -m pytest -q
.venv/bin/python /Users/natelipp/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/validate-companies
.venv/bin/python /Users/natelipp/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/find-jobs
.venv/bin/python /Users/natelipp/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/daily-jobs-status
.venv/bin/python scripts/v4-queue status
.venv/bin/v4-queue next-commands
```

Routine completion output must state per-item outcomes, canonical changes, queue transition/follow-up, warnings, and the deterministic progress footer. Do not perform extra research solely to prepare a summary.

Queue-helper operations automatically write compact events under
`data/telemetry/`. Do not put claim tokens, prompts, evidence, instructions, or
job descriptions into telemetry. Telemetry is observability only and must never
be used as canonical input or edited to affect workflow state.
