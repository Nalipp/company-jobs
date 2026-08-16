---
description: Scan ready career sources against one exact versioned search profile
argument-hint: <profile_id> [all] [company <company_id> ...] [source <source_id> ...] [access-mode <mode>]
---

# Job scan

Enumerate current jobs from reusable official career sources, apply one exact
search profile, and propose profile-specific job-result changes. Do not discover
companies or rewrite career-source instructions.

## Inputs and selection

1. Confirm the working directory is `/Users/natelipp/ai-code/_v3_daily-jobs`.
2. Read `AGENTS.md`, `instructions/job-scan.md`, `data/companies.json`,
   `data/career_sources.json`, `schemas/job-results.schema.json`,
   `schemas/handoff.schema.json`, and the requested profile and job-result files.
3. Require an exact `profile_id` that has a matching file in both
   `config/search-profiles/` and `data/job-results/`.
4. By default, process every `ready` career source. Optional `company`, `source`,
   or `access-mode` selectors narrow the run without changing its semantics.
5. Never scan `no_careers_found`, `blocked`, `unsupported`, or `stale` sources as
   though they were empty boards.
6. Continue after individual source failures. If the overall run must stop early,
   write a valid partial handoff containing only completed or explicitly failed
   source scans, list every unprocessed source in the completion response, and do
   not invent outcomes for work that was not performed.

## Source scan procedure

For each selected ready source:

1. Follow its saved access instructions and enumerate the entire current board.
2. Capture a stable identity for every current posting in
   `active_job_identities`, including postings that do not match the profile. Use
   the source job/requisition ID when available and its canonical job-detail URL.
   A generic careers URL is not a job identity.
3. Set `current_job_count` to the exact length of that complete identity list.
4. Apply inexpensive official fields first: location, workplace, employment,
   compensation, and obvious role-family signals. Open canonical job details for
   candidates that still require role-fit or AI evidence.
5. Put only jobs that satisfy every hard constraint into `matching_jobs`. Preserve
   official text and use concise evidence rather than copying a full description.
6. If complete enumeration fails, use outcome `failed`, leave both arrays empty,
   set `current_job_count` to null, explain the failure, and add a specific issue.
   Do not submit partial matches from a failed source.

Use these outcome distinctions exactly:

- `success_matches`: complete enumeration, at least one current job, and at least
  one profile match;
- `success_no_matches`: complete enumeration found current jobs but no match;
- `success_zero_jobs`: complete enumeration found no current jobs; and
- `failed`: the source could not be completely and reliably enumerated.

## Profile interpretation

Use the selected profile without relaxing it. For
`ai-product-frontend-v1` in particular:

- the normalized job location must be San Francisco city, not merely the Bay
  Area or a nearby city;
- explicit `hybrid` or `on-site` is eligible, explicit `remote` is not, and a
  missing workplace arrangement is recorded as `unknown` and remains eligible;
- explicit full-time is eligible, a missing employment type remains eligible
  under the profile's assumption, and explicit part-time, contract, temporary,
  or internship work is excluded;
- the published annual USD base-salary maximum must be at least $200,000;
- title is a signal rather than a required match, but exact excluded titles remain
  excluded;
- seniority is recorded but does not decide eligibility; and
- role-relevant AI evidence must come from the official posting. Employer AI
  branding alone is insufficient.

## Identity and stale-state guards

- Copy the source record's exact `updated_date` into
  `expected_source_updated_date`.
- Read the source's current `last_successful_scan_at` from the selected job-result
  file's `source_states`. Copy it into `expected_last_successful_scan_at`, or use
  null when that source has never been successfully scanned under this profile.
- Set `scanned_at` to the actual completion time for that source. A later handoff
  must advance this per-source watermark.
- Do not copy saved job records into `matching_jobs` unless the current official
  source still shows them and they currently pass the profile.

## Handoff-only output

Do not edit any file under `data/`, `config/`, `schemas/`, `instructions/`, or
`.claude/`. Do not run `scripts/apply_handoff.py`, including with `--dry-run`.
Your only repository write is one new JSON handoff in `imports/`.

The envelope and each successful source scan follow this shape:

```json
{
  "schema_version": 1,
  "operation": "job-scan",
  "profile_id": "ai-product-frontend-v1",
  "created_at": "<UTC RFC 3339 timestamp>",
  "changes": [
    {
      "company_id": "example-company",
      "source_id": "example-company-greenhouse",
      "expected_source_updated_date": "<saved source updated_date>",
      "expected_last_successful_scan_at": null,
      "outcome": "success_matches",
      "scanned_at": "<UTC RFC 3339 timestamp>",
      "current_job_count": 1,
      "active_job_identities": [
        {
          "source_job_id": "12345",
          "canonical_url": "https://example.com/jobs/12345"
        }
      ],
      "matching_jobs": [
        {
          "company_id": "example-company",
          "source_id": "example-company-greenhouse",
          "source_job_id": "12345",
          "canonical_url": "https://example.com/jobs/12345",
          "title": "Senior AI Product Engineer",
          "location_text": "San Francisco, CA",
          "location": {
            "city": "San Francisco",
            "region": "CA",
            "country": "US"
          },
          "workplace_arrangement": "hybrid",
          "workplace_text": "Hybrid in San Francisco",
          "employment_type": null,
          "employment_text": null,
          "seniority": "senior",
          "compensation": {
            "minimum": 180000,
            "maximum": 230000,
            "currency": "USD",
            "basis": "base",
            "period": "year",
            "source_text": "$180,000-$230,000 base salary"
          },
          "posted_date": null,
          "evidence": {
            "active_status": "Present on the current official board.",
            "location": "San Francisco, CA",
            "workplace_arrangement": "Hybrid in San Francisco",
            "employment_type": null,
            "compensation": "$180,000-$230,000 base salary",
            "seniority": "Senior",
            "role_fit": "Build user-facing product features.",
            "ai_involvement": "Build LLM-powered product workflows."
          }
        }
      ],
      "failure_details": null
    }
  ],
  "issues": []
}
```

Write it to:

`/Users/natelipp/ai-code/_v3_daily-jobs/imports/<UTC compact timestamp>-job-scan-<profile_id>.json`

## Completion response

Report sources selected and completed; all four outcome counts; current postings
enumerated; qualifying jobs found; source failures with company and source IDs;
the absolute handoff path; and these commands with the actual path:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py "<actual-handoff-path>" --dry-run
.venv/bin/python scripts/apply_handoff.py "<actual-handoff-path>"
```

For every failed source, also print the literal refresh command with its real ID:

```text
/career-source-discover company <actual-company-id> --refresh
```

Do not apply the handoff yourself.
