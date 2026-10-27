# Verification runs

The agent saves one validated JSON file here for each completed `/verify` batch,
including batches initiated by `/fetch`. Files use the local date
(`YYYY-MM-DD.json`) and a numeric suffix when needed.

Search runs are generated artifacts and should conform to
`../schemas/search-run.schema.json`.

`/validate-addresses` may update a selected run in place with normalized
addresses, coordinates, evidence, and map-eligibility states. The command is
optional and preserves the run's original search evidence and ranking.
