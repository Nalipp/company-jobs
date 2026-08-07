# Daily Jobs v2

Daily Jobs v2 uses an LLM for web research and a small deterministic Python
importer for state changes. This keeps all three commands replay-safe without a
database, run ledger, hashes, or generated identities.

## The three commands

Run these as project slash commands in Claude Code:

```text
cd /Users/natelipp/ai-code/_v2_daily-jobs
claude

/company-discover
/validate-company-address
/company-job-fetch
```

Claude Code must be launched from this repository so it can load `CLAUDE.md`, the
project skills, current catalog, and schemas. A normal web chat cannot write to
this local path; use the documented JSON-block fallback in that case.

The equivalent repo-local Codex skills are `$company-discover`,
`$validate-company-address`, and `$company-job-fetch`.

- `company-discover` finds previously unknown companies through active jobs that
  pass every saved constraint.
- `validate-company-address` locates the next 10 pending companies by default.
- `company-job-fetch` polls known company sources and emits only unseen qualifying
  jobs.

Each command always produces the same five-field JSON envelope. Empty `changes`
means there is nothing to apply. `issues` contains problems that need attention
but are never written into the catalog.

## Safe two-phase workflow

The research agent reads current state and writes a handoff under the absolute
directory:

```text
/Users/natelipp/ai-code/_v2_daily-jobs/imports/
```

It never edits `data/companies.json`. Apply its output from the repository root:

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/apply_handoff.py /absolute/path/to/handoff.json
```

The importer validates the artifact, reloads the latest catalog, deduplicates or
checks the expected state, and performs an atomic write only when needed. Applying
the same file again is a no-op. Research runs may happen in parallel, and their
handoffs may finish or be applied in any order.

If an agent cannot write to the repository, it returns exactly one JSON code block.
Save that block under `imports/` and pass its path to the same importer.

## Identity rules

Companies deduplicate by ATS provider plus board ID, then primary domain, then
normalized jobs URL. Similar names are not identity.

Jobs use a source-provided ID. If none exists, a stable, job-specific canonical URL
is sufficient. A posting without either is reported in `issues`; the system does
not create a fallback ID.

## Repository layout

- `.claude/skills/`: Claude Code slash commands
- `.agents/skills/`: repo-local Codex skills
- `config/search.json`: active hard constraints
- `data/companies.json`: canonical catalog
- `docs/handoff-protocol.md`: shared handoff and import behavior
- `docs/`: operation, source, architecture, and data rules
- `work-statements/`: self-contained external-agent instructions
- `schemas/handoff.schema.json`: shared operation-aware handoff schema
- `schemas/companies.schema.json`: canonical company and job schema
- `examples/`: fictional valid handoffs
- `scripts/apply_handoff.py`: deterministic importer
- `imports/`: generated handoffs

The first version deliberately has no scheduler, database, candidate memory,
content hashing, board snapshots, or inactive-job lifecycle.
