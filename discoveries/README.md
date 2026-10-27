# Discovery runs

`/discover` saves broad, deduplicated job candidates here before detailed
verification. `/refresh` uses the same checkpoint format for new or changed jobs
from known catalog companies. Files use the local date (`YYYY-MM-DD.json`) and a
numeric suffix when needed.

`/verify` reads the newest or selected discovery, processes one bounded batch of
queued candidates, and records their resulting verification status. Discovery
files conform to `../schemas/discovery-run.schema.json`.

Historical files use schema version 1. New files use schema version 2 and identify
their `collection_mode` as `known_company_refresh` or `catalog_expansion`; this lets
`/history` distinguish the two workflows without guessing from query text.

The first broad discovery with `strategy: backfill` covers the configured lookback;
subsequent broad discoveries and known-company refreshes use `strategy:
incremental`. Record the actual sources and checks in `query_matrix` and `sources`.
Candidates whose canonical page or date evidence remains unresolved use
`needs_review` and can be retried explicitly with
`/verify [discovery] --retry-review`.
