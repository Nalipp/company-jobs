# V5 Daily Jobs — Data Foundation

V5 is a local-first data system for verified San Francisco technology companies
and their complete job postings. Phase 1 established the SQLite company/source
foundation and migrated reusable V4 research. V4 source instructions are in
`career_sources.instructions`; V4's profile-filtered job history is excluded.
The collector saves unfiltered postings from currently eligible sources.

The working database is local. There is no database server, web server, MCP
server, JSON product export, or UI yet.

## Phase 1 data flow

```text
V4 companies.json + company research + applied company results
                              |
                              v
                 read-only, validated importer
                              |
                              v
                      data/v5.sqlite
```

Current accepted company facts and career sources are normalized into tables.
Historical research is retained losslessly as JSON payloads but cannot establish
collection eligibility.

## Commands

Create the local development environment once:

```bash
cd /Users/natelipp/ai-code/_v5_daily-jobs
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

The operational CLI itself has no third-party runtime dependencies and can also
run directly with `python3` from the repository checkout.

Preview the current V4 migration without creating or changing a database:

```bash
cd /Users/natelipp/ai-code/_v5_daily-jobs
python3 scripts/v5-data import-v4 \
  --v4-root ../_v4_daily-jobs \
  --database data/v5.sqlite \
  --dry-run
```

Apply the migration:

```bash
python3 scripts/v5-data import-v4 \
  --v4-root ../_v4_daily-jobs \
  --database data/v5.sqlite
```

Verify the database against a fresh coherent read of V4:

```bash
python3 scripts/v5-data verify \
  --v4-root ../_v4_daily-jobs \
  --database data/v5.sqlite
```

Add `--json` to any command for machine-readable output.

## Collect jobs

In a Codex task using Luna, enter one command:

```text
$collect-jobs --run luna
```

The [repository skill](.agents/skills/collect-jobs/SKILL.md) syncs any newer V4
company/source facts, runs deterministic Ashby/Greenhouse/Lever scans in local
batches, then handles due browser sources one at a time. Its internal helper is
`python3 scripts/collect-jobs`; ordinary users do not need to choose lanes or
run an enqueue command. A run ends when no sources are due or a system failure
requires attention. Running it again later picks up sources due for refresh.

To inspect progress without collecting anything:

```bash
python3 scripts/collect-jobs status
```

Collection creates `job_scans` records and stores full posting captures in
`jobs`. A complete scan can mark vanished jobs inactive; an incomplete scan
preserves them. Browser recipes proven during a complete scan are stored in
`source_collection_recipes` for reuse. Job-to-office matching and user profile
filters remain future work.

## Important semantics

- `eligible_companies` contains only companies with a verified physical San
  Francisco location and coordinates.
- `collectible_sources` contains only ready sources belonging to those eligible
  companies.
- A V4 `outside_sf` location is retained but never makes a company eligible.
- A pending address is retained as a candidate, not accepted as a workplace.
- A V4 `not_found` company has no accepted current location.
- Multiple locations are supported. At most one may be the primary map location.
- Job-specific workplace review and location-to-office matching are still
  deferred; a collected job is not automatically eligible for a map pin.

See `V5_DATA_MODEL.md` for the complete Phase 1 contract and acceptance gate.
See `SQL_INSPECTION.md` for terminal queries and a sample row from every table.
