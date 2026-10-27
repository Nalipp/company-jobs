# Daily Jobs

Daily Jobs is a file-backed agent workflow for defining a reusable job-search
profile and finding current openings against it.

## Current state

This repository contains the agent specification and data contracts. It does not
yet contain a standalone application or scheduler.

The slash commands are agent workflows defined in `init-prompt.md`, supported by
dry-run-first local helpers. A new agent should read `AGENTS.md`, `init-prompt.md`,
and `RUNBOOK.md`, then load the approved profile and persisted files named by the
requested workflow.

A file-backed company catalog is active under `catalog/companies/`. The initial
migration completed on 2026-08-05; its report is `catalog/migration-report.json`.
The pre-migration workflow is preserved with checksums under
`legacy-old/2026-08-05-pre-catalog/`.

Both controlled trials completed on 2026-08-05. The Ampersand known-company
refresh is recorded in `discoveries/2026-08-05.json` and `runs/2026-08-05.json`.
The limited catalog-expansion trial is recorded in
`discoveries/2026-08-05-2.json` and `runs/2026-08-05-2.json`; it admitted two
qualifying Edison Scientific jobs, excluded two nonqualifying leads, and produced
zero queued candidates when rerun against persisted memory.

The catalog is limited to companies with at least one job that passes the
posting-level hard constraints. Exact address and coordinate validation occur later
and do not block initial admission when the official posting explicitly identifies
the approved city.

## Workflow

1. `/help` displays all available commands and short summaries.
2. `/daily-jobs-init` interviews the user and saves approved rules to
   `profiles/default.json`.
3. `/show` displays the saved profile.
4. `/edit` changes selected rules and asks for approval.
5. `/companies` lists cataloged companies or shows one company in detail.
6. `/refresh` polls known catalog companies and queues new or changed jobs.
7. `/discover` searches broadly for jobs and companies outside the catalog.
8. `/verify` applies the hard gate to a bounded batch and admits passing records.
9. `/fetch` refreshes known companies and verifies one batch; `--expand` uses broad
   discovery instead.
10. `/validate-addresses` optionally validates one catalog company's SF address.
11. `/history` reads prior discovery, refresh, and verification files.

Natural-language edits are accepted even without command syntax.

## Repository layout

- `AGENTS.md`: minimal durable context for new project agents
- `init-prompt.md`: governing agent instructions
- `RUNBOOK.md`: exact dry-run, write, validation, and recovery procedure
- `schemas/profile.schema.json`: contract for persisted user preferences
- `schemas/discovery-run.schema.json`: contract for broad-discovery and
  known-company refresh checkpoints
- `schemas/employer-source.schema.json`: legacy employer-source contract retained
  for validating archived data
- `schemas/company-catalog.schema.json`: contract for persistent per-company memory
- `schemas/candidate-memory.schema.json`: compact cross-run identity/hash/outcome cache
- `schemas/expansion-input.schema.json`: normalized input for agent-led broad discovery
- `schemas/job-admission.schema.json`: verified job/company admission contract
- `schemas/search-run.schema.json`: contract for results and source evidence;
  version 1 validates historical runs and new runs use version 2
- `profiles/default.example.json`: starter profile illustrating the data format
- `product-notes/`: non-operational product ideas and supporting research notes
- `discoveries/`: generated discovery/refresh checkpoints and verification queues
- `catalog/companies/`: active per-company job, source, office, and evaluation memory
- `catalog/candidate-memory.json`: lightweight memory for avoiding unchanged work
- `catalog/migration-report.json`: migration counts, exclusions, and limitations
- `legacy-old/`: immutable rollback snapshots of earlier workflows
- `runs/`: generated search history
- `scripts/daily-jobs.mjs`: dry-run-first refresh, expansion, admission, and memory helper
- `test/`: offline provider and workflow fixtures/tests

## Design principles

- Files, not chat memory, are the source of truth.
- Hard constraints determine eligibility; preferences determine rank.
- Role discovery uses approved engineering families plus role-specific evidence;
  an exact AI title is a ranking boost rather than a default eligibility gate.
- An AI-focused employer alone does not establish that a role includes AI work.
- Culture fit is evidence-based; missing signals remain unknown rather than negative.
- Compensation follows an explicit comparison rule and unknown-salary policy.
- Job boards and web search provide broad discovery; employer and ATS pages are
  canonical during verification.
- The first discovery backfills active jobs for 60 days; later runs search for
  additions and changes since the previous completed discovery.
- Unknown posting dates remain eligible when the canonical job is confirmed active.
- Promising unresolved candidates remain `needs_review` instead of being discarded.
- Material result claims retain source evidence and a verification timestamp.
- Verification keeps matching roles even when a street address is unavailable.
- Map publication requires either a verified exact worksite or a documented company
  office proxy in the job's confirmed city. Address validation is optional and
  independently rerunnable.
- Address collection is scoped to approved job cities. The workflow does not try
  to catalog or geocode every office operated by a matching company.
