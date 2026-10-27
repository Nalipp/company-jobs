# Company Job Fetch

## Purpose

Poll each saved company's canonical jobs source and propose only newly identified
jobs that pass every current hard constraint. Known job objects remain unchanged.

## Scope

Read `config/search.json`, `data/companies.json`, `docs/source-policy.md`,
`docs/data-contract.md`, `docs/handoff-protocol.md`, and the search, company, and
handoff schemas. An ordinary run processes every saved company and never discovers
unknown companies. An empty catalog returns a no-op without browsing.

## Stable job identity

Use a source-provided job or requisition ID. When none exists, use a stable,
job-specific canonical URL. Do not create an ID from content, title, location,
date, salary, model judgment, or a hash.

Within one company:

- when fetched and saved jobs both have non-null IDs, only equal IDs match;
- when either lacks an ID, equal normalized canonical URLs match; and
- a different non-null source ID is a new posting even when its title is reused.

Remove only obvious tracking parameters and an inconsequential trailing slash for
comparison. Preserve any query or fragment that distinguishes jobs. A generic
careers page shared by roles is not stable job identity.

## Procedure

For each company:

1. Open its saved job source, preferring an available structured ATS feed.
2. Collect stable identities and inexpensive constraint fields for current roles.
3. If the source is unavailable or unsupported, add an issue; never interpret the
   failure as an empty board.
4. Compare identities with saved jobs and ignore known postings without rereading,
   emitting, or modifying them.
5. Put missing, shared, or conflicting identity in `issues` and continue.
6. Apply inexpensive title, location, workplace, employment, seniority, and salary
   gates to unseen roles.
7. Inspect canonical postings for remaining evidence, including role-specific AI
   work. Propose only jobs that independently pass every gate.

Rejected jobs are not stored. This allows a later run to admit one if its source
facts or the configuration change.

## Change and issue shape

Emit one `changes` item only for a company that has at least one qualifying unseen
job:

```json
{
  "company_id": "example-ai",
  "new_jobs": []
}
```

`new_jobs` must be non-empty when the change exists. Use issue codes such as
`missing_stable_identity`, `shared_job_url`, `identity_conflict`,
`source_unavailable`, or `unsupported_source`. An issue does not block other
companies or jobs.

Write the common handoff with operation `company-job-fetch`. Do not copy known
jobs, company records, empty per-company results, or board snapshots. The external
agent never edits the catalog.

## Non-goals

This version does not overwrite changed postings, hash descriptions, remember
rejected roles, remove missing jobs, or mark jobs inactive. Surface recurring
identity or lifecycle problems before adding state.
