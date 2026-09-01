# V4 Daily Jobs — Deterministic Queue Implementation Plan

Status: implemented; audited against the acceptance contracts on 2026-08-31
Migration source: `/Users/natelipp/ai-code/_v3_daily-jobs`
Implementation target: `/Users/natelipp/ai-code/_v4_daily-jobs`

## 1. Outcome

V4 provides two repository skills and two reusable operator workflows:

```text
$validate-companies --run luna
$validate-companies --run terra
$validate-companies --run sol

$find-jobs --run luna --lane structured|manual
$find-jobs --run terra --lane structured|manual
$find-jobs --run sol --lane structured|manual
```

The operator starts the desired number of persistent workers and pastes the same lane-specific command into each once. Every worker uses a persisted goal, atomically claims one exact task in its structured or manual lane, finalizes it, and repeats until that lane has no due work. Concurrent workers cannot overlap. Neither the operator nor an agent directly edits queue or canonical database JSON.

The job finalizer rejects untouched drafts, partial semantic reviews, and reason
codes based on candidate or execution budgets. A one-time idempotent maintenance
repair reopens historical Luna tasks that violated this rule and marks only
their never-claimed Terra children `superseded`. Deterministic filtering also
rejects explicit non-engineering occupational titles before model review while
preserving titles with engineering/development signals for semantic judgment.

Automatic continuations reuse the same literal worker label. The agent emits a
final report only after `queue_empty` or a documented systemic stop; rollout
boundaries are never reported as worker completion.

The read-only terminal planner `.venv/bin/v4-queue next-commands` recommends new workers from due counts, current active workers, and configurable concurrency targets. It performs a dry audit calculation and prints deterministic audit/recovery/inspection commands where needed.

The initial implementation must favor a small, auditable Python system over a service, database server, daemon, terminal UI, or distributed scheduler.

## 2. Non-negotiable invariants

1. Queue JSON is readable in Vim but operationally read-only.
2. Every queue mutation passes through one shared queue library.
3. Every canonical JSON mutation passes through one shared safe finalization path.
4. Locks use separate lock files. Never lock the JSON inode because atomic replacement changes it.
5. A claimed task has one exact target: one company for company validation or one career source for job finding.
6. A task can be finalized only with its current opaque claim token.
7. Finalization validates the token before changing canonical data and again while the queue lock remains held.
8. All lock-taking code uses the same order: queue lock, then canonical database lock. No code may acquire these in reverse order.
9. Writes reload current state after locking, validate before and after mutation, write a same-directory temporary file, flush and `fsync`, use `os.replace`, and `fsync` the parent directory where supported.
10. Repeating an audit, enqueue, claim response, or result submission cannot create duplicate work or duplicate canonical records.
11. Model escalation happens only for a genuine evidence-based `inconclusive` result: Luna to Terra, Terra to Sol. Sol never auto-escalates.
12. Network, authentication, rate-limit, robots, and source-access failures remain at the same model. Local sandbox/DNS failures release the current claim without consuming a source retry and stop the worker.
13. Job claims are lane-scoped. Structured helpers reject manual sources without charging retries; confirmed provider 404/410 responses block once and route the source to company revalidation.
14. Workers claim only when ready to process immediately and never reserve the next task across a continuation boundary.
13. An empty eligible queue is a successful no-op and must not rewrite a file.
14. V3 is migration input only. Migration must not mutate it.

## 3. Deliberate simplifications

Implement now:

- two JSON queues;
- a single queue/finalization library and thin CLI;
- repository skills for the two user-facing commands;
- deterministic audits and deterministic parsing/filtering where rules are known;
- compact locked JSONL telemetry plus a read-only local summary command;
- one-company and one-source task scopes;
- local file locks, leases, explicit stale recovery, schemas, and tests;
- Ashby, Greenhouse, and Lever structured adapters first;
- preservation of V3 company and source data plus the primary search profile.

Defer until evidence shows it is needed:

- SQLite, a server, background workers, a web UI, or terminal integration;
- automatic process/model discovery;
- external telemetry services, databases, dashboards, or background collectors;
- task batching with multiple companies or sources;
- generalized workflow engines or plugin systems;
- broad location gazetteers, elaborate title taxonomies, four-layer content hashing, or repair daemons;
- parallel writes to one JSON database outside the finalizer;
- migration of old job scan results.

The deterministic spine must be built first. Adding it later would require replacing the ownership, result, and write paths after agents already depend on them.

## 4. Repository layout

Create this minimal structure:

```text
_v4_daily-jobs/
├── AGENTS.md
├── README.md
├── V4_IMPLEMENTATION_PLAN.md
├── pyproject.toml
├── .agents/skills/
│   ├── validate-companies/SKILL.md
│   ├── find-jobs/SKILL.md
│   └── daily-jobs-status/SKILL.md
├── src/v4_daily_jobs/
│   ├── cli.py
│   ├── json_store.py
│   ├── queue.py
│   ├── schemas.py
│   ├── audit.py
│   ├── migration.py
│   ├── companies.py
│   ├── jobs.py
│   ├── telemetry.py
│   └── adapters/
│       ├── ashby.py
│       ├── greenhouse.py
│       └── lever.py
├── schemas/
│   ├── companies.schema.json
│   ├── company-queue.schema.json
│   ├── company-result.schema.json
│   ├── job-queue.schema.json
│   ├── job-result.schema.json
│   ├── telemetry-event.schema.json
│   └── search-profile.schema.json
├── data/
│   ├── companies.json
│   ├── profiles/ai-product-frontend-v1.json
│   ├── queues/company-queue.json
│   ├── queues/job-queue.json
│   ├── results/companies/
│   ├── results/jobs/
│   ├── jobs/sources/
│   ├── telemetry/YYYY-MM-DD.jsonl
│   └── audit/events.jsonl
├── archive/v3/
│   ├── migration-manifest.json
│   └── company-research.json
├── scripts/v4-queue
└── tests/
```

`data/queues/*.lock` and canonical `*.lock` files are runtime artifacts and should be ignored by Git. Career-source records remain embedded under their companies in `companies.json`; this permits a company result and its source changes to commit as one canonical transaction.

## 5. Queue document and task contract

Each queue is one readable, pretty-printed JSON document:

```json
{
  "schema_version": 1,
  "revision": 12,
  "updated_at": "2026-08-29T18:00:00Z",
  "tasks": []
}
```

Every task contains at least:

```json
{
  "task_id": "company:cmp_123:validate:policy_v1:luna:rev_7",
  "dedupe_key": "company|cmp_123|validate|policy_v1|luna|rev_7",
  "workflow": "validate_companies",
  "status": "open",
  "expected_model": "luna",
  "priority": 50,
  "available_at": "2026-08-29T18:00:00Z",
  "created_at": "2026-08-29T18:00:00Z",
  "updated_at": "2026-08-29T18:00:00Z",
  "target": {
    "company_ids": ["cmp_123"],
    "source_ids": []
  },
  "reason_codes": ["pending_validation"],
  "policy_version": "company_validation_v1",
  "target_revision": 7,
  "context": {
    "company_snapshot": {},
    "source_snapshots": [],
    "relevant_evidence": []
  },
  "attempts": [],
  "parent_task_id": null,
  "root_task_id": "company:cmp_123:validate:policy_v1:luna:rev_7",
  "claim": null,
  "resolution": null
}
```

Rules:

- Company tasks contain exactly one `company_id` and zero or more source IDs belonging to that company.
- Job tasks contain exactly one `source_id` and its owning company ID.
- `context` is a claim-time snapshot sufficient to work independently. It includes prior attempts and evidence; secrets are never embedded.
- `expected_model` is `luna`, `terra`, or `sol`.
- `status` is `open`, `running`, `completed`, `inconclusive`, `blocked`, or `failed`.
- `revision` increments once for each successful queue transaction.
- Timestamps are UTC ISO-8601 with `Z`.
- Stable IDs and dedupe keys are constructed from normalized components, not Python hash values.
- Pretty output uses UTF-8, two-space indentation, sorted object keys where that does not reduce readability, and a trailing newline.

## 6. State machine

Allowed transitions:

```text
open -> running
running -> completed
running -> inconclusive
running -> blocked
running -> failed
running -> open          only through stale recovery, exact-task operator abandon, or retry scheduling
blocked -> open          only through an explicit retry/recovery operation
failed -> open           only through an explicit retry operation
```

Terminal task records remain in the queue for history. Compaction, if ever required, is a later explicit maintenance command.

An escalation creates a new child task; it does not mutate the model of the old task:

- Luna `inconclusive`: parent becomes `inconclusive`; enqueue equivalent Terra child.
- Terra `inconclusive`: parent becomes `inconclusive`; enqueue equivalent Sol child.
- Sol `inconclusive`: parent becomes `inconclusive`, sets `human_review_required: true`, and creates no child.

Access-class failures do not escalate:

- First and second retryable access failures enqueue or reopen equivalent work at the same model with deterministic backoff metadata.
- After two same-model retries, mark `blocked` with a structured reason and human action.
- Keep retry count configurable, with a default of two.
- Invalid result envelopes and invariant violations become `failed`, not `inconclusive`.

## 7. Claim, lease, and recovery protocol

`claim-next` performs one transaction:

1. Acquire the queue's separate lock file with `fcntl.flock(LOCK_EX)`.
2. Reload and schema-validate the queue.
3. Select only `open` tasks whose `expected_model` equals the requested model and whose `available_at` is due.
4. Sort by descending priority, then ascending `created_at`, then ascending `task_id`.
5. If none exist, release the lock and print a machine-readable and human-readable empty result without writing.
6. Generate a cryptographically random `claim_token` and unique `attempt_id`.
7. Set `running`, `claimed_at`, `lease_expires_at`, requested model, agent/run identifier when supplied, and append the attempt.
8. Validate and atomically save the queue.
9. Release the lock and return the exact task snapshot plus token.

Default leases:

- company validation: 120 minutes;
- job finding: 60 minutes.

Provide `renew --task-id ... --claim-token ...` so a skill can extend an active lease. Renewal must fail if the token is stale or the task is no longer running.

Do not automatically steal an expired task during `claim-next`. Provide explicit:

```text
$validate-companies --recover-stale
$find-jobs --recover-stale
```

Recovery runs under the queue lock, records an abandoned attempt, clears the old token, and returns the task to `open` at the same model. The old agent can no longer finalize because its token is invalid.

When the operator intentionally stops an agent before its lease expires, the local `company|jobs abandon --task-id ...` command performs the same locked invalidation for that exact known task without waiting. It must reject non-running tasks and never release work by worker label or broad pattern.

## 8. Result and safe finalization protocol

Agents produce schema-validated result envelopes in a unique immutable path under `data/results`. They never edit canonical or queue JSON. A result includes:

- task ID, attempt ID, claim token, workflow, and model requested;
- exact target IDs;
- outcome and structured reason code;
- evidence with URL, retrieval time, evidence type, and concise finding;
- a keyed canonical patch containing only allowed fields;
- access-failure classification when applicable;
- notes for the next model or a human reviewer.

The finalizer performs this single coordinated transaction:

1. Acquire queue lock.
2. Reload and validate queue.
3. Verify task is `running`, token and attempt match, model matches, target IDs match, and lease is valid. A just-expired lease may be accepted only if it has not been recovered and policy explicitly permits a short grace period; default to no grace.
4. Acquire canonical database lock while still holding the queue lock.
5. Reload and validate canonical data.
6. Verify the task's `target_revision` or expected field preconditions. Never overwrite newer conflicting research.
7. Detect whether this task/attempt was already applied using a canonical application ledger. If already applied, treat the canonical portion as an idempotent no-op.
8. Apply only the allow-listed keyed patch. Reject unknown IDs, duplicate IDs, source ownership changes without explicit support, and full-document replacement.
9. Validate and atomically save canonical data when it changed.
10. Release canonical lock.
11. Update the parent task's terminal state and enqueue any deterministic follow-up child in the same queue transaction.
12. Append an audit event. If audit logging cannot be made part of the same recoverable protocol, canonical and queue records remain the source of truth and the audit log is reconstructable.
13. Validate and atomically save the queue.
14. Release queue lock.

Because two JSON files cannot provide a true filesystem-wide atomic transaction, implement a small write-ahead transaction record before the canonical save. It contains transaction ID, task ID, old/new revisions, intended queue terminal state, result digest, and phase. On every mutating command, recover an unfinished transaction deterministically before proceeding. Keep this journal to one file per workflow and test crashes after each phase. This is the minimum required to prevent a crash between canonical commit and queue completion from causing duplicate or lost work.

The canonical application ledger records transaction/task/result digest. Re-running finalization after recovery is therefore safe.

## 9. CLI contract

Expose one internal executable, `scripts/v4-queue`, with stable subcommands. Repository skills wrap it; they do not reproduce queue logic.

Required internal commands:

```text
scripts/v4-queue company claim-next --model luna --json
scripts/v4-queue company renew --task-id ID --claim-token TOKEN --json
scripts/v4-queue company finalize --result PATH --json
scripts/v4-queue company audit-and-enqueue --json
scripts/v4-queue company recover-stale --json
scripts/v4-queue company retry --task-id ID --json
scripts/v4-queue company inspect --status open --model luna --json

scripts/v4-queue jobs claim-next --model luna --json
scripts/v4-queue jobs renew --task-id ID --claim-token TOKEN --json
scripts/v4-queue jobs preflight-result --result PATH --json
scripts/v4-queue jobs finalize --result PATH --json
scripts/v4-queue jobs audit-and-enqueue --json
scripts/v4-queue jobs recover-stale --json
scripts/v4-queue jobs retry --task-id ID --json
scripts/v4-queue jobs inspect --status open --model luna --json

scripts/v4-queue status --json
```

Exit behavior:

- `0`: success, including an empty queue;
- nonzero: invalid arguments, schema/invariant failure, lock timeout, stale token, conflict, or unrecoverable I/O failure;
- stdout: requested JSON result only when `--json` is used;
- stderr: concise diagnostics;
- no mutation on validation failure or empty selection. `jobs preflight-result`
  is explicitly read-only: it validates current claim ownership, result scope,
  normalized-job ownership/stable identities, and manual candidate accounting
  before any finalization transaction.

Use bounded lock timeouts and report which lock could not be obtained.

Every mutating command returns a structured operation report as part of its normal response. Skills must format that report for the operator rather than re-reading files, repeating research, or asking the model to reconstruct counts.

## 10. Repository skill behavior

Create `.agents/skills/validate-companies/SKILL.md` and `.agents/skills/find-jobs/SKILL.md`. They are the user-facing `$validate-companies` and `$find-jobs` workflows.

Each `--run MODEL` worker must repeat these steps until `queue_empty`:

1. Validate the model argument.
2. Call the internal claim command once per iteration.
3. Stop successfully if no eligible task exists.
4. Treat returned IDs as an absolute scope boundary.
5. Read only supplied context and additional evidence needed for those IDs.
6. Prefer deterministic adapters/checks before semantic research.
7. Renew before the lease deadline when necessary.
8. Produce the exact result schema at a unique path.
9. Run the read-only result preflight; repair that same result if it fails.
10. Run the finalizer only after preflight passes; never patch queue or canonical JSON itself.
11. Report task ID, outcome, canonical changes, follow-up task/model, and evidence summary.

`--audit-and-enqueue` performs only deterministic inspection and enqueueing. It does not claim or execute research.

Important limitation: the repository can enforce the model label requested in the command and match it to `expected_model`; it cannot reliably prove which model the operator selected in the Codex UI. The README must tell the operator to select Luna, Terra, or Sol before pasting the matching command.

## 11. Completion reports and full-system status

Reporting is an output layer over the queue and canonical database. It must not introduce a second state store, alternate mutation path, or agent-maintained summary.

### Per-command completion report

Every `$validate-companies --run MODEL` and `$find-jobs --run MODEL` worker ends with a concise aggregate report from its operation reports. It distinguishes execution success from research outcomes.

Present the most specific results first.

```text
Task result
  Command: completed
  Task: company:cmp_123:validate:policy_v1:luna:rev_7
  Model requested: luna
  Target: Example Company (cmp_123)
  Outcome: inconclusive
  Canonical changes: none
  Evidence: 3 sources checked; 2 supported findings; 1 conflict
  Follow-up: Terra task enqueued (task ID ...)

Item results
  [inconclusive] Company identity — conflicting official evidence
  [completed] Careers source — Ashby URL verified

Run summary
  Items resolved: 1
  Items inconclusive: 1
  Items blocked/failed: 0
  Queue delta: +1 Terra open, -1 Luna running
  Duration: 8m 14s

Current progress
  Company queue: Luna 81 open | Terra 32 open | Sol 13 open | 4 running
  Company coverage: 931 verified | 426 outside target | 1,553 not found | 126 pending
```

The exact fields vary by workflow, but the contract is fixed:

- execution status: `completed`, `no_task`, or `failed`;
- task ID, target IDs/names, requested model, attempt ID, and duration;
- one line per item the agent actually evaluated, with `resolved`, `unchanged`, `inconclusive`, `blocked`, or `failed`;
- evidence counts and only conclusions already collected during the task;
- canonical fields/records added, changed, or left unchanged;
- job-specific counts already known from the run: fetched, normalized, deterministically rejected, semantically reviewed, qualifying, and unresolved;
- final queue transition and any retry/escalation/human-review task created;
- warnings, conflicts, or incomplete-source flags;
- a small queue delta calculated by the finalizer;
- a final `Current progress` footer containing deterministic headline counts for the affected workflow.

For company work, the footer shows open/running queue counts by model and canonical company-status totals. For job work, it shows open/running queue counts by model, current/stale/missing source coverage, active qualifying jobs, and unresolved candidates for the active profile. The helper calculates these counts locally while it already has validated state; the agent does not spend tokens deriving them. Keep the footer to two or three lines. The separate `$daily-jobs-status` command remains the place for the comprehensive breakdown.

For job searches, finding zero qualifying jobs is not itself a failure. Report fetch completeness and filtering outcomes separately from qualifying-job count. For company validation, `not_found`, `outside_target`, and `inconclusive` must remain distinct.

No-task output is short and explicit:

```text
No open Luna company-validation task was available. No files changed.
```

The agent must not perform extra searches or scan the whole canonical database to prepare this report. The internal CLI emits a versioned `operation_report` JSON object, and the skill renders it. Persist the report's essential facts in the task attempt/resolution and audit event so the output can be reconstructed without storing prose as canonical state.

### Full-system status command

Add a third repository skill:

```text
$daily-jobs-status
```

It calls the read-only `scripts/v4-queue status --json` command and renders a deterministic summary. It performs no network requests, agent research, queue recovery, enqueueing, or writes. Its token cost should be limited to presenting the already-computed local result.

The default report contains:

1. **Snapshot identity** — generation time, schema versions, and revisions/content digests read.
2. **Queue inventory** — counts by company/job workflow, model, and status; running tasks split into active and lease-expired; due versus delayed open retries.
3. **Company coverage** — total companies grouped by the canonical statuses actually defined in the migrated schema, including at least verified/qualified, outside-target or disqualified, not-found, pending/unverified, inconclusive/human-review, and data-error where applicable.
4. **Career-source coverage** — totals grouped by ready, no-careers, unsupported, blocked, stale, and missing; structured adapter coverage by Ashby, Greenhouse, Lever, and other.
5. **Job-search coverage** — sources with current, stale, incomplete, failed, or missing snapshots; active qualifying job count for the current profile; unresolved semantic candidates. Do not imply historical trend data until V4 has actually accumulated it.
6. **Attention required** — blocked/failed tasks, Sol-inconclusive human reviews, expired leases, canonical conflicts, incomplete fetches, and data-integrity warnings.
7. **Recent activity** — deterministic counts of completed, inconclusive, blocked, and failed attempts over the last 24 hours and seven days, derived from persisted timestamps.

Suggested human-readable shape:

```text
V4 status — 2026-08-29 18:00 UTC

Queues
  Company validation  open 126 | running 4 | completed 18 | attention 7
    Luna 82 open | Terra 31 open | Sol 13 open
  Job finding         open 340 | running 6 | completed 52 | attention 11

Companies
  Total 3,036
  Verified 930 | Outside target 426 | Not found 1,553 | Pending 127

Career sources
  Ready 543 | No careers 178 | Unsupported 132 | Blocked 20 | Stale 5
  Structured: Ashby 191 | Greenhouse 135 | Lever 40

Job coverage
  Current 48 | Stale 3 | Incomplete 2 | Missing 490
  Active qualifying jobs 17 | Unresolved candidates 9

Needs attention
  Expired leases 2 | Blocked tasks 18 | Human review 4 | Data warnings 5

Recent activity
  Last 24h: 21 completed | 5 inconclusive | 2 blocked | 0 failed
```

Labels must come from an explicit mapping of canonical enum values. Never combine categories merely to make a cleaner-looking report; if V4 distinguishes `outside_target` from another form of disqualification, show both or document the exact roll-up.

The status reader should obtain a coherent snapshot using shared locks and the established global lock order, with a bounded timeout. It validates every input before counting. If a file is invalid or unavailable, it reports the affected section as unavailable and exits nonzero rather than presenting misleading partial totals. A successful status command is byte-for-byte read-only: file contents, revisions, timestamps, and lock-independent metadata do not change.

Support `--json` internally for tests and future tooling, but keep `$daily-jobs-status` as the only required operator command in V4. Optional filtering and historical analytics are deferred.

## 12. Deterministic versus agent work

### Deterministic company logic

- schema validation and ID/ownership checks;
- URL normalization and website-domain comparison;
- known Ashby, Greenhouse, and Lever source recognition;
- source URL canonicalization;
- duplicate company/domain detection;
- queue need/reason calculation;
- exact keyed application of an accepted result;
- known San Francisco boundary rules when structured data supports them.

The agent handles ambiguous company identity, conflicting evidence, website discovery that cannot be derived, and unsupported careers platforms.

### Deterministic job logic

- fetch and parse Ashby, Greenhouse, and Lever structured feeds;
- stable source and job IDs;
- pagination/completeness checks;
- normalization, deduplication, and snapshot comparisons;
- exact profile predicates for known structured fields;
- fail-safe disappearance handling: an incomplete fetch must never mark prior jobs closed;
- compensation normalization only when source units/currency/period are explicit.

The canonical snapshot `jobs` array is the authoritative qualifying set. The finalizer assigns every selected record a `qualification_status` (`qualifying` for semantic selections and `deterministic_pass` for deterministic string selections), and the snapshot schema requires that provenance marker. Status counts active records in this qualifying-only array rather than trusting an optional agent annotation.

The agent handles semantic title/role relevance, job-specific AI involvement, unclear workplace language, ambiguous compensation, and unsupported sources.

Implement deterministic handling in the first build, but only for these high-confidence rules. Do not recreate the whole V3 agent process and bolt determinism on afterward.

## 13. Audit-and-enqueue rules

Company audit walks canonical records in stable company-ID order and consolidates all applicable reason codes into one task per company. Initial reasons include:

- company validation is pending or inconclusive;
- verified company has no canonical website;
- website/source relationship needs validation;
- career source is missing, stale, blocked, or unsupported;
- possible duplicate canonical website domain;
- prior preserved research needs reconciliation.

It creates a Luna task unless an equivalent active/terminal task and unchanged target revision make new work unnecessary. Existing unresolved model history determines whether the next valid model is Luna, Terra, Sol, or human review.

Job audit walks eligible sources in stable source-ID order. Initial reasons include:

- ready source has no current snapshot;
- snapshot is stale under the configured policy;
- previous fetch failed or was incomplete;
- search profile version changed;
- deterministic results contain semantic candidates needing agent review.

Start with structured sources and then enqueue supported non-structured sources. A job task is scoped to one source and one profile version.

Dedupe is based on workflow, exact target, reason/policy version, expected model, and target revision. At most one nonterminal task may exist for a dedupe key. Repeated audits with unchanged data must make zero writes.

## 14. V3 migration specification

Migration is a deterministic, repeatable command that reads V3 and writes only V4. It must create a manifest with input paths, content digests, counts, transformation version, warnings, and output digests.

Use the dirty V3 working tree as the source of truth, not only Git `HEAD`.

Start from the V3 canonical company/source data and apply these five clean, unapplied company handoffs in timestamp order to a migration copy using the V3 applier semantics:

1. `imports/20260810T035302000Z-company-ingest-builtin-p005-p005.json`
2. `imports/20260810T035319000Z-company-ingest-builtin-p005-p005.json`
3. `imports/20260810T035353000Z-company-ingest-builtin-p006-p006.json`
4. `imports/20260815T202742000Z-company-ingest.json`
5. `imports/20260828T214645Z-company-validate.json`

Expected checkpoint before the V4 audit:

- 3,036 companies total;
- 930 verified;
- 426 outside target;
- 1,553 not found;
- 127 pending;
- 878 career sources inherited from the V3 baseline unless the handoffs change that count;
- no duplicate company IDs, duplicate source IDs, or orphan sources.

The five handoffs are expected to add 18 companies, fill four websites, and apply 75 validations: 30 verified, 12 outside target, and 33 not found. Treat any mismatch as a migration failure requiring investigation, not a count to force.

Preserve:

- all canonical company and career-source data;
- 1,446 non-job issue/evidence records involving 1,365 company IDs as a queryable V4 research archive;
- `config/search-profiles/ai-product-frontend-v1.json` exactly, followed by schema validation and an explicit V4 copy;
- provenance linking migrated fields to V3 inputs.

Do not migrate old job scan results, job handoffs, generated job reports, or transient job queues. Archive the temporary no-AI profile rather than making it active.

Known migration checks:

- investigate and preserve, rather than silently merge, the five duplicate website-domain groups;
- current baseline source statuses are expected near: 543 ready, 178 no-careers, 132 unsupported, 20 blocked, and 5 stale;
- current ready structured adapters are expected near: Ashby 191, Greenhouse 135, Lever 40; verify counts from migrated data.

## 15. One-shot implementation sequence

The implementing agent should follow this order and keep the repository runnable after every phase.

### Phase 0 — Inspect and protect inputs

1. Read this plan, all V3 command prompts, schemas, `scripts/apply_handoff.py`, tests, canonical JSON, profile, and relevant handoffs.
2. Record `git status` for V3 and V4. Do not clean, reset, or modify V3.
3. Confirm V4 is the intended target and `/Users/natelipp/Documents/ChatGPT/v4` is not an implementation target.
4. Write discovery/count scripts as tests or migration utilities, not one-off undocumented mutations.

Gate: documented source paths and independently reproduced baseline/migrated counts.

### Phase 1 — Scaffold contracts

1. Create the package, schemas, empty canonical documents, queue documents, README, AGENTS instructions, and skill directories.
2. Define enums, result reason codes, error classes, timestamp/ID normalization, and schema loading.
3. Add fixtures representing every task state and outcome.

Gate: all empty documents and fixtures validate; imports and CLI help work without network access.

### Phase 2 — Safe JSON storage

1. Implement separate lock files with bounded `flock` acquisition.
2. Implement locked reload, schema validation, atomic same-directory save, and directory sync.
3. Implement revision checking, deterministic formatting, file digesting, and no-op detection.
4. Implement transaction journal creation and recovery primitives.

Gate: multiprocessing tests prove no lost updates; injected crashes at each save phase recover to a valid state.

### Phase 3 — Queue engine

1. Implement enqueue/dedupe, deterministic claim-next, renewal, final state transitions, retry scheduling, inspect, and explicit stale recovery.
2. Implement claim tokens, leases, attempt history, parent/root lineage, and model escalation.
3. Enforce transition table, exact target cardinality, and one active dedupe key.

Gate: 20+ concurrent claimers receive distinct task IDs; empty claims do not change file bytes or revision; stale tokens cannot renew or finalize.

### Phase 4 — Canonical finalizer

1. Define allow-listed company and job patches.
2. Implement queue-then-canonical lock ordering and journaled finalization.
3. Add target revision/precondition checks and canonical application ledger.
4. Implement outcome classification, same-model retry rules, Luna/Terra/Sol escalation, and Sol human review.

Gate: repeated finalize is a no-op; recovered old claims cannot write; conflicting canonical updates are rejected and re-auditable; crash injection cannot leave ambiguous ownership.

### Phase 5 — V3 migration

1. Implement read-only V3 loader and transformations.
2. Apply the five clean handoffs to an in-memory or temporary migration copy.
3. Preserve research archive and profile; omit old job scans.
4. Write V4 canonical data and manifest only after all expected invariants pass.
5. Run migration twice into clean temporary destinations and compare content digests.

Gate: expected checkpoints pass and repeat migrations are byte-identical except explicitly excluded run timestamps; preferably derive timestamps from source data so all outputs are identical.

### Phase 6 — Company audit and skill

1. Implement deterministic company audit reasons and consolidation.
2. Implement company result schema and patch allow-list.
3. Create `$validate-companies` skill for `--run`, diagnostic `--claim-next`, `--audit-and-enqueue`, and `--recover-stale`.
4. Port only the useful research guidance from V3's separate company commands into this single skill.

Gate: audit twice yields no new tasks/revision on the second run; concurrent claims are disjoint; representative resolved/inconclusive/access-failure paths finalize correctly.

### Phase 7 — Structured job adapters and deterministic filters

1. Implement Ashby, Greenhouse, and Lever adapters with recorded fixtures before live tests.
2. Prove pagination/completeness and stable job identity.
3. Port the primary profile exactly and implement deterministic predicates for structured facts.
4. Preserve unknowns for semantic review; do not turn missing facts into exclusions unless the profile explicitly says so.
5. Implement safe snapshot application and disappearance rules.

Gate: fixture tests cover paging, duplicate posts, changed posts, partial failure, missing compensation, workplace ambiguity, and unchanged reruns.

### Phase 8 — Job audit and skill

1. Implement job audit/dedupe for one-source tasks.
2. Define the self-contained claim context: company, source, last snapshot metadata, prior attempts, profile version, and deterministic candidate/rejection details.
3. Create `$find-jobs` skill with the same claim/finalize discipline.

Gate: multiple same-model agents claim different sources; a repeated source result is idempotent; access failure does not escalate; semantic inconclusive follows the model ladder.

### Phase 9 — Reporting and status skill

1. Define and version the `operation_report` schema returned by claim, finalize, audit, recovery, retry, and empty-queue operations.
2. Make both work skills render item results first and the run-level summary last using only the CLI response and evidence already gathered.
3. Implement the read-only status aggregator with explicit canonical-status label mappings and stable ordering.
4. Create `.agents/skills/daily-jobs-status/SKILL.md` and document `$daily-jobs-status`.
5. Append one compact, schema-validated event for every queue-helper operation to a locked daily JSONL file. Reduce structured reports to an explicit allowlist so claim tokens, prompts, evidence, source instructions, and job descriptions can never enter telemetry.
6. Add a read-only `v4-queue telemetry` command with time, workflow, worker, and tail filters plus deterministic aggregate counts. Telemetry is diagnostic history only and never feeds queue or canonical decisions.
7. Add golden-output fixtures for company resolution, job search with zero matches, inconclusive escalation, access block, failed finalization, empty queue, full status, and telemetry aggregation.

Gate: completion reports require no extra research or full-data scan; status totals reconcile to their source records; repeated status and telemetry-summary runs make no file changes; concurrent telemetry appends produce complete schema-valid lines; invalid input cannot produce a deceptively successful partial report.

### Phase 10 — End-to-end rehearsal

1. Run migration in a clean V4 test copy.
2. Run both audits twice.
3. Spawn local concurrent test processes for Luna/Terra/Sol claims across both queues.
4. Exercise resolved, inconclusive, blocked, failed, retry, expired lease, stale recovery, stale token, stale canonical revision, process crash, and empty queue cases.
5. Run representative live adapter smoke tests separately from the deterministic offline suite.
6. Verify no test or skill directly writes queue/canonical files outside the shared library.

Gate: all acceptance criteria below pass, README commands match actual behavior, and the working tree contains no runtime locks, secrets, or accidental V3 job results.

## 16. Required test matrix

At minimum, automate:

- JSON schema acceptance/rejection for every document;
- deterministic IDs, ordering, formatting, and audit output;
- concurrent enqueue dedupe;
- concurrent same-model and mixed-model claims;
- exact one-target task scope;
- empty queue byte-for-byte no-op;
- lease renewal and explicit recovery;
- rejected expired, recovered, wrong-model, and wrong-target tokens;
- every allowed and forbidden state transition;
- Luna to Terra and Terra to Sol child creation exactly once;
- Sol human-review terminal behavior;
- same-model retry/backoff for each access-error class;
- maximum retry to blocked;
- idempotent result and finalization replay;
- canonical revision conflict;
- atomic-write interruption and journal recovery at every phase;
- canonical and queue schema failure before save;
- adapter pagination and partial/incomplete fetch safety;
- job/profile deterministic rules and preserved unknowns;
- reproducible V3 migration and expected checkpoints;
- audit repeatability and no-op revision behavior;
- versioned operation-report schema for success, no-task, inconclusive, blocked, and failure outcomes;
- per-item report totals reconciling with the result envelope and canonical patch;
- queue deltas reconciling with the committed queue transaction;
- full-status category totals reconciling with canonical and queue records;
- active versus expired running-task counts and due versus delayed retry counts;
- status command byte-for-byte read-only behavior;
- deterministic status ordering and golden human-readable output;
- invalid status inputs producing an explicit nonzero incomplete/unavailable result;
- static/search test that direct `open(..., 'w')`, `os.replace`, or queue mutation is confined to approved storage modules.

## 17. Acceptance criteria

The implementation is complete only when:

1. The six commands at the top are sufficient for routine work.
2. Twenty concurrent workers using the same model command cannot claim the same task.
3. A worker whose task was recovered cannot commit with its old token.
4. Queue JSON remains readable and explains task target, history, evidence, model, state, and follow-up lineage.
5. No normal instructions ask a human or agent to edit queue/canonical JSON.
6. Audits enqueue work deterministically and a second unchanged audit is a byte-for-byte no-op.
7. Result finalization is idempotent across retries and simulated crashes.
8. Model escalation and access-error behavior match this plan exactly.
9. V3 company/source data and research archive are accounted for by manifest; old job runs are absent.
10. The primary search profile is preserved and its deterministic rules have tests.
11. All offline tests pass from one documented command.
12. README contains the fresh-install, migration, audit, work, recovery, inspection, and troubleshooting runbook.
13. Every completed work command reports per-item outcomes first and a concise run summary last without additional research.
14. `$daily-jobs-status` reports queue, company, source, job coverage, attention, and recent-activity totals without mutating any data.
15. Every queue-helper operation emits compact secret-free telemetry, and the local telemetry summary remains read-only and independent of canonical workflow state.

## 18. Operator runbook to document in README

Fresh setup:

```text
cd /Users/natelipp/ai-code/_v4_daily-jobs
<create environment and install the package using the actual pyproject commands>
<run the read-only V3 migration command>
<run the complete offline test command>
$validate-companies --audit-and-enqueue
$find-jobs --audit-and-enqueue
```

Daily work:

```text
$validate-companies --run luna
$validate-companies --run terra
$validate-companies --run sol

$find-jobs --run luna --lane structured|manual
$find-jobs --run terra --lane structured|manual
$find-jobs --run sol --lane structured|manual
```

Overall progress:

```text
$daily-jobs-status
```

Recovery and inspection:

```text
$validate-companies --recover-stale
$find-jobs --recover-stale
<document internal inspect commands and how to search queue JSON for statuses in Vim>
```

Do not put placeholder commands in the finished README. Replace angle-bracketed lines with exact commands verified against the implemented package.

## 19. Instructions to the one-shot implementing agent

- Implement the complete plan in `/Users/natelipp/ai-code/_v4_daily-jobs`; do not implement in `/Users/natelipp/Documents/ChatGPT/v4`.
- Do not delete the extra project folder or mutate V3. Folder removal from the Codex project UI is a separate user action.
- Preserve unrelated or dirty V3 changes.
- Prefer Python standard library plus `jsonschema` and a test runner; justify every additional runtime dependency.
- Use recorded HTTP fixtures for normal tests. Live network checks are opt-in.
- Make no silent corrections to migration data; record warnings and provenance.
- Keep shared mutation logic in one place. Skills, audits, adapters, and tests must call it rather than implement alternate writes.
- After each phase, run its gate tests and fix failures before continuing.
- At completion, report exact migrated counts, queued counts by workflow/model/status, test totals, live checks performed, known unsupported sources, reporting/status examples, and any deviations from this plan.
