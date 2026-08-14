# V3 Daily Jobs

This repository maintains a reusable catalog of San Francisco technology companies, reusable career-source instructions, and versioned job-search profiles. Job-result state is designed separately from those inputs.

## Operating rules

- `data/companies.json` is canonical state.
- `data/career_sources.json` is canonical reusable career-source state and references companies by `company_id`.
- Files under `config/search-profiles/` are versioned job-search inputs; agents must not silently relax them.
- Agents must not edit canonical data directly. Write a handoff in `imports/`, then apply it with `scripts/apply_handoff.py`.
- Keep discovery of companies separate from validation of physical addresses.
- Only `/company-validate` may mark an address `verified`, `outside_sf`, or `not_found`.
- `/company-ingest` may preserve supplied address details, but they remain `pending` until validation.
- Preserve existing non-null values. Report conflicts rather than guessing or overwriting.
- New companies are appended to the catalog; existing order is stable.
- Handoffs and catalog files must validate against the schemas in `schemas/`.
- Operations must be idempotent: replaying the same handoff must be a no-op.
- Successful completion summaries should be concise. When anything fails or remains unresolved, state exactly what failed, what state was left unchanged, and provide a literal copy-pastable remediation or retry command with real IDs and paths rather than placeholders.

## Commands

- `/company-ingest` — ingest supplied technology-company sources or data.
- `/company-validate` — validate physical addresses and coordinates, 10 pending companies by default.
- `/career-source-discover` — discover and verify reusable official job-listing sources for verified companies.
