# Career-source discovery

## Purpose

Build reusable instructions for polling the official career sources of companies
whose physical-address status is `verified`. This operation discovers and checks
career sources only. It does not apply a search profile, retain jobs, or make a
claim that a company currently has matching jobs.

`data/companies.json` remains the company and physical-location catalog.
`data/career_sources.json` is the separate canonical career-source registry. A
career source references its owner through `company_id`; it does not copy the
company name, address, or website into the source registry.

## Eligibility and identity

- Process only companies currently marked `verified` in `data/companies.json`.
- The source importer must reject a source whose `company_id` is absent or is not
  currently verified.
- `source_id` is a stable lowercase slug. A company may have multiple source
  records when divisions, brands, acquisitions, geographies, or genuinely
  separate boards require them.
- Treat the provider plus exact case-sensitive `board_id`, when available, as a
  stable source identity. Otherwise use the normalized `listings_url`.
- Do not assign one board ambiguously to multiple companies. Record an issue
  instead of guessing when employer identity or corporate-brand ownership is
  unclear.

## Discovery procedure

1. Begin at the official `website_url` saved for the company. If it is null,
   perform a bounded search for the official company website and propose that URL
   as a separate safe enrichment; do not treat a search result alone as proof of
   an official job source.
2. Find the employer-controlled careers or jobs page.
3. Follow the employer's link or embedded listing to the source that actually
   enumerates current openings.
4. Save the employer page as `careers_url` and the enumeration endpoint as
   `listings_url`. They may be the same URL for a custom employer site.
5. Establish `official_relationship_url` from an employer-controlled page that
   connects the company to the listings source. A board found only through search
   is not sufficient.
6. Identify the provider and preserve its exact board or tenant identifier without
   guessing. Provider values are lowercase slugs such as `ashby`, `greenhouse`,
   `lever`, `workday`, `smartrecruiters`, `icims`, `rippling`, `custom`, or
   `unknown`.
7. Confirm that the source can enumerate its current listings and open at least one
   canonical job detail when a job exists. Do not retain the sample job.
8. Record whether enumeration is complete, stable source job identities are
   available, and structured data is accessible.
9. Save short, reusable access instructions. Prefer stable provider behavior over
   brittle CSS selectors, click coordinates, or layout-specific steps.

## Important distinctions

- A verified San Francisco company may have no San Francisco jobs. Location is a
  job-level constraint evaluated later.
- A successfully enumerated source with zero current jobs is still a working
  `ready` source.
- A blocked, rate-limited, malformed, or inaccessible source is not an empty job
  board.
- The employer page and underlying ATS may expose the same postings. Select one
  preferred enumeration endpoint rather than creating duplicate active sources.
- Custom sites may paginate, lazy-load, use several locales, or require a browser.
  Test whether the recorded method can enumerate the whole source.
- A single company may legitimately need multiple sources. Record the scope of
  each source, such as `all roles`, `US roles`, or a named division.
- One temporary failure must not delete or overwrite a previously working source.
  Report the failure and preserve the last known-good record until a deliberate
  refresh confirms a replacement or stale source.

## Status policy

- `ready`: the official relationship is established and the source is usable.
- `no_careers_found`: a bounded search found no official careers destination.
- `blocked`: an apparent official source exists but could not be evaluated because
  of access or tooling failure.
- `unsupported`: the official source is understood but the current workflow
  cannot enumerate it reliably.
- `stale`: a previously working source has been deliberately rechecked and is no
  longer usable or has been replaced.

## Handoff and application contract

Discovery agents write exactly one `career-source-discover` handoff in `imports/`;
they never edit `data/career_sources.json` or run the importer. Each handoff
change contains a complete source record and `expected_updated_date`:

- use `null` when adding a new `source_id`;
- use the exact saved source `updated_date` when deliberately refreshing an
  existing source; and
- report a transient refresh failure as an issue rather than submitting a
  replacement that downgrades an existing `ready` source to `blocked`,
  `unsupported`, or `no_careers_found`.

For a new source, `added_date` and `updated_date` equal the handoff's
`created_at`. For an update, keep `added_date` unchanged and set `updated_date`
to the handoff's `created_at`. Codex applies the handoff through
`scripts/apply_handoff.py`, which verifies the company is still `verified`,
preserves source identity, and makes an identical replay a no-op.

No periodic refresh is automatic. A job scan that cannot evaluate a saved source
must report the specific failure, leave that company's job state unchanged, and
give the human the exact refresh command:

```text
/career-source-discover <actual-company-id> --refresh
```

The completion response must replace the placeholder with the real `company_id`.
When several companies fail, provide one copy-pastable command per company.

## Job-scan contract

Career-source discovery never applies role constraints. A later job scan reads a
named file from `config/search-profiles/`, polls every eligible `ready` source,
and distinguishes these outcomes:

- source succeeded and produced current jobs, but none matched;
- source succeeded and contained zero current jobs; and
- source could not be evaluated.

Only the third outcome requires a source refresh. A scan may mark a previously
active job inactive only after successfully performing complete enumeration of
that source. A source failure must never remove, deactivate, or alter saved jobs.
