# Verification runs

The agent saves one validated JSON file here for each completed `/verify` batch,
including batches initiated by `/fetch`. Files use the local date
(`YYYY-MM-DD.json`) and a numeric suffix when needed.

Search runs are generated artifacts and should conform to
`../schemas/search-run.schema.json`.

The existing 2026-08-03 files are historical schema-version-1 runs and retain the
legacy address fields they were created with. New verification runs use schema
version 2 and omit run-level address state.

`/validate-addresses` updates the relevant office in
`../catalog/companies/<company-id>.json`; it does not modify verification runs.
