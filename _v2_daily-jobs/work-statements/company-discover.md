# Work Statement: Company Discover

## Role and boundary

Act as the external research agent for Daily Jobs v2. Find new companies through
current qualifying jobs and produce one idempotent JSON handoff. Do not edit
`data/companies.json`.

Read completely before starting:

1. `config/search.json`
2. `data/companies.json`
3. `docs/company-discover.md`
4. `docs/source-policy.md`
5. `docs/data-contract.md`
6. `docs/handoff-protocol.md`
7. `schemas/search.schema.json`
8. `schemas/companies.schema.json`
9. `schemas/handoff.schema.json`
10. `examples/company-discovery.example.json`

The example is fictional and supplies shape only.

## Research budget and checkpoints

Keep each invocation bounded. Stop after 12 web searches, 30 canonical fetches, or
3 newly qualifying companies, whichever comes first. Write the first valid
checkpoint before the context becomes large: after each 2 qualifying companies or
8 search/fetch actions, whichever comes first.

Each checkpoint is a normal `company-discover` handoff containing only the new
companies and issues found so far. Save it as a unique file with
`-part-01`, `-part-02`, and so on before `.json`; never overwrite it. The receiving
agent can apply each part independently and safely. If the budget is reached before
the configured target, stop and report `partial`; a later invocation can continue
from the updated catalog.

## Objective

Find up to the configured target number of companies not already represented by
an ATS provider and board ID, primary domain, or normalized jobs URL. A company
qualifies only when an accessible canonical employer, ATS, or qualifying primary
employer-authored platform posting proves that at least one active job passes every
hard constraint.

Use varied title, responsibility, job-board, and ATS queries. Treat search engines
and third-party boards only as lead generators. Omit a role if a required fact is
missing, ambiguous, failing, or supported only by a third party. Employer AI focus
alone is not role-specific AI work.

After the first role qualifies, inspect the official board and include all other
current roles that independently pass every gate. Preserve exact salary text and
concise evidence. Do not include rejected or unresolved leads, descriptions, raw
HTML, scores, or reasoning.

Each change is a complete new company object. Initialize the configured city as a
pending map location and leave every address-result field null. Put unavailable
sources or ambiguous company identity in `issues`, not `changes`.

## Required output

Always produce JSON conforming to `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "company-discover",
  "created_at": "ISO-8601 timestamp",
  "changes": [],
  "issues": []
}
```

When filesystem access exists, write JSON only to a new file named:

```text
/Users/natelipp/ai-code/_v2_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-company-discover.json
```

Use UTC and never overwrite an existing file; add `-2`, `-3`, and so on if needed.
Return the exact absolute path after writing. Do not edit the catalog.

When filesystem access does not exist, return exactly one fenced JSON block and no
other prose. If nothing qualifies, still return the valid envelope with empty
`changes`; record only genuine problems in `issues`.

## Completion response

When files were written, respond after the JSON work with:

1. `Status: complete`, `Status: partial`, or `Status: no-op`.
2. A brief summary of searches performed, companies found, qualifying jobs, and
   issues.
3. The exact absolute path of every checkpoint or final handoff file.
4. A `Next step` containing literal copy-pastable commands, with no placeholders:

```text
cd /Users/natelipp/ai-code/_v2_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v2_daily-jobs/imports/<exact-file>.json"
```

For multiple files, provide one literal importer command per file. If no handoff
file was written, say so explicitly and do not invent a command.
