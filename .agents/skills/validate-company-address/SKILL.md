---
name: validate-company-address
description: Locate map-ready addresses for a deterministic batch of Daily Jobs v2 companies, or validate and apply a supplied address handoff. Use when the user invokes /validate-company-address or $validate-company-address, asks for the next address batch, requests an explicit not-found retry, or supplies external validate-company-address JSON.
---

# Validate Company Address

Resolve the repository root from this skill. Read `../../../AGENTS.md`,
`../../../docs/handoff-protocol.md`, and
`../../../work-statements/validate-company-address.md` completely.

If the user supplies a handoff, do not browse. Apply it with
`../../../scripts/apply_handoff.py` and report the importer's result.

Otherwise execute the work statement, save the shared JSON handoff under
`../../../imports/`, and apply that saved file with the same importer unless the
user explicitly asks for handoff-only output. Never mutate the catalog directly.
Preserve issues and conflicts in the report rather than guessing.
