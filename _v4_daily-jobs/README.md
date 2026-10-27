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

Choose the matching model in the Codex UI, open the desired number of parallel workers, and paste one reusable command into each:

```text
$validate-companies --run luna
$validate-companies --run terra
$validate-companies --run sol

$find-jobs --run luna --lane structured
$find-jobs --run luna --lane manual
$find-jobs --run terra --lane structured
$find-jobs --run terra --lane manual
$find-jobs --run sol --lane structured
$find-jobs --run sol --lane manual
```

Each worker creates a persisted goal, then repeatedly claims and finalizes one exact task at a time until no due work remains for its model and lane. The structured lane uses Ashby, Greenhouse, and Lever adapters; the manual lane uses official-page browser research for other providers. Ten identical workers can run concurrently without overlap. Workers claim only when ready to process immediately, so a continuation boundary does not intentionally strand the next item.

Structured job fetching requires network-authorized execution of the narrowly scoped `jobs prepare-result` helper. A local DNS/sandbox failure is classified as `environment_network`: it releases the current task after a short delay without consuming that source's access-retry budget and sets `stop_worker: true`. The worker then stops before claiming another source.

Lane enforcement is deterministic: `claim-next --lane structured` cannot claim a manual source, and `prepare-result` refuses a manual claim without consuming a retry. Confirmed structured-feed 404/410 responses block immediately and automatically create or augment a company source-revalidation task instead of repeating the same request.

The repository can enforce the model label requested by the command and task; it cannot independently confirm which model was selected in the Codex UI.

Each item within a command:

1. atomically claims one exact company or career source;
2. uses deterministic processing before semantic research;
3. writes a schema-valid result envelope under `data/results/`;
4. runs a read-only result preflight that verifies the current claim, exact
   target/profile scope, per-job source ownership, stable identity, and manual
   candidate accounting;
5. finalizes through the claim-token-authorized safe applier;
6. prints item outcomes, changes, retry/escalation, and a short progress footer.

At worker completion the skill prints aggregate claimed/completed/inconclusive/blocked/failed and follow-up totals plus an explicit stop reason. Source size or a self-imposed work budget is never a valid reason to escalate models. A structured draft may be finalized as inconclusive only after every retained candidate has explicit review notes documenting a genuine evidence gap.

An empty model lane is a successful no-op and changes no files.

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

## HTML results browser

The dependency-free, read-only results browser lives in `site/`. It shows every
company with an active matching job, a company detail page with that company's
roles, and a job detail page with the useful canonical posting fields.

Regenerate the browser data after job workers finalize new results:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/python scripts/build-results-site
```

Then serve `site/` with any static web server and open `index.html`. The browser
never writes to company, job-snapshot, or queue data.

## Worker telemetry

Every queue-helper operation automatically appends one compact event to the
daily JSON Lines log under `data/telemetry/YYYY-MM-DD.jsonl`. Writes use a
separate lock, so concurrent workers cannot interleave or corrupt lines. The
event contains operational identifiers, lane/provider, outcome, failure
classification, duration, and numeric job totals. It intentionally excludes
claim tokens, prompts, evidence, instructions, and job descriptions.

Inspect recent worker activity locally without model tokens or network access:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/v4-queue telemetry
.venv/bin/v4-queue telemetry --days 7 --workflow find_jobs --tail 50
.venv/bin/v4-queue telemetry --worker ACTUAL_WORKER_ID --json
```

The telemetry command is read-only and does not log itself. It reports worker
and task counts, final outcomes, queue states, reasons, providers, aggregate job
metrics, and a compact recent-event tail. Telemetry is diagnostic history, not
canonical state: queue and database decisions never depend on it.

## What to run next

From a terminal, get a deterministic launch plan without using a model:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/v4-queue next-commands
```

It counts due tasks and active workers by workflow and model, then recommends how many new persistent workers to open. It also detects work that a queue audit would add and prints the local deterministic audit command to run first. Recovery, audit, and inspection use the local CLI and consume no model tokens.

Override the planning sizes without changing any queue data:

```bash
.venv/bin/v4-queue next-commands --company-workers 10 --job-workers 10
.venv/bin/v4-queue next-commands --json
```

Re-run the planner after a model lane drains because delayed retries and genuine Luna→Terra→Sol escalation may create later work.

For normal job processing, run the exact lane printed by `next-commands`. For example:

```text
$find-jobs --run luna --lane manual
```

The persisted goal automatically continues across rollouts until the worker's
model lane has no due work. Medium is the normal balance of persistence,
judgment, and cost; use high only for genuinely difficult semantic work.

## Recovery

Leases default to 120 minutes for company work and 60 minutes for job work. Skills renew long runs before expiration.

After an agent is abandoned, explicitly recover expired work:

```text
$validate-companies --recover-stale
$find-jobs --recover-stale
```

Recovery invalidates the old claim token and returns the same task to `open` at the same model. The abandoned agent can no longer commit.

If you intentionally stop an agent and do not want to wait for its lease to expire, release only its exact current task:

```bash
.venv/bin/v4-queue company abandon --task-id ACTUAL_TASK_ID
.venv/bin/v4-queue jobs abandon --task-id ACTUAL_TASK_ID
```

This locked operation preserves the attempt as `abandoned`, invalidates the old claim token, and atomically reopens that one task.

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

For a result envelope being prepared or repaired manually, use the same
read-only preflight the worker skill runs before finalization:

```bash
.venv/bin/python scripts/v4-queue jobs preflight-result --result /ABSOLUTE/PATH/TO/RESULT.json --json
```

It changes neither queue nor canonical data. A failed preflight means repair
the current result file first; do not submit it to `finalize`.

One-time canonical snapshot repairs, when introduced by an upgrade, run through the validated helper rather than direct JSON edits:

```bash
.venv/bin/python scripts/v4-queue jobs repair-snapshots --json
.venv/bin/python scripts/v4-queue jobs repair-escalations --json
```

Both commands are idempotent and normally report zero changes after the
applicable upgrade repair has been applied. The escalation repair reopens only
historical Luna work that was incorrectly stopped for an execution/candidate
budget and supersedes its untouched Terra child; it never rewrites a Terra task
that has been claimed.

## Model routing

- A genuine Luna `inconclusive` result atomically creates an equivalent Terra child.
- A genuine Terra `inconclusive` result creates an equivalent Sol child.
- A Sol `inconclusive` result is retained for human review and does not escalate.
- Network, authentication, rate-limit, robots, timeout, and source-access failures retry or block at the same model. Local environment/DNS failures stop the worker and do not consume a source retry.
- Invalid output/invariants fail rather than consuming a stronger model.

Parent/child lineage, prior result paths, evidence counts, attempts, and resolutions stay visible in queue JSON.

## Deterministic job support

V4 includes offline-tested Ashby, Greenhouse, and Lever adapters with:

- official endpoint construction;
- stable source-scoped job IDs and canonical URLs;
- pagination/completeness and duplicate checks;
- conservative normalization of explicit workplace, employment, location, and compensation facts;
- exact profile predicates and explicit non-engineering title-family rejections before semantic review;
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
- `data/telemetry/*.jsonl` — compact, secret-free operational events from helper calls.
- `archive/v3/migration-manifest.json` — exact migration input/output digests, counts, exclusions, and preserved-data warnings.

Queue files are intentionally readable in Vim. Searching for `"status": "open"` is safe; editing it is not.
