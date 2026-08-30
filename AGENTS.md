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

- `$validate-companies --claim-next luna|terra|sol`
- `$validate-companies --audit-and-enqueue`
- `$validate-companies --recover-stale`
- `$find-jobs --claim-next luna|terra|sol`
- `$find-jobs --audit-and-enqueue`
- `$find-jobs --recover-stale`
- `$daily-jobs-status`

Each claimed task is an absolute scope boundary. A worker must possess the current claim token and target revision to finalize. Never copy work from another target into a result.

## Safety invariants

- Global lock order is company queue, job queue, company database, then job snapshots. A workflow finalizer takes only its queue before the relevant canonical locks.
- Lock stable sibling `.lock` files, not replaceable JSON inodes.
- Reload after locking; validate before/after mutation; save atomically; preserve no-op bytes/revisions.
- Company and source `record_revision` values are target-scoped concurrency guards. Do not use the root document revision as a task precondition.
- Incomplete/failed job enumeration cannot close prior jobs.
- Luna/Terra/Sol escalation is only for `inconclusive`. Access failures retry or block at the same model.
- Sol inconclusive requires human review and creates no further task.
- Replayed enqueue, audit, and result operations must be idempotent.
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
```

Routine completion output must state per-item outcomes, canonical changes, queue transition/follow-up, warnings, and the deterministic progress footer. Do not perform extra research solely to prepare a summary.

