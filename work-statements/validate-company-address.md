# Work Statement: Validate Company Address

## Role and boundary

Act as the external address-research agent for Daily Jobs v2. Propose map-location
changes for a deterministic batch and produce one idempotent JSON handoff. Do not
edit `data/companies.json` and do not return a complete catalog snapshot.

Read completely before starting:

1. `data/companies.json`
2. `docs/validate-company-address.md`
3. `docs/data-contract.md`
4. `docs/handoff-protocol.md`
5. `schemas/companies.schema.json`
6. `schemas/handoff.schema.json`
7. `examples/company-address-located.example.json`

The example is fictional and supplies shape only.

## Selection

Unless command arguments say otherwise, select the first 10 `pending` companies,
ordered by `discovered_at` and then `company_id`. Accept a limit from 1 through 25.
Select `not_found` only when retry mode is explicit. Never select `located`.

If no company is eligible, do not browse. Return a valid envelope with empty
`changes` and `issues`.

## Research

For each selected company, confirm that a saved job anchors the map city. Prefer a
street address tied to an official saved job. Otherwise search `"<company name>"
"<city> office" address` and at most two focused variants.

Accept one clearly associated street address from an official company page, Google
business or Maps result, reputable directory, or official legal page. Geocode it
and confirm the saved city and region. A company office is an acceptable proxy; a
second source and exact-worksite proof are not required.

For a usable result, propose a complete `located` map object. After an accessible
but unsuccessful search, propose a complete `not_found` map object. When tool
failure prevents a real attempt, add an issue and do not emit a change.

Each change contains only `company_id`, the selected company's observed
`expected_status`, and the proposed complete `map_location`. Copy its city, region,
and country from the catalog. Do not copy or edit any other company or job data.

## Required output

Always produce JSON conforming to `schemas/handoff.schema.json`:

```json
{
  "schema_version": 1,
  "operation": "validate-company-address",
  "created_at": "ISO-8601 timestamp",
  "changes": [],
  "issues": []
}
```

When filesystem access exists, write JSON only to a new file named:

```text
/Users/natelipp/ai-code/_v2_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-validate-company-address.json
```

Use UTC and never overwrite an existing file; add `-2`, `-3`, and so on if needed.
Return the exact absolute path after writing. Do not edit the catalog.

When filesystem access does not exist, return exactly one fenced JSON block and no
other prose.
