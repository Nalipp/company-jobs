# Discovery runs

`/discover` saves broad, deduplicated job candidates here before detailed
verification. Files use the local date (`YYYY-MM-DD.json`) and a numeric suffix
when needed.

`/verify` reads the newest or selected discovery, processes one bounded batch of
queued candidates, and records their resulting verification status. Discovery
files conform to `../schemas/discovery-run.schema.json`.

The first discovery with `strategy: backfill` covers the configured lookback;
subsequent discoveries use `strategy: incremental`. Candidates whose canonical
page or date evidence remains unresolved use `needs_review` and can be retried
explicitly with `/verify [discovery] --retry-review`.
