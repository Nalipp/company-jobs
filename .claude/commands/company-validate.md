---
description: Validate physical addresses and coordinates or backfill missing official websites
argument-hint: [limit N] [retry not_found] [website-backfill] [company <company_id> ...]
---

# Company validate

Research and validate physical company addresses or backfill official websites.
Do not ingest companies, inspect jobs, apply job constraints, or research ATS providers.

## Safety and batch selection

1. Confirm the working directory is exactly `/Users/natelipp/ai-code/_v3_daily-jobs`. Stop with a clear correction if it is not.
2. Read `AGENTS.md`, `schemas/companies.schema.json`, `schemas/handoff.schema.json`, and `data/companies.json` before doing work.
3. By default, this is an address-validation run: select the first 50 companies whose `address_status` is `pending`, ordered by `added_date` and then `company_id`. This default applies only when no explicit `limit N` appears in `$ARGUMENTS`.
4. When `$ARGUMENTS` explicitly says `website-backfill`, this is a website-only run: select verified companies whose `website_url` is null. Do not research or rewrite their addresses.
5. Honor any explicit `limit N` where `N` is a positive integer. Select exactly `N` eligible pending companies when at least `N` are available; an explicit limit is a workload commitment, not a maximum or a suggestion. Never silently fall back to the default 50 or reduce an explicit limit to an arbitrary smaller batch. There is no command-level maximum; the coordinator or external research prompt may choose a larger or smaller batch based on the available research capacity. If the research cannot be completed, it may be performed in internal chunks, but the agent must either write one handoff covering every selected company or explicitly report a partial run with the actual completed count and unresolved company IDs.
6. Only select `not_found` companies when `$ARGUMENTS` explicitly says to retry them. Never address-reprocess `verified` or `outside_sf` companies. Verified companies are eligible only for the explicit `website-backfill` mode.
7. When `$ARGUMENTS` contains one or more `company <company_id>` selectors, process only those eligible company IDs and honor their given order. Report an issue for an unknown or ineligible ID rather than silently selecting another company.
8. If no companies are eligible, write an empty handoff and report that no work was needed. Do not apply it.

## Official website enrichment

For each selected company whose `website_url` is null, make a bounded attempt to
identify the official company website before or alongside address research:

1. Prefer an unambiguous official domain linked from an existing credible source.
2. Otherwise use one focused search for `"<company name>" official website` and
   confirm the result using the company's identity, product, or official about,
   contact, legal, or careers content.
3. Save the canonical official site or company root URL, not a social profile,
   directory, search result, redirect wrapper, or third-party job board.
4. If identity is ambiguous, leave `website_url` unchanged and add an issue. A
   missing website alone is not an issue after the bounded attempt.

Website enrichment is independent of address validation. Failure to find a
website must not block a valid address result, and finding a website is never
evidence that an address is physical or in San Francisco. Include `website_url`
at the validation-change level only when a new official URL was established. The
importer fills a null catalog value and preserves any existing non-null value.
For a website-only enrichment of an already validated company, include its
current `expected_status` and `website_url`, and omit `address_result`; this
leaves every address field and status unchanged.

## Address research policy

Skip this section during `website-backfill`. For each selected company in an
address-validation run, perform a bounded address search:

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

## Write handoff

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
      "website_url": "https://example.com/",
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

For `website-backfill`, an individual change contains only the current status
guard and the discovered URL; it must not include `address_result`:

```json
{
  "company_id": "example-company",
  "expected_status": "verified",
  "website_url": "https://example.com/"
}
```

Write it to:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/<UTC compact timestamp>-company-validate.json`

Do not apply it yourself. Finish by giving the human these commands with the
actual absolute handoff path:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/<filename>-company-validate.json" --dry-run
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/<filename>-company-validate.json"
```

If `.venv/bin/python` is unavailable, still write the handoff and print those exact commands; do not mutate canonical data directly.

## Completion response

End with a concise summary containing:

- `Status: complete`, `partial`, or `blocked`;
- companies examined and counts for `verified`, `outside_sf`, `not_found`, and issues;
- missing official websites found and catalog website fields filled;
- the absolute handoff path;
- that it was not applied by the research agent;
- the exact copy-pastable dry-run and apply commands;
- for every company left unchanged because of access, tooling, identity, or source failure, explain what failed and provide the literal copy-pastable retry command `/company-validate company <actual-company-id>` with the real ID substituted.
