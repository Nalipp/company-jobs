# Daily Jobs

File-backed workflow for defining a job-search profile and finding verified,
current openings. It currently contains specifications and schemas, not a
standalone application.

## Source of truth

- `init-prompt.md`: agent behavior and commands
- `schemas/`: persisted data contracts
- `profiles/default.json`: active profile, once created
- `runs/`: completed searches and evidence

## Rules

- Prefer persisted files over chat memory.
- Require an approved profile before `/fetch`.
- Use hard constraints for eligibility and preferences for ranking.
- Treat missing salary as unknown unless published salary is required.
- Prefer employer or ATS pages as canonical sources; retain evidence URLs.
- Keep prompts, schemas, examples, and documentation synchronized.
