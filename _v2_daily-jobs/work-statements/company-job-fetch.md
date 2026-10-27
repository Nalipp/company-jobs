# Work Statement: Company Job Fetch

## Role and boundary

Act as the external research agent for Daily Jobs v2. Poll saved company sources,
propose only qualifying unseen jobs, and produce one idempotent JSON handoff. Do
not edit `data/companies.json`.

Read completely before starting:

1. `config/search.json`
2. `data/companies.json`
3. `docs/company-job-fetch.md`
4. `docs/source-policy.md`
5. `docs/data-contract.md`
6. `docs/handoff-protocol.md`
7. `schemas/search.schema.json`
8. `schemas/companies.schema.json`
9. `schemas/handoff.schema.json`
10. `examples/company-job-fetch.example.json`

The example is fictional and supplies shape only.

## Objective

Process every saved company unless command arguments explicitly narrow a
diagnostic run. Poll only its saved canonical jobs source. Do not discover unknown
companies.

Use a source-provided job ID when available. Otherwise require a stable,
job-specific canonical URL. Never create identity from title, company, location,
date, description, salary, model judgment, or a hash.

Ignore known postings without emitting or changing them. Put a posting with no
stable identity, a shared generic URL, conflicting identity, or an unavailable
source in `issues` and continue. Apply every saved hard constraint only to unseen
jobs, and propose only active jobs whose canonical source supports every gate. Do
not persist rejected jobs, known jobs, empty per-company results, or board
snapshots.

Each change contains `company_id` and a non-empty `new_jobs` array. If a company
has no new qualifying job, emit no change for it.

## Required output

Always produce JSON conforming to `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "company-job-fetch",
  "created_at": "ISO-8601 timestamp",
  "changes": [],
  "issues": []
}
```

When filesystem access exists, write JSON only to a new file named:

```text
/Users/natelipp/ai-code/_v2_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-company-job-fetch.json
```

Use UTC and never overwrite an existing file; add `-2`, `-3`, and so on if needed.
Return the exact absolute path after writing. Do not edit the catalog.

When filesystem access does not exist, return exactly one fenced JSON block and no
other prose. If there are no additions or problems, return the valid empty
envelope.
