# Company catalog design

Status: active. The initial legacy migration completed on 2026-08-05. See
`migration-report.json` and `MIGRATION-PLAN.md`.

The catalog improves the current workflow's memory without introducing a database.
It uses one JSON file per company under `catalog/companies/`, validated against
`../schemas/company-catalog.schema.json`.

## Responsibilities

- `../legacy-old/2026-08-05-pre-catalog/sources/employers.json` preserves the
  legacy source registry and is not the active company list.
- Each company file owns its company identity, career and ATS
  sources, offices, remembered jobs, and source-refresh state.
- `discoveries/` remains an auditable queue of candidates produced by a collection
  pass.
- `runs/` remains an auditable record of profile-specific verification results.
- `candidate-memory.json` stores compact identity, hash, and outcome records for
  rejected, unresolved, queued, and included candidates without storing rejected
  descriptions.
- Catalog records provide reusable memory; discoveries and runs provide history.
- Rejected and unresolved leads remain outside company files. A company enters the
  catalog only after at least one job passes the posting-level hard constraints.

## Company identity and deduplication

Use a stable lowercase `company_id` slug as the filename. Before creating a new
company, attempt to match it in this order:

1. Exact ATS provider plus board ID
2. Exact normalized primary website domain
3. Existing career-source URL
4. Normalized company name or alias, followed by manual review when ambiguous

Never merge companies using a similar name alone. Preserve alternate names in
`company.aliases`.

Company description, industries, funding stage, and size are populated only when
encountered during normal job/source work. Do not run separate enrichment searches
for these fields unless the user asks.

## Sources

A company may have more than one source. Each source records a stable source ID,
source type, provider, URL, board ID when available, status, discovery provenance,
and its latest check timestamps.

Provider feeds and official company career pages are canonical for current job
state. Search engines and job boards may discover a source but do not override a
conflicting canonical source.

## Offices and map data

Every company record contains at least one office record. When no office has been
researched, use a placeholder with `null` address, latitude, longitude, and
`address_verified`.

A company may have multiple offices, but the catalog is not intended to describe
the company's complete office footprint. Only populate offices relevant to the
active profile's approved job locations. Do not search for, backfill, geocode, or
maintain offices in other cities merely because the company operates there.

Every saved office always exposes:

- `address`
- `latitude`
- `longitude`
- `address_verified`
- evidence and last-checked time

The single address flag has these meanings:

- `null`: address validation has not succeeded yet
- `true`: the address and coordinates are verified inside an approved city
- `false`: validation confirmed that the candidate address is unacceptable

Coordinates are saved only after the address is supported by attributable evidence
and geocoded. Unknown components remain `null`. Only `address_verified: true`
records may appear on a map.

A company office is not automatically the exact worksite for a job. It may be used
as a clearly labeled `company_office_proxy` only when the canonical posting also
confirms that the job is hybrid or on-site in that city. Exact job-site evidence
continues to belong to the job or verification run.

Discovery and refresh do not perform separate office research. They may save an
approved-city address already present in a canonical source, but otherwise leave
the office unresolved. Failure to find an address does not block company creation
or job collection.

An explicit office-validation pass is limited to companies with plausible active
jobs in the approved city. It stops after finding enough evidence for one acceptable
map pin per relevant city unless the user asks for more. It never expands into a
general search for the company's other locations.

## Catalog-admission gate

Discovery may retain broad leads temporarily, but it does not create a company
file. A company file is created only after a canonical active job passes every
posting-level hard constraint that can be determined without address research:

- compatible approved title or role family
- any title containing `Frontend` or `Front-End`, close punctuation variants of
  `Software Engineer, Product`, or `Forward Deployed Engineer` passes the title
  screen without opening the gate to generic software-engineering titles
- explicit role-specific AI work in the title or canonical description
- full-time employment
- acceptable seniority
- hybrid or on-site arrangement
- official posting text explicitly identifying San Francisco, not only the Bay Area
- published annual USD base-salary maximum of at least $200,000
- no hard-excluded title or responsibility

Ambiguous or rejected jobs do not enter company files. Preserve only lightweight
identity, content hash, and outcome history outside the company catalog when needed
to avoid repeating the same work.

Street address and coordinates are not part of initial catalog admission. Explicit
San Francisco text on the official posting is sufficient for the first pass.

## Job memory

Jobs are keyed within the company file by a stable ATS job ID when available.
Otherwise, derive a deterministic key from the canonical URL. Match jobs by source
job ID first and canonical URL second.

For every admitted job, retain normalized canonical metadata, exact compensation
text, the complete meaningful plain-text description, content hashes, discovery
provenance, timestamps, activity state, and reusable profile evaluations. Do not
store raw HTML, scripts, or navigation noise.

Legacy migration may use `description_completeness: evidence_only` when the saved
run contains sufficient verification evidence but not the complete canonical text.
The next successful source refresh must replace it with `full`, recompute the
content hash, and re-evaluate the job. New admissions require `full` descriptions.

## Change detection and reuse

Compute SHA-256 hashes from normalized metadata and canonical description content.

- New job ID or URL: create a record and consider it for verification.
- Unchanged metadata and content: update check timestamps and reuse prior evidence.
- Changed metadata or content: update the record and re-evaluate affected rules.
- Changed profile with unchanged job: reuse raw job data and run only a new
  profile evaluation.
- Job missing from one successful feed refresh: increment its missing count.
- Job missing from two consecutive successful feed refreshes: mark it inactive.
- Failed or partial source refresh: do not infer that missing jobs are inactive.

An evaluation is reusable only when both its `profile_hash` and `job_content_hash`
still match. This prevents stale decisions without rereading unchanged postings.

## Compensation rule

For the current profile, a job satisfies the compensation constraint when its
published annual USD base-salary range has a maximum of at least $200,000. The
minimum of the published range does not need to be $200,000.

When no qualifying published base range is available, preserve the lead as
`needs_review` outside the company catalog. Do not claim that it satisfies the
constraint and do not infer compensation from third-party estimates.

## Collection workflows

### Known-company refresh

The refresh workflow iterates existing company files, polls their canonical ATS or
career sources, updates check state, and emits new or changed job leads into a
discovery checkpoint. It does not run broad web searches or reread unchanged job
descriptions. New jobs are added to the company file only after `/verify` confirms
that they pass the current hard constraints.

Command: `/refresh`.

### Catalog expansion

The expansion workflow uses web search and selected job boards to find jobs and
companies outside the catalog. It resolves canonical employer sources and emits
plausible jobs into a discovery checkpoint. It creates a company file only after
`/verify` confirms at least one qualifying job.

Command: `/discover`.

Both workflows feed the existing bounded `/verify` stage. Verification writes its
audit result under `runs/` and adds a reusable evaluation to the corresponding
catalog job record.

Local workflow helpers live in `../scripts/daily-jobs.mjs`. They are dry-run by
default and require `--write` to change persisted state. Exact command order,
validation, and recovery behavior are documented in `../RUNBOOK.md`.

`/fetch` defaults to `/refresh` followed by one `/verify` batch.
`/fetch --expand` runs `/discover` followed by one `/verify` batch.
This keeps the inexpensive known-company check as the daily default while making
broader, token-intensive expansion explicit.

## Slash commands

- `/help`: show every command and its purpose.
- `/daily-jobs-init`: create and approve a job-search profile.
- `/show`: display the current profile and status.
- `/edit`: change selected profile rules and request approval.
- `/companies [company]`: list cataloged companies or show one company, its
  qualifying jobs, source status, and address-verification state.
- `/refresh [company]`: poll one company, or all cataloged companies when omitted,
  and queue only new or changed job leads.
- `/discover`: run broader web discovery for qualifying jobs at companies outside
  the catalog; it does not create company files before verification.
- `/verify [discovery] [--retry-review]`: apply the hard posting-level gate to one
  bounded candidate batch, write the audit run, and admit passing jobs and companies
  to the catalog.
- `/fetch`: run `/refresh` followed by one `/verify` batch.
- `/fetch --expand`: run `/discover` followed by one `/verify` batch.
- `/validate-addresses [company]`: find and geocode an approved-city address for
  one company, then set `address_verified` to `true`, `false`, or leave it `null`.
- `/history`: list or summarize saved discoveries, verification runs, and refreshes.

## Size management

There is no fixed job limit per company. Do not introduce archival behavior until
real file sizes justify it. Each run opens only the company files it needs. If a
company file later becomes unwieldy, move old inactive jobs into
`catalog/archive/<company-id>-<year>.json` while retaining their IDs and latest
state in the live company record.
