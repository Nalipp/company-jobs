---
description: Discover reusable official career and job-listing sources for verified companies
argument-hint: [limit N] [company <company_id> ...] [--refresh]
---

# Career-source discover

Discover and verify the direct official source used to enumerate a verified San
Francisco company's current job openings. This command builds reusable access
instructions only; it does not evaluate a search profile, collect jobs, or
change company address state.

## Safety and selection

1. Confirm the working directory is `/Users/natelipp/ai-code/_v3_daily-jobs`.
2. Read `AGENTS.md`, `instructions/career-source-discovery.md`,
   `schemas/career-sources.schema.json`, `schemas/handoff.schema.json`,
   `data/companies.json`, and `data/career_sources.json` before research.
3. Process only companies with `address_status: "verified"`.
4. By default select the first 10 verified companies without a career-source
   record, ordered by the catalog's existing company order. Honor a limit from 1
   through 25.
5. With `company <company_id>`, process only the named verified companies, in
   the given order. Report unknown or ineligible IDs as issues.
6. `--refresh` is required to recheck a company that already has source records.
   During a failed refresh, leave an existing `ready` record unchanged and report
   an issue instead.

## Research and source record

Follow `instructions/career-source-discovery.md` exactly. Start at the saved
official company website. Record the employer-controlled careers page, the
actual listing endpoint, provider and exact board identifier, an employer page
that proves the official relationship, capabilities, scope, and short reusable
instructions. Do not save a source merely because a search engine found an ATS.

A `ready` source must enumerate all current listings and provide stable job
identity. A company with no official careers destination receives a
`no_careers_found` record; an access/tooling failure is `blocked`; a source that
is understood but cannot be enumerated reliably is `unsupported`.

## Handoff-only output

Do not edit `data/companies.json` or `data/career_sources.json`, and do not run
`scripts/apply_handoff.py`. Your only repository write is one handoff in
`imports/` with operation `career-source-discover`.

Each change contains a complete source record and its expected current source
timestamp. Use `null` for `expected_updated_date` when adding a source. When
refreshing an existing source, use its exact current `updated_date`. For a new
source, both `added_date` and `updated_date` must equal the handoff's
`created_at`; for an update, retain its original `added_date` and set
`updated_date` to the handoff's `created_at`.

```json
{
  "schema_version": 1,
  "operation": "career-source-discover",
  "created_at": "<UTC RFC 3339 timestamp>",
  "changes": [
    {
      "expected_updated_date": null,
      "source": {
        "source_id": "example-greenhouse",
        "company_id": "example",
        "careers_url": "https://example.com/careers",
        "listings_url": "https://job-boards.greenhouse.io/example",
        "provider": "greenhouse",
        "board_id": "example",
        "access_mode": "structured_feed",
        "scope": "All roles",
        "instructions": "Enumerate the public board and open canonical job details.",
        "capabilities": {
          "complete_enumeration": true,
          "stable_job_identity": true,
          "structured_data": true
        },
        "status": "ready",
        "official_relationship_url": "https://example.com/careers",
        "checked_at": "<UTC RFC 3339 timestamp>",
        "last_success_at": "<UTC RFC 3339 timestamp>",
        "added_date": "<same as created_at>",
        "updated_date": "<same as created_at>"
      }
    }
  ],
  "issues": []
}
```

Write the handoff to:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/<UTC compact timestamp>-career-source-discover.json`

## Completion response

Report companies reviewed; `ready`, `no_careers_found`, `blocked`, and
`unsupported` counts; source failures and affected company IDs; the absolute
handoff path; and these exact copy-pastable commands with the real path:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "<actual-handoff-path>" --dry-run
.venv/bin/python scripts/apply_handoff.py "<actual-handoff-path>"
```

Do not apply the handoff yourself.
