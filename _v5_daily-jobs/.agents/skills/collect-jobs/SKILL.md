---
name: collect-jobs
description: Collect unfiltered jobs for verified San Francisco companies into V5 SQLite. Use for `$collect-jobs --run luna`; structured sources run locally and browser sources are handled one at a time with durable SQL claims.
---

# Collect Jobs

Run from `/Users/natelipp/ai-code/_v5_daily-jobs`. The normal Codex invocation
is `$collect-jobs --run luna` (also respond to `collect-jobs --run luna`). The
model is chosen in the Codex UI; the word `luna` in the command is a worker label,
not a model switch. Keep V4 read-only.

## Start and continue

For a run, create one persisted goal to drain due sources or stop for a system
failure. Reuse the same worker label and goal on continuation. Do not set a token
budget or stop merely because the source count is large. Claim only the source
you will process immediately. A run stops when no sources are due, the network
environment is unavailable, or the current claim cannot be finalized safely.

At the start, sync current V4 company/source facts, then inspect due work:

```bash
python3 scripts/v5-data import-v4 --database data/v5.sqlite --v4-root ../_v4_daily-jobs
python3 scripts/collect-jobs status
```

The import never imports V4 jobs. The SQL `collectible_sources` view is the only
source of eligible work; do not duplicate its SF predicate or scan a source
outside it.

Run due Ashby, Greenhouse, and Lever feeds in local batches. The helper fetches
and saves every listed posting without profile filters. Repeat batches until
`no_due_structured_sources`; stop the worker on `environment_network`.
These feeds require outbound HTTPS. If the local sandbox restricts network
access, request the normal command permission before starting the batch so a
sandbox denial is not mistaken for a broken company source.

```bash
python3 scripts/collect-jobs run-structured --limit 25 --claimed-by WORKER_ID
```

Then claim browser sources one by one:

```bash
python3 scripts/collect-jobs claim-next --mode manual --claimed-by WORKER_ID
```

`no_task` ends the run. A claim contains one source, its saved V4 instructions,
any tested V5 recipe, previous scan notes, a claim token, and a lease. Keep the
token out of chat. Renew a long investigation before the lease expires:

```bash
python3 scripts/collect-jobs renew --scan-id SCAN_ID --claim-token CLAIM_TOKEN
```

Follow the official source instructions and reuse a saved recipe when its source
revision matches. Prefer an official feed or embedded page data where available;
otherwise exhaust listing pagination/lazy loading and open every stable detail
URL. Preserve every public role, including remote and non-matching roles.
Capture the full posting text and useful raw fields for later classification.

## Submit one browser scan

Write one result to a unique `data/manual-results/SCAN_UUID.json` file with
`apply_patch`, then
run `python3 scripts/collect-jobs submit --result ABSOLUTE_PATH`. A result has
this shape (replace every example value with observed facts):

```json
{
  "scan_id": "scan:...",
  "claim_token": "...",
  "complete": true,
  "completeness_evidence": "The official listing ended after page 3; all 24 detail links were opened.",
  "identity_evidence": "Each detail URL contains a stable requisition ID.",
  "summary": {"pages": 3, "listing_count": 24},
  "notes": "Official careers site checked.",
  "recipe": {"method": "browser", "listing_url": "https://example.com/careers", "pagination": "Next link until absent", "job_id": "Detail URL requisition ID"},
  "jobs": [{
    "source_job_id": "REQ-123",
    "canonical_url": "https://example.com/jobs/REQ-123",
    "title": "Software Engineer",
    "description_text": "Full posting text...",
    "locations": ["San Francisco, CA"],
    "workplace_text": "Hybrid",
    "employment_text": "Full time",
    "compensation_text": "$180,000-$240,000",
    "published_at": null,
    "details": {"department": "Engineering", "team": "Product"},
    "raw_posting": {"url": "https://example.com/jobs/REQ-123", "captured_text": "Full original posting text..."}
  }]
}
```

Use `complete: true` only when enumeration is proven and the observed set has
stable job identities. An official zero-job board may submit an empty `jobs`
array with explicit completeness evidence. If only some jobs can be observed,
submit them with `complete: false`, explain the gap in `notes`, and set
`retry_after_hours: 168` for a structural limit that will not resolve soon.
An incomplete scan saves observed jobs but never closes unseen prior jobs.
Only a complete scan may store a reusable recipe as validated.

If access fails before any trustworthy capture, record the exact source failure:

```bash
python3 scripts/collect-jobs block --scan-id SCAN_ID --claim-token CLAIM_TOKEN --error-class source_access --notes "Reason"
```

For a local DNS/network-permission failure use `environment_network`, record it
once, and stop the whole worker. Do not treat a large or difficult source as a
network failure. If submission validation fails, repair the same result while
the lease is active; do not claim another source. At the end, report compact
source/job totals, blocked/incomplete counts, and the stop reason, then complete
the persisted goal.
