---
name: company-job-fetch
description: Poll saved Daily Jobs v2 company sources for qualifying unseen jobs, or validate and apply a supplied job-fetch handoff. Use when the user invokes /company-job-fetch or $company-job-fetch, asks to refresh known company jobs, or supplies external company-job-fetch JSON.
---

# Company Job Fetch

Resolve the repository root from this skill. Read `../../../AGENTS.md`,
`../../../docs/handoff-protocol.md`, and
`../../../work-statements/company-job-fetch.md` completely.

If the user supplies a handoff, do not browse. Apply it with
`../../../scripts/apply_handoff.py` and report the importer's result.

Otherwise execute the work statement, save the shared JSON handoff under
`../../../imports/`, and apply that saved file with the same importer unless the
user explicitly asks for handoff-only output. Never mutate the catalog directly.
Preserve issues and conflicts in the report rather than guessing.
