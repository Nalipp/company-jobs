# V5 Data Model and Phase 1 Contract

Phase 1 was approved on 2026-09-01 and passed its acceptance gate. Sections 8
and 9 document later, separately approved job-schema and collection work.

## 1. Scope

Phase 1 creates a local SQLite database containing company identity, company
locations, location evidence, historical company research, and career sources.
It imports those facts from V4 through a read-only, replay-safe process.

Explicitly deferred during Phase 1 (see Sections 8–9 for later approvals):

- job fetches, jobs, versions, classifications, and profile verdicts;
- company discovery and new address-validation workflows;
- MCP, JSON product exports, HTML, CSS, JavaScript, and maps;
- distribution and licensing decisions for a future data product.

## 2. Collection and map semantics

Company eligibility and job map eligibility are distinct.

Company eligibility is known in Phase 1. A company must have a current,
verified, physical San Francisco location with latitude and longitude.

Job map eligibility is deferred. A later job version must indicate on-site or
hybrid work and be associated with exactly one verified company location before
the product places it on the map. A company address alone does not assert where
every employee works.

Multiple company locations are allowed. At most one is the primary map location.
The current V4 canonical SF address becomes that initial primary location. A
future job may select a different verified location when its posting provides
better evidence.

## 3. Tables

### `schema_migrations`

Records applied SQL migration version, name, checksum, and timestamp. An applied
migration whose file checksum changes is an error.

### `import_runs`

Records one successfully committed V4 snapshot import, including canonical and
research digests and the reconciliation report. The composite source digest is
unique. Re-importing it returns the stored report without writing.

### `companies`

One row per stable V4 company ID. `sf_validation_status` preserves the current
canonical result: `pending`, `verified`, `outside_sf`, or `not_found`.

Website domains are intentionally not unique. V4 contains known duplicate-domain
groups that must be preserved for later identity review rather than silently
merged.

### `company_locations`

Stores multiple physical or candidate addresses per company. A deterministic ID
is derived from company ID and normalized address. Current V4 mappings are:

| V4 status | Location mapping |
|---|---|
| `verified` | verified, physical, inside SF, primary |
| `outside_sf` | verified, physical, outside SF, not primary |
| `pending` with an address | candidate, physical status unknown |
| `not_found` | no current location row |

Older imported locations remain as `stale` history when a later V4 snapshot
changes the canonical address.

### `company_location_evidence`

Stores structured evidence tied to a current or historical location. Canonical
V4 address source URLs and labels become supporting evidence. Evidence alone can
never change eligibility.

### `company_research_records`

Losslessly retains:

1. the V3 research archive carried by V4; and
2. applied V4 company-result envelopes after removing claim tokens.

`referenced_company_id` preserves the original identifier even when it does not
exist in the canonical company set. `company_id` is a nullable validated foreign
key for known companies.

### `career_sources`

One row per V4 source ID, including provider, board ID, official relationship,
capabilities, reusable instructions, and status. Known duplicate provider/board
identities are reported but not merged during migration.

## 4. Authoritative views

`eligible_company_locations` implements the location eligibility predicate.

`eligible_companies` returns one row per current company with at least one
eligible location.

`collectible_sources` returns current `ready` sources belonging to eligible
companies. Future collection code must use this view so a newly validated
company is picked up automatically on the next run.

## 5. Import protocol

1. Acquire a shared lock on V4 `data/companies.lock`.
2. Read one coherent byte snapshot of `data/companies.json`.
3. Release the lock and validate company/source ownership and address invariants.
4. Read the immutable V3 research archive preserved by V4.
5. Match every V4 company application-ledger digest to an immutable company
   result file and sanitize its claim token.
6. Calculate a composite source digest.
7. For dry-run, calculate mappings and stop without creating a database.
8. For import, use one `BEGIN IMMEDIATE` SQLite transaction.
9. Upsert current company/source facts, reconcile locations, and append evidence
   and research.
10. Run reconciliation and foreign-key checks before commit.

No importer path writes anywhere under V4.

## 6. Idempotence and incremental import

An already completed composite source digest is a strict no-op: no import-run
row, timestamp, or canonical row changes.

A newer V4 digest creates one new import run. Current facts are upserted, missing
older V4 locations become stale, and append-only research remains preserved.

## 7. Phase 1 acceptance gate

Phase 1 passes only when:

1. V4 and SQLite current company/source counts and status splits match.
2. Every V4 verified company has an eligible migrated SF location.
3. No pending, outside-SF, or not-found company is eligible.
4. Only ready sources belonging to eligible companies are collectible.
5. Current canonical evidence and historical company research reconcile.
6. No V4 job history or job tables exist.
7. A second identical import changes zero rows.
8. Invalid input rolls back without an import-run record.
9. `PRAGMA foreign_key_check` and `PRAGMA integrity_check` pass.

## 8. Approved next step: empty job inventory schema

The Phase 1 acceptance gate passed. The user subsequently approved an empty
`jobs` table and reuse of V4 career-source instructions. Migration 0002 adds
that table without importing V4 job snapshots, results, queues, or profile
verdicts. `career_sources.instructions` remains the one current instruction
field, with source URL, provider, board ID, access mode, scope, capability
flags, and official-relationship evidence in the same row.

Each future job row is attached to one career source, with a unique
source-scoped key, canonical URL, title, optional description and posting
facts, source revision, and first/last seen lifecycle fields. The original
posting payload may be retained as JSON. Insertions and source changes must
pass the existing `collectible_sources` view. Jobs are unfiltered by profile.
The table has no company-location assignment: a future job-specific workplace
step must establish the office before any map pin is displayed.

The job collector still needs to: select `collectible_sources`; run provider
adapters or follow source-specific browser instructions; enumerate every
posting; normalize and upsert stable jobs; and record whether a scan was
complete before inactivating jobs that disappeared. Those processes are not
implemented by migration 0002.

## 9. Approved collection phase

The user approved unfiltered collection after the empty job schema was created.
Migration 0003 adds a scan ledger and a V5 recipe overlay. The single Codex
entry point is `$collect-jobs --run luna`; the local `scripts/collect-jobs`
helper handles deterministic scans, SQL claims, submission, and status.

`job_scans` provides both work claims and history, so no separate queue or
enqueue phase is needed. Claims have leases; an expired claim is abandoned
before another scan can claim its source. The finalizer checks the claim,
source revision, and `collectible_sources` membership inside its write
transaction. Complete, incomplete, and blocked outcomes have retry times.

`source_collection_recipes` stores a tested browser method for one source and
the source revision against which it was proven. It does not duplicate or
replace the V4-imported `career_sources.instructions`. A source revision change
makes its recipe unavailable until it is revalidated.

Each `jobs` row stores normalized details plus the captured original posting.
An observed job is upserted under a stable source-scoped identity. Only a
complete scan may mark a previously active job inactive. Job location evidence
is retained, but no job is mapped to an office until later workplace review.
