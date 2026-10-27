# Phase 1 Migration Report

Checkpoint created: 2026-09-01 23:29:34 UTC

## Outcome

The V5 SQLite company/source foundation was migrated from a coherent read-only
V4 snapshot and passed the Phase 1 reconciliation gate.

- Database: `data/v5.sqlite`
- Database schema version: 1
- V4 canonical revision: 835
- Composite source digest:
  `8f952b19783fb86d7584a4d5f4fa2b6a14a10bd69850f36ec7ac91191bef59ae`
- SQLite integrity check: `ok`
- Foreign-key violations: 0
- Claim tokens retained in research payloads: 0
- Unexpected job tables: 0

## Reconciled data

| Category | V4 | V5 current |
|---|---:|---:|
| Companies | 3,036 | 3,036 |
| Verified | 947 | 947 |
| Outside SF | 438 | 438 |
| Not found | 1,636 | 1,636 |
| Pending | 15 | 15 |
| Career sources | 905 | 905 |
| Ready sources | 584 | 584 |
| Current accepted locations | — | 1,385 |
| Eligible SF companies | — | 947 |
| Collectible ready sources | — | 581 |

The three ready sources excluded from `collectible_sources` belong to companies
that are not currently SF-verified. This is the intended safety gate.

## Preserved research

- V3 archived company research records: 1,446
- Applied V4 company-result records: 834
- Total historical company research records: 2,280
- Applied V4 results recovered from terminal queue history because their original
  immutable result files were absent: 5
- Historical research identifiers not present in the canonical company set:
  `qualified` (preserved as an unresolved reference)

Historical research is evidence only. It did not create or modify an eligible
location.

## Explicit exclusions

- V4 job snapshots
- V4 job result envelopes
- V4 job queues and claims
- V4 profile verdicts

## Replay result

An immediate second import of the same composite source digest was a no-op. It
created no import-run row and changed no canonical database row.

## Preserved warnings

Ten duplicate website-domain groups and three duplicate provider/board identity
groups were preserved for later identity review. Phase 1 did not silently merge
them.

