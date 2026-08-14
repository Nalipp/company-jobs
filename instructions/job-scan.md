# Profile-based job scan

## Purpose

Poll reusable sources from `data/career_sources.json`, apply one exact versioned
profile from `config/search-profiles/`, and maintain that profile's matching job
state. A job scan does not discover companies and does not silently rewrite career
source instructions.

## Source outcomes

Report these outcomes separately for every eligible source:

1. Source enumeration succeeded and one or more current jobs were evaluated.
2. Source enumeration succeeded but the source contained zero current jobs.
3. Source enumeration succeeded but no current job matched the selected profile.
4. Source enumeration failed, was blocked, was incomplete, or no longer followed
   the saved instructions.

The first three are valid scan outcomes. The fourth is a career-source problem.
For a source problem, explain the specific failure, leave that company's saved job
state unchanged, and provide this command with the actual ID substituted:

```text
/career-source-discover <actual-company-id> --refresh
```

Never ask the human to remember or reconstruct a command. When several sources
fail, give one literal copy-pastable command per affected company.

## Profile application

- Use the selected profile exactly. Do not silently relax a hard constraint.
- Record the exact `profile_id` with the result state.
- A title match is only a signal when the profile says `title_policy` is
  `signal_only`. Establish role fit from the title or official responsibilities as
  permitted by `role_fit_policy`.
- Preserve official title, location text, employment text, compensation text, and
  canonical posting URL. Normalize only the fields governed by the profile and job
  schema.
- Missing employment type follows the profile's `missing_type_policy`; an explicit
  disallowed type never becomes full-time by assumption.
- AI evidence must come from the official job posting and meet the profile's
  `minimum_evidence`. Employer branding alone is insufficient when the profile
  says so.

## Stable identity and lifecycle

Job identity is scoped to a company and source. Prefer a source-provided job or
requisition ID. When none exists, require a stable job-specific canonical URL.
Never derive identity from title, location, salary, description, model judgment,
or a hash.

For each profile scan:

- an existing active job that still matches and is materially unchanged is left
  untouched;
- a newly matching job is appended;
- a materially changed job is updated only when canonical evidence changes a
  saved fact or its eligibility;
- a previously active job that disappears after successful complete source
  enumeration is retained and marked `inactive`;
- a source failure never removes, deactivates, or changes jobs.

Inactive jobs are retained as useful history. The default user view should show
all and only current active jobs matching the selected profile, while allowing a
future view to include historical inactive postings.
