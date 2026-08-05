# Daily Jobs

File-backed workflow for defining a job-search profile and finding verified,
current openings. It currently contains specifications and schemas, not a
standalone application.

## Source of truth

- `init-prompt.md`: agent behavior and commands
- `schemas/`: persisted data contracts
- `profiles/default.json`: active profile, once created
- `discoveries/`: broad candidate checkpoints and verification queues
- `sources/employers.json`: verified employer careers and ATS source registry
- `runs/`: completed searches and evidence

## Rules

- Prefer persisted files over chat memory.
- Require an approved profile before discovery or verification.
- Use hard constraints for eligibility and preferences for ranking.
- Match roles by approved role family plus explicit responsibility evidence; do not
  require an AI title or accept an AI employer alone unless the profile says so.
- Treat missing salary as unknown unless published salary is required.
- Use job boards for discovery and employer or ATS pages for verification.
- Run one initial active-job backfill, then incremental discovery updates.
- Preserve plausible unresolved leads as `needs_review` rather than excluding them.
- Keep address validation optional. A confirmed role city plus one documented
  company office in that city is sufficient as a clearly labeled map proxy.
- Keep prompts, schemas, examples, and documentation synchronized.
