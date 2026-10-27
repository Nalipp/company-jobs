# Daily Jobs operational runbook

This runbook turns the agent behavior in `init-prompt.md` into repeatable file and
command operations. `init-prompt.md` remains the authority for product rules; this
file explains how to execute them safely.

## Before every run

1. Read `AGENTS.md`, `init-prompt.md`, and `profiles/default.json`.
2. Confirm that the profile validates and has `status: approved`.
3. Run `npm install` if dependencies are absent, then run `npm run check`.
4. Inspect `git status --short`. Preserve unrelated user changes.
5. Use an explicit ISO timestamp with `--now` when reproducibility matters.

The workflow CLI is dry-run by default. Always inspect a dry run before repeating
the same command with `--write`. A write uses atomic replacement for each JSON file,
but a command may update several files, so do not interrupt it deliberately.

## Known-company refresh (`/refresh`)

Dry-run all catalog companies:

```sh
node scripts/daily-jobs.mjs refresh
```

Dry-run one company:

```sh
node scripts/daily-jobs.mjs refresh --company ampersand
```

After reviewing the summary, repeat with `--write`. A successful write may update
company source/refresh timestamps, job activity counters, `catalog/candidate-memory.json`,
and one version-2 checkpoint under `discoveries/`.

The structured collectors support Ashby, Greenhouse, and Lever. Custom, Workday,
and unknown sources are reported as unsupported; they are never interpreted as an
empty feed, so their jobs cannot be marked missing by that result. Resolve those
sources manually during verification or add a reviewed collector later.

Refresh queues only:

- new plausible jobs;
- materially changed remembered jobs; and
- migrated `evidence_only` jobs that need a full-description backfill.

Unchanged full jobs receive timestamp/activity maintenance without model analysis.
One successful absence increments `missing_from_source_count`; two consecutive
successful absences mark a job inactive. Failed, partial, or unsupported polls do
not increment it.

## Catalog expansion (`/discover`)

Broad web and job-board search remains an agent task because it uses the current
profile's query matrix and accessible sources. Save normalized leads using
`examples/expansion-input.example.json` as the template, then dry-run:

```sh
node scripts/daily-jobs.mjs expand --input /absolute/path/to/expansion-input.json
```

Review the counts and repeat with `--write`. The helper deduplicates by ATS identity,
canonical URL, and deterministic fallback fingerprint; skips unchanged catalog jobs
and unchanged remembered outcomes; applies cheap hard filters; and writes a bounded
`catalog_expansion` checkpoint. It does not create company files.

Use `--retry-review` only when the user or `/verify --retry-review` explicitly wants
unchanged `needs_review` leads queued again.

## Verification and admission (`/verify`)

1. Open the newest or named version-2 checkpoint and select at most the profile's
   `verification_batch_limit` queued candidates.
2. Verify each candidate against the canonical employer or ATS posting using all
   rules in `init-prompt.md`.
3. Save a schema-version-2 run under `runs/`; do not add run-level address fields.
4. Set each processed discovery candidate to `verified`, `excluded`,
   `needs_review`, or `error`, including reason and timestamp.
5. For every included job, prepare an admission file from
   `examples/job-admission.example.json`. The evaluation must explicitly contain:
   `active`, `acceptable_seniority`, `role_specific_ai_work`, and
   `all_profile_hard_constraints_passed` along with specific evidence.
6. Dry-run and then write each passing admission:

```sh
node scripts/daily-jobs.mjs admit --input /absolute/path/to/admission.json
node scripts/daily-jobs.mjs admit --input /absolute/path/to/admission.json --write
```

The admission helper recomputes hashes, enforces deterministic location, title,
workplace, employment, and compensation rules, requires the explicit semantic
verification decisions above, preserves existing office data, and creates a new
company only for a passing job.

Preserve source-normalized factual values in the admission `job` object. In
particular, leave `seniority` as `null` when the collector supplied no structured
level; record an inferred acceptable level in the verification run and evaluation
evidence instead. Mixing inferred values into raw metadata causes false change
detection on the next refresh.

After the discovery statuses and run are safely saved, synchronize lightweight
outcome memory:

```sh
node scripts/daily-jobs.mjs sync-memory --discovery /absolute/path/to/discovery.json
node scripts/daily-jobs.mjs sync-memory --discovery /absolute/path/to/discovery.json --write
```

Finish with `npm run check`.

## Candidate memory

`catalog/candidate-memory.json` stores only identifiers, canonical URLs, hashes,
profile/outcome state, reason codes, source names, and timestamps. It deliberately
does not store descriptions. Same-profile records with unchanged hashes can be
skipped; a changed profile or changed hashes causes reconsideration.

If memory is missing or suspected to be inconsistent, rebuild it deterministically
from discoveries and admitted catalog jobs:

```sh
node scripts/daily-jobs.mjs rebuild-memory
node scripts/daily-jobs.mjs rebuild-memory --write
```

## Address validation

`/validate-addresses` remains a separate evidence-gathering workflow. It updates
only the relevant company office and never a discovery or verification run. Follow
the evidence and geocoding rules in `init-prompt.md`, then validate the company file
and run `npm run check`.

## Recovery and resume

- A collection checkpoint is the handoff boundary between collection and
  verification. If interrupted after collection, resume from that file.
- Never infer missing jobs from an incomplete source record.
- If a write command fails, run `npm run validate`, inspect the files named in its
  output and `git diff`, and rerun only after the state is understood.
- The immutable pre-catalog rollback remains under
  `legacy-old/2026-08-05-pre-catalog/`; normal operations never edit it.

## Command reference

```text
npm test                         Offline unit and workflow tests.
npm run validate                 Schema and cross-file validation.
npm run check                    Tests followed by validation.
node scripts/daily-jobs.mjs help Local workflow-helper usage.
```
