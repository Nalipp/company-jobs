# Daily Jobs

Daily Jobs is a file-backed agent workflow for defining a reusable job-search
profile and finding current openings against it.

## Current state

This repository contains the agent specification and data contracts. It does not
yet contain a standalone application or scheduler.

## Workflow

1. `/help` displays all available commands and short summaries.
2. `/daily-jobs-init` interviews the user and saves approved rules to
   `profiles/default.json`.
3. `/show` displays the saved profile.
4. `/edit` changes selected rules and asks for approval.
5. `/discover` runs a Google-first 60-day backfill initially, then incremental
   searches, and saves a deduplicated checkpoint under `discoveries/`.
6. `/verify` verifies one bounded candidate batch and saves results under `runs/`.
7. `/fetch` runs discovery followed by one verification batch.
8. `/validate-addresses` optionally validates a run's worksite addresses and
   coordinates for map use.
9. `/history` reads prior discovery and verification files.

Natural-language edits are accepted even without command syntax.

## Repository layout

- `AGENTS.md`: minimal durable context for new project agents
- `init-prompt.md`: governing agent instructions
- `schemas/profile.schema.json`: contract for persisted user preferences
- `schemas/discovery-run.schema.json`: contract for broad candidate checkpoints
- `schemas/employer-source.schema.json`: contract for reusable employer sources
- `schemas/search-run.schema.json`: contract for results and source evidence
- `profiles/default.example.json`: starter profile illustrating the data format
- `discoveries/`: generated discovery checkpoints and verification queues
- `sources/employers.json`: growing registry of verified career and ATS boards
- `runs/`: generated search history

## Design principles

- Files, not chat memory, are the source of truth.
- Hard constraints determine eligibility; preferences determine rank.
- Role discovery uses approved engineering families plus role-specific evidence;
  an exact AI title is a ranking boost rather than a default eligibility gate.
- An AI-focused employer alone does not establish that a role includes AI work.
- Culture fit is evidence-based; missing signals remain unknown rather than negative.
- Unknown salary remains eligible unless published salary is explicitly required.
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
