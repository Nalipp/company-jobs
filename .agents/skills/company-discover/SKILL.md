---
name: company-discover
description: Discover previously unknown companies through current jobs that pass every Daily Jobs v2 hard constraint, or validate and apply a supplied company-discovery handoff. Use when the user invokes /company-discover or $company-discover, asks to find new qualifying companies, or supplies an external company-discover JSON result.
---

# Company Discover

Resolve the repository root from this skill. Read `../../../AGENTS.md`,
`../../../docs/handoff-protocol.md`, and
`../../../work-statements/company-discover.md` completely.

If the user supplies a handoff, do not browse. Apply it with
`../../../scripts/apply_handoff.py` and report the importer's result.

Otherwise execute the work statement, save the shared JSON handoff under
`../../../imports/`, and apply that saved file with the same importer unless the
user explicitly asks for handoff-only output. Never mutate the catalog directly.
Preserve issues and conflicts in the report rather than guessing.
