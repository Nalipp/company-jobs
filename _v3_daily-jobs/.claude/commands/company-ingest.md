---
description: Ingest supplied technology-company sources into the canonical catalog
argument-hint: <URL, file path, pasted data, or collection instructions> [limit N]
---

# Company ingest

Ingest technology companies from the source or instructions in `$ARGUMENTS`.

Keep this operation narrow: collect company records, deduplicate them, and write a handoff. Do not research jobs, job constraints, ATS providers, or company addresses that were not supplied by the source.

## Safety and inputs

1. Confirm the working directory is exactly `/Users/natelipp/ai-code/_v3_daily-jobs`. Stop with a clear correction if it is not.
2. Read `AGENTS.md`, `schemas/companies.schema.json`, `schemas/handoff.schema.json`, and `data/companies.json` before doing work.
3. `$ARGUMENTS` must contain a URL, file path, pasted data, or clear collection instructions. If no useful input was supplied, ask for it and do nothing else.
4. Use a default maximum of 50 source companies in one run. Honor an explicit limit from 1 through 200.
5. Treat the supplied source as a list of technology companies; do not spend research tokens re-qualifying the industry.

## Build candidate records

For each source company:

- Capture `company_name`.
- Capture `website_url` only when supplied or unambiguously linked by the source. Do not search for it separately in this command.
- Preserve address, coordinates, and address-source evidence only when those values are supplied by the input. Never infer or guess them.
- Do not retain the URL used merely to discover the company.
- Set `company_id` to a stable lowercase ASCII slug of the company name. Remove corporate suffixes only when they are clearly legal suffixes, not part of the brand.
- Set `address_status` to `pending` and `verified_date` to `null`, even if an address is supplied. Validation belongs to `/company-validate`.
- Set both `added_date` and `updated_date` to the handoff `created_at` timestamp.

Use complete records matching `schemas/companies.schema.json`. For an unknown address use `null`. For a partial supplied address, use an address object with all five keys and `null` for unknown parts.

## Deduplication policy

The importer is authoritative and performs deterministic deduplication in this order:

1. exact `company_id`;
2. normalized `website_url` domain;
3. normalized company name, ignoring case, punctuation, whitespace, and ordinary legal suffixes.

Include each source company at most once in the handoff. For an existing company, supply useful incoming values so the importer can fill missing fields. Never replace a non-null catalog value with a different value. Report ambiguous identities or conflicting facts in `issues`.

New companies must be appended to the end of the canonical list. Existing catalog order must remain unchanged.

## Write handoff

Create one handoff matching `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "company-ingest",
  "created_at": "<UTC RFC 3339 timestamp>",
  "changes": [],
  "issues": []
}
```

Write it to this exact repository using an absolute path with this filename pattern:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/<UTC compact timestamp>-company-ingest.json`

Do not apply it yourself. Finish by giving the human these commands with the
actual absolute handoff path:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/<filename>-company-ingest.json" --dry-run
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/<filename>-company-ingest.json"
```

If `.venv/bin/python` is unavailable, still write the handoff and print those exact commands; do not mutate canonical data directly.

## Completion response

End with a short, human-readable summary containing:

- `Status: complete`, `partial`, or `blocked`;
- source records examined, candidates written, records skipped, and conflicts/issues;
- the absolute handoff path;
- that it was not applied by the research agent; and
- the exact copy-pastable dry-run and apply commands.
