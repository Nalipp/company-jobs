# Architecture

## One boundary for every operation

Daily Jobs v2 separates uncertain research from deterministic state mutation:

```text
current config + catalog
          |
          v
LLM web research and judgment
          |
          v
{ operation, changes, issues }
          |
          v
schema validation + deterministic importer
          |
          v
canonical company catalog
```

The LLM resolves sources and applies semantic constraints. It never edits the
catalog. The importer does no web research or model judgment; it validates,
deduplicates, checks transitions, and writes atomically.

## Canonical state

`config/search.json` is human-edited configuration. `data/companies.json` is the
only workflow state. Each company owns stable identity, one canonical jobs source,
one map location, and the qualifying jobs discovered so far.

`imports/*.json` are self-identifying handoffs, not a second state store. All
operations use `schemas/handoff.schema.json`:

- discovery proposes complete new company records;
- address validation proposes map-location transitions with an expected state;
- job fetch proposes qualifying unseen jobs grouped by saved company.

Issues remain in the handoff and importer report. They never enter the catalog.

## Idempotency and concurrency

Idempotency comes from existing business keys rather than run records:

- an already-known company is skipped;
- an address already equal to the proposal is skipped, while a changed unexpected
  state conflicts;
- a job already known by source ID or stable canonical URL is skipped.

Before every apply, the importer reloads the latest catalog. It writes only the
small changes in one handoff, so discovery, address, and job-fetch research may run
in parallel without exchanging complete catalog snapshots. Reapplying the same
handoff is safe.

## Intentional simplicity

There is no candidate queue, run ledger, generated fallback ID, content hash,
board snapshot, rejected-job memory, database, or scheduler. Add state only after
real use demonstrates a specific recurring problem.
