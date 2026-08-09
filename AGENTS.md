# V3 Daily Jobs

This repository maintains a reusable catalog of San Francisco technology companies. It intentionally does not contain job-search constraints or job records yet.

## Operating rules

- `data/companies.json` is canonical state.
- Agents must not edit the catalog directly. Write a handoff in `imports/`, then apply it with `scripts/apply_handoff.py`.
- Keep discovery of companies separate from validation of physical addresses.
- Only `/company-validate` may mark an address `verified`, `outside_sf`, or `not_found`.
- `/company-ingest` may preserve supplied address details, but they remain `pending` until validation.
- Preserve existing non-null values. Report conflicts rather than guessing or overwriting.
- New companies are appended to the catalog; existing order is stable.
- Handoffs and catalog files must validate against the schemas in `schemas/`.
- Operations must be idempotent: replaying the same handoff must be a no-op.

## Commands

- `/company-ingest` — ingest supplied technology-company sources or data.
- `/company-validate` — validate physical addresses and coordinates, 10 pending companies by default.

