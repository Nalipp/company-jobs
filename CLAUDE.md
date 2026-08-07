# Daily Jobs v2 Claude Instructions

This project has three research commands. Each command must read the latest local
configuration and catalog, perform only its documented research, and write one
self-identifying JSON handoff. Never edit `data/companies.json`.

Before executing a command, read `docs/handoff-protocol.md` and its matching file
under `work-statements/` completely. The work statement defines sources, selection,
change shape, no-op behavior, and the absolute output directory.

Always create valid JSON with exactly `schema_version`, `operation`, `created_at`,
`changes`, and `issues`. Use `schemas/handoff.schema.json`. Empty changes are valid.
Do not add prose to the file. After a successful local write, respond with the
absolute path so another agent can apply it deterministically.
