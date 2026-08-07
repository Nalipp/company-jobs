# Data Contract

`schemas/companies.schema.json` governs the canonical catalog.
`schemas/handoff.schema.json` governs every research result. Read
`docs/handoff-protocol.md` for the shared envelope and import behavior.

## Company identity

Set `company_id` to a stable lowercase hyphenated slug derived from the official
name. Before adding a company, compare:

1. exact ATS provider plus board ID, when both exist;
2. exact lowercase primary domain; and
3. normalized jobs URL after removing an inconsequential trailing slash and only
   obvious tracking parameters.

Any match means the company is already known. Similar names are not merge keys.
If different keys point to different saved companies, report a conflict.

`website_url` and `primary_domain` may be null. `jobs_url` is required.

## Job identity

Identity is scoped to a saved company:

1. when fetched and saved jobs both have IDs, only equal source IDs match;
2. when either lacks an ID, equal normalized job-specific canonical URLs match.

Different non-null source IDs represent different postings even when their titles
are equal. Never create identity from title, company, location, date, description,
salary, model judgment, or a hash. A generic careers page shared by roles is not a
job-specific URL; report that posting as an issue.

Known job objects are immutable in this version. Fetch appends genuinely new jobs
and does not update, remove, or mark existing ones inactive.

## Normalized job facts

- Preserve official title and location text.
- Normalize workplace arrangement and seniority only with official evidence.
- Preserve a missing posting date as null.
- Store salary as numbers plus exact published text.
- Save only active canonically verified jobs.
- Record ISO 8601 verification times and short evidence for every hard gate.

Do not store full descriptions, raw HTML, search snippets, scores, or reasoning.

## Map location

Discovery creates a pending map target for the configured city with address,
postal code, coordinates, location type, source fields, and checked time set to
null.

Address research proposes either:

- `located`, with normalized address, coordinates, one source, checked time, and
  `exact_worksite` or `company_office_proxy`; or
- `not_found`, with result fields null and checked time populated.

Each address change includes `expected_status`. The importer applies it only when
the latest catalog still has that status, unless the desired map object is already
present.

## Apply behavior

`scripts/apply_handoff.py` validates both inputs and the complete proposed catalog.
It appends new companies or jobs, changes only the selected map object for address
results, reports issues and conflicts, and leaves the catalog byte-for-byte
unchanged on a no-op. Successful writes use an atomic replacement.
