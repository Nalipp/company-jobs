---
description: Validate physical addresses and coordinates for pending companies
argument-hint: [limit N] [retry not_found]
---

# Company validate

Research and validate physical company addresses. Do not ingest companies, inspect jobs, apply job constraints, or research ATS providers.

## Safety and batch selection

1. Confirm the working directory is exactly `/Users/natelipp/ai-code/_v3_daily-jobs`. Stop with a clear correction if it is not.
2. Read `AGENTS.md`, `schemas/companies.schema.json`, `schemas/handoff.schema.json`, and `data/companies.json` before doing work.
3. By default select the first 10 companies whose `address_status` is `pending`, ordered by `added_date` and then `company_id`.
4. Honor an explicit limit from 1 through 25.
5. Only select `not_found` companies when `$ARGUMENTS` explicitly says to retry them. Never reprocess `verified` or `outside_sf` companies.
6. If no companies are eligible, create and apply an empty handoff, then report that no work was needed.

## Research policy

For each selected company, perform a bounded address search:

1. If the pending record contains an address, start by checking that candidate.
2. Prefer the company's official contact, about, privacy, terms, legal, or careers page.
3. Otherwise search for `"<company name>" "San Francisco office" address`. Use at most two focused search-query variants.
4. Accept one credible source that clearly associates the company with a current physical office. A second source is not required.
5. Accept official company pages, Google Maps or a comparable business listing, or reputable reporting, commercial-real-estate coverage, and business directories.
6. Missing suite or floor information is acceptable when the street address is otherwise clear.
7. Reject virtual mailboxes, registered-agent addresses, another company with a similar name, stale addresses contradicted by a newer source, and ambiguous matches.
8. Obtain latitude and longitude for the accepted street address from an actual map or geocoding result. Never estimate or invent coordinates.

Classify the result:

- `verified`: a credible physical address is inside the City and County of San Francisco.
- `outside_sf`: a credible current physical office is found, but it is outside San Francisco.
- `not_found`: the searches completed but produced no credible physical address.
- Tool or access failure: leave the company unchanged and add an issue; do not call it `not_found`.

For `verified` and `outside_sf`, save the complete address object, numeric coordinates, `address_source_url`, a short `address_source_label`, and the UTC `verified_date`. For `not_found`, set address, coordinates, and address-source fields to `null` and set `verified_date` to the attempt time.

## Write and apply

Create one handoff matching `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "company-validate",
  "created_at": "<UTC RFC 3339 timestamp>",
  "changes": [
    {
      "company_id": "example-company",
      "expected_status": "pending",
      "address_result": {
        "address": {
          "street": "123 Example St",
          "city": "San Francisco",
          "region": "CA",
          "postal_code": "94105",
          "country": "US"
        },
        "latitude": 37.0,
        "longitude": -122.0,
        "address_status": "verified",
        "address_source_url": "https://example.com/contact",
        "address_source_label": "Official contact page",
        "verified_date": "<UTC RFC 3339 timestamp>"
      }
    }
  ],
  "issues": []
}
```

Write it to:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/<UTC compact timestamp>-company-validate.json`

Then apply it automatically:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/<filename>-company-validate.json"
```

If `.venv/bin/python` is unavailable, do not mutate the catalog directly. Keep the handoff and print the exact command the user should run after setup.

## Completion response

End with a concise summary containing:

- `Status: complete`, `partial`, or `blocked`;
- companies examined and counts for `verified`, `outside_sf`, `not_found`, and issues;
- the absolute handoff path;
- whether it was applied;
- when not applied, the exact copy-pastable `cd` plus `apply_handoff.py` command.

