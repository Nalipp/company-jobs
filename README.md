# V4 Daily Jobs

V4 is a local-first company-validation and job-search system built around readable JSON, deterministic queues, exact task ownership, and safe canonical application.

Multiple Codex tasks may paste the same model command concurrently. Each helper invocation locks and reloads the queue, then atomically claims a different task. Agents never edit queue or canonical JSON directly.

## Current migrated checkpoint

The V3 migration preserved:

- 3,036 companies: 930 `verified`, 426 `outside_sf`, 1,553 `not_found`, and 127 `pending`;
- 878 career sources: 543 ready, 178 no-careers-found, 132 unsupported, 20 blocked, and 5 stale;
- all 1,446 non-job V3 issue/evidence records with handoff provenance;
- the primary `ai-product-frontend-v1` search profile byte-for-byte;
- the five known duplicate website-domain groups as explicit audit warnings.

Historical V3 job scans and generated job reports were intentionally not migrated.

## Setup

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

The migration is already applied in this repository. For a genuinely fresh V4 destination only, run:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/python scripts/v4-queue migrate-v3 --v3-root /Users/natelipp/ai-code/_v3_daily-jobs --json
```

The migration refuses to overwrite changed V4 canonical data. Do not use `--force` after V4 work begins.

In the Codex project, keep `/Users/natelipp/ai-code/_v4_daily-jobs` as the primary source folder. V3 may remain attached for read-only reference. `/Users/natelipp/Documents/ChatGPT/v4` is not part of this implementation and may be removed from the project UI.

## Routine workflow

Choose the matching model in the Codex UI, open as many tasks as desired, and paste one reusable command into each:

```text
$validate-companies --claim-next luna
$validate-companies --claim-next terra
$validate-companies --claim-next sol

$find-jobs --claim-next luna
$find-jobs --claim-next terra
$find-jobs --claim-next sol
```

The repository can enforce the model label requested by the command and task; it cannot independently confirm which model was selected in the Codex UI.

Each command:

1. atomically claims one exact company or career source;
2. uses deterministic processing before semantic research;
3. writes a schema-valid result envelope under `data/results/`;
4. finalizes through the claim-token-authorized safe applier;
5. prints item outcomes, changes, retry/escalation, and a short progress footer.

An empty model queue is a successful no-op and changes no files.

## Populate recommended work

```text
$validate-companies --audit-and-enqueue
$find-jobs --audit-and-enqueue
```

Audits are deterministic and idempotent. They inspect current canonical state and enqueue missing/stale work without executing it. Repeating an unchanged audit leaves queue bytes and revisions unchanged.

The migrated queues have already received their initial audits. Current counts should always be read with the status command rather than copied from this README.

## Progress summary

```text
$daily-jobs-status
```

This read-only command reports:

- queue counts by workflow, model, and status;
- active versus expired running claims and due versus delayed retries;
- company address-status and career-source coverage;
- current/stale/incomplete/missing job snapshots;
- qualifying jobs and unresolved candidates;
- blocked/failed/human-review work, integrity warnings, and recent activity.

It performs no research, enqueueing, recovery, or mutation.

## Recovery

Leases default to 120 minutes for company work and 60 minutes for job work. Skills renew long runs before expiration.

After an agent is abandoned, explicitly recover expired work:

```text
$validate-companies --recover-stale
$find-jobs --recover-stale
```

Recovery invalidates the old claim token and returns the same task to `open` at the same model. The abandoned agent can no longer commit.

Inspect exact queue state without mutation:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/python scripts/v4-queue company inspect --status open --model luna --json
.venv/bin/python scripts/v4-queue jobs inspect --status blocked --json
```

Blocked or failed work may be explicitly retried at the same model:

```bash
.venv/bin/python scripts/v4-queue company retry --task-id ACTUAL_TASK_ID --reason operator_retry --json
.venv/bin/python scripts/v4-queue jobs retry --task-id ACTUAL_TASK_ID --reason operator_retry --json
```

Replace `ACTUAL_TASK_ID` with an ID returned by `inspect`; do not paste the literal example.

## Model routing

- A genuine Luna `inconclusive` result atomically creates an equivalent Terra child.
- A genuine Terra `inconclusive` result creates an equivalent Sol child.
- A Sol `inconclusive` result is retained for human review and does not escalate.
- Network, authentication, rate-limit, robots, timeout, and source-access failures retry or block at the same model.
- Invalid output/invariants fail rather than consuming a stronger model.

Parent/child lineage, prior result paths, evidence counts, attempts, and resolutions stay visible in queue JSON.

## Deterministic job support

V4 includes offline-tested Ashby, Greenhouse, and Lever adapters with:

- official endpoint construction;
- stable source-scoped job IDs and canonical URLs;
- pagination/completeness and duplicate checks;
- conservative normalization of explicit workplace, employment, location, and compensation facts;
- exact profile predicates before semantic review;
- lifecycle reconciliation that never closes jobs after an incomplete fetch.

Other ready sources use their stored source instructions and require agent enumeration. Ambiguous compensation text, role fit, and job-specific AI involvement remain semantic decisions.

At the migrated checkpoint, 365 ready sources have deterministic adapter preparation and 178 use their source-specific agent-enumeration instructions. Pinterest is labeled as a Greenhouse provider but remains browser-required in V3 evidence, so it correctly stays in the latter group.

## Important files

- `V4_IMPLEMENTATION_PLAN.md` — complete architecture and phased contract.
- `AGENTS.md` — mutation and safety rules for all agents.
- `.agents/skills/` — the three user-facing repository skills.
- `scripts/v4-queue` — the only operational CLI boundary.
- `src/v4_daily_jobs/` — queue, storage, migration, adapters, filtering, finalization, audits, and reporting.
- `schemas/` — persisted document and result contracts.
- `archive/v3/migration-manifest.json` — exact migration input/output digests, counts, exclusions, and preserved-data warnings.

Queue files are intentionally readable in Vim. Searching for `"status": "open"` is safe; editing it is not.
