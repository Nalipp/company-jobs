# External Claude work statement: collect Built In company leads

Use this work statement to collect San Francisco company leads from Built In and
stage them for Codex. This is a discovery task, not address validation.

## Responsibility boundary

Claude is the research agent. Codex owns every mutation of canonical project
state.

- Work only in `/Users/natelipp/ai-code/_v3_daily-jobs`.
- Read `AGENTS.md`, `data/companies.json`, `schemas/companies.schema.json`, and
  `schemas/handoff.schema.json` completely before collecting data.
- Do not edit or replace any existing repository file.
- Do not edit `data/companies.json` under any circumstances.
- Do not run `scripts/apply_handoff.py` or any other command that can update the
  catalog.
- The only permitted write is one new, never-overwritten JSON handoff under
  `imports/`. Codex will review and apply it later.
- Do not collect jobs, ATS data, compensation, funding, people, email addresses,
  or physical street addresses.

If the environment cannot write the handoff file, return one fenced JSON block
for Codex to save. Do not attempt another repository mutation.

## Source interpretation

Start from:

`https://builtin.com/companies/location/san-francisco?country=USA&page=<PAGE>`

Built In labels this collection **San Francisco Bay Area**. It is a large lead
set, not evidence that every listed company has a current physical office inside
the City and County of San Francisco. The listing may include fully remote
companies and companies with several offices or an HQ elsewhere.

Use Built In only to discover a company and, when present on its profile, follow
the direct **View Website** link to capture the company's website. HQ and office
city labels may be used to prioritize promising leads, but they must not be saved
as an address or treated as validation. `/company-validate` will later determine
whether a physical San Francisco office exists.

The general Built In listing currently shows 20 company cards per page. Process a
bounded page range supplied by the coordinator. If no range is supplied, use pages
1 through 2 and target at most 30 handoff changes. Hard limits for one run are 3
listing pages, 60 company-profile fetches, and 50 handoff changes.

Use ordinary accessible pages at a conservative pace. Do not sign in, bypass an
access restriction, reverse-engineer an internal endpoint, or use a bulk scraper.
If access is blocked, record a genuine issue and finish with `partial` or
`blocked` status rather than trying to circumvent it.

## Step 1: collect candidates

Process the assigned listing pages in ascending order. For each real company
card, retain only temporary working notes for:

- Built In company name;
- Built In profile URL;
- direct external **View Website** URL, if the profile supplies one; and
- whether the profile says `HQ San Francisco` or lists a San Francisco office.

Prioritize profiles that identify San Francisco as the HQ or one of the office
cities. Skip a profile that is only `Fully Remote`, or whose profile identifies
only non-San-Francisco offices. This is lead selection only; it is not address
validation.

Do not perform a separate web search for a missing website. Set `website_url` to
`null` when Built In does not provide an unambiguous direct website link. Do not
copy descriptions, employee counts, categories, jobs, office addresses, or the
Built In discovery URL into the handoff.

Stop when the assigned page range is complete, the target number of changes is
reached, or a hard limit is reached, whichever happens first.

## Step 2: normalize and deduplicate

Compare every candidate with the latest `data/companies.json`. Deduplicate within
the run and against the catalog in this order:

1. exact `company_id`;
2. normalized website domain, ignoring scheme, `www.`, path, and a trailing dot;
3. normalized company name, ignoring case, punctuation, whitespace, accents, and
   ordinary legal suffixes such as Inc., Corp., LLC, Ltd., and Limited.

Create `company_id` as a stable lowercase ASCII slug of the brand name. Convert
`&` to `and`, replace other non-alphanumeric runs with one hyphen, collapse and
trim hyphens, and remove a trailing legal suffix only when it is clearly not part
of the brand.

Normally omit an already-complete catalog company. If the catalog company has a
null `website_url` and Built In supplies an unambiguous direct website, include
one incoming record so the importer can fill the missing value. Never propose a
different value for an existing non-null website. Put ambiguous identities or
conflicting website domains in `issues` and omit the unsafe change.

## Step 3: write one import-ready JSON handoff

Use one UTC RFC 3339 timestamp for `created_at`, `added_date`, and `updated_date`.
Write a complete handoff conforming to `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "company-ingest",
  "created_at": "2026-08-09T23:15:00Z",
  "changes": [
    {
      "company_id": "example-company",
      "company_name": "Example Company",
      "website_url": "https://example.com/",
      "address": null,
      "latitude": null,
      "longitude": null,
      "address_status": "pending",
      "address_source_url": null,
      "address_source_label": null,
      "verified_date": null,
      "added_date": "2026-08-09T23:15:00Z",
      "updated_date": "2026-08-09T23:15:00Z"
    }
  ],
  "issues": []
}
```

Every change must have exactly the fields shown above. For this Built In workflow,
all address, coordinate, and address-source fields must be null, and every
`address_status` must be `pending`.

Each issue must have exactly this shape:

```json
{
  "code": "ambiguous_company_identity",
  "company_id": null,
  "subject": "Example",
  "source_url": "https://builtin.com/company/example",
  "details": "The Built In profile could refer to either of two catalog companies."
}
```

Write JSON only to a new path using the actual page range:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-company-ingest-builtin-p001-p002.json`

Use zero-padded page numbers, UTC, and millisecond precision. Never overwrite an
existing file. The page range in the filename is coverage bookkeeping; discovery
source URLs do not belong in the canonical catalog.

Before finishing, re-read the JSON and check that it parses, has only schema
fields, has no duplicate candidate IDs or website domains, and contains no
address evidence. Do not run the importer as part of this check.

## Step 4: hand off processing to Codex

Do not apply the handoff. Finish with:

- `Status: complete`, `partial`, `no-op`, or `blocked`;
- assigned and completed Built In page ranges;
- listing cards examined, company profiles examined, changes written, existing
  companies skipped, and issues;
- the exact absolute JSON path; and
- a `Next step for Codex` containing both literal commands below with the actual
  JSON path substituted. Do not leave placeholders in the final response.

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/ACTUAL-FILENAME.json" --dry-run
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v3_daily-jobs/imports/ACTUAL-FILENAME.json"
```

Codex should review the dry-run output and apply only when schema validation and
identity conflict checks succeed. Reapplying the same handoff must remain a
no-op. After the ingest is applied, the next repository operation is
`/company-validate` in bounded batches.

## Coordinator rollout

Run non-overlapping page windows, starting with pages 1-2, then 3-4, 5-6, and so
on. Built In ordering and counts can change, so overlap or movement is expected;
the catalog importer provides the safety boundary through deterministic identity
matching and replay-safe application. Prefer small page windows over one long
session so a browsing interruption does not lose a large result.
