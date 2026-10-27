# Daily Jobs v2

This repository is a small, file-backed company and job workflow. Keep research
semantic and web-based; keep catalog mutation deterministic and local.

## Source of truth

- `config/search.json`: active hard constraints and discovery settings
- `data/companies.json`: canonical saved companies and qualifying jobs
- `schemas/search.schema.json`: search configuration contract
- `schemas/companies.schema.json`: canonical catalog contract
- `schemas/handoff.schema.json`: shared contract for all external-agent results
- `docs/handoff-protocol.md`: output, replay, and import rules
- `docs/company-discover.md`: company-discovery procedure
- `docs/validate-company-address.md`: address procedure
- `docs/company-job-fetch.md`: known-company job-fetch procedure
- `docs/source-policy.md`: source and evidence hierarchy
- `docs/data-contract.md`: identity and merge rules

Do not infer current behavior from chat history when these files are available.

## Command routing

- `/company-discover` and `$company-discover` use the repo-local
  `company-discover` skill.
- `/validate-company-address` and `$validate-company-address` use the repo-local
  `validate-company-address` skill.
- `/company-job-fetch` and `$company-job-fetch` use the repo-local
  `company-job-fetch` skill.

## Handoff boundary

All three research operations emit `schemas/handoff.schema.json` with exactly five
top-level fields: `schema_version`, `operation`, `created_at`, `changes`, and
`issues`.

An external research agent must never edit `data/companies.json`. It writes a
JSON-only handoff under `imports/` as specified by `docs/handoff-protocol.md`.
Only `scripts/apply_handoff.py` applies a handoff. It always reads the latest
catalog, validates both inputs, skips changes already present, rejects conflicts,
and writes atomically only when the catalog changes.

An empty `changes` array is a valid no-op. Issues are reported, not merged. A
handoff can be replayed, and independently researched handoffs can be applied in
any order.

## Discovery invariants

- Use search engines and job boards only to generate leads.
- Require an accessible employer, ATS, or qualifying primary employer-authored
  platform posting before admission.
- Add a company only after at least one active job passes every hard constraint.
- Apply constraints as gates, not preferences; unknown required facts fail.
- Employer AI focus alone does not prove role-specific AI work.
- Discovery initializes `map_location.status` as `pending` and leaves address
  result fields null.

## Address invariants

- An ordinary run selects the next 10 `pending` companies by `discovered_at` and
  `company_id`; an explicit limit may be 1 through 25.
- Retry `not_found` only when explicitly requested. Never reprocess `located`.
- A reasonable company office in the saved job city is an acceptable map proxy.
- One credible address source plus a geocoder result in that city is sufficient.
- A completed unsuccessful lookup proposes `not_found`; broad tool failure emits
  an issue and no change.
- Address changes carry `expected_status` so stale handoffs conflict safely.

## Job-fetch invariants

- Poll only saved canonical job sources; do not discover unknown companies.
- Identify a posting by its source job ID, or a stable job-specific canonical URL
  when no source ID exists.
- Never generate job identity from content, title, date, salary, or a hash.
- Do not emit or overwrite known jobs. Apply current hard constraints only to
  unseen jobs and propose only passing jobs.
- Put missing or conflicting identity and source failures in `issues`.
- Do not remove or mark saved jobs inactive in this version.

## Scope control

Do not add candidate queues, run ledgers, hashes, snapshots, migrations,
schedulers, or helper commands without a demonstrated need and explicit approval.
