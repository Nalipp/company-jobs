# Daily Jobs

File-backed workflow for defining a job-search profile and finding verified,
current openings. It currently contains specifications and schemas, not a
standalone application.

## Source of truth

- `init-prompt.md`: agent behavior and commands
- `RUNBOOK.md`: safe operational procedure and local helper commands
- `schemas/`: persisted data contracts
- `profiles/default.json`: active profile, once created
- `discoveries/`: broad-discovery and known-company refresh checkpoints
- `catalog/companies/`: active qualifying-company and job memory
- `catalog/candidate-memory.json`: lightweight cross-run identity/hash/outcome cache
- `catalog/migration-report.json`: initial migration audit and exclusions
- `legacy-old/2026-08-05-pre-catalog/`: checksummed rollback snapshot
- `legacy-old/2026-08-05-pre-catalog/sources/employers.json`: archived source registry
- `catalog/README.md`: active company-catalog behavior
- `schemas/company-catalog.schema.json`: contract for one persistent company file
- `runs/`: completed searches and evidence

## Rules

- Before executing a Daily Jobs command, read `init-prompt.md`, then load the active
  profile, `RUNBOOK.md`, and the relevant persisted catalog, discovery, or run files.
  Do not infer current behavior from chat history.
- Prefer persisted files over chat memory.
- Require an approved profile before discovery or verification.
- Use hard constraints for eligibility and preferences for ranking.
- Match roles by approved role family plus explicit responsibility evidence; do not
  require an AI title or accept an AI employer alone unless the profile says so.
- Apply the profile's explicit compensation comparison and unknown-salary policy.
- Use job boards for discovery and employer or ATS pages for verification.
- Run one initial active-job backfill, then incremental discovery updates.
- Preserve plausible unresolved leads as `needs_review` rather than excluding them.
- Use the local helpers in dry-run mode before `--write`; validate all generated
  artifacts and never treat a failed or unsupported source as an empty feed.
- Keep address validation optional. A confirmed role city plus one documented
  company office in that city is sufficient as a clearly labeled map proxy.
- Do not enumerate or backfill a company's offices outside the active profile's
  approved job locations. Multiple-office support is not a completeness target.
- In the company catalog, create a company file only after at least one
  canonical job passes every posting-level hard constraint. Address and coordinate
  validation remain a separate optional pass.
- Keep prompts, schemas, examples, and documentation synchronized.
