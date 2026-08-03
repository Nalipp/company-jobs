# Daily Jobs

Daily Jobs is a file-backed agent workflow for defining a reusable job-search
profile and finding current openings against it.

## Current state

This repository contains the agent specification and data contracts. It does not
yet contain a standalone application or scheduler.

## Workflow

1. `/help` displays all available commands and short summaries.
2. `/init` interviews the user and saves approved rules to
   `profiles/default.json`.
3. `/show` displays the saved profile.
4. `/edit` changes selected rules and asks for approval.
5. `/fetch` collects, normalizes, verifies, filters, and ranks current jobs, then
   saves the evidence-backed result under `runs/`.
6. `/history` reads prior run files.

Natural-language edits are accepted even without command syntax.

## Repository layout

- `AGENTS.md`: minimal durable context for new project agents
- `init-prompt.md`: governing agent instructions
- `schemas/profile.schema.json`: contract for persisted user preferences
- `schemas/search-run.schema.json`: contract for results and source evidence
- `profiles/default.example.json`: starter profile illustrating the data format
- `runs/`: generated search history

## Design principles

- Files, not chat memory, are the source of truth.
- Hard constraints determine eligibility; preferences determine rank.
- Unknown salary remains eligible unless published salary is explicitly required.
- Employer and ATS pages are canonical; aggregators are discovery aids.
- Material result claims retain source evidence and a verification timestamp.
