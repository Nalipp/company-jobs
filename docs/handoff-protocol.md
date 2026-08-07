# External-Agent Handoff Protocol

All three research commands produce the same small JSON envelope. Research agents
never edit `data/companies.json`; `scripts/apply_handoff.py` is the only component
that applies their proposed changes.

## Common envelope

Every run returns an object with exactly these top-level fields:

```json
{
  "schema_version": 1,
  "operation": "company-discover",
  "created_at": "2026-08-06T19:15:00Z",
  "changes": [],
  "issues": []
}
```

`operation` is one of `company-discover`, `validate-company-address`, or
`company-job-fetch`. `schemas/handoff.schema.json` selects the allowed shape of
each item in `changes` from that value.

`issues` contains research or identity problems that need attention. Each issue
has `code`, nullable `company_id`, nullable `subject`, nullable `source_url`, and
plain-language `details`. Issues are reported by the importer but never saved in
the company catalog.

An empty `changes` array is a successful no-op. The agent must still create valid
JSON. An empty `issues` array means no exception needs attention.

## Output path

When the agent has repository filesystem access, write one or more JSON-only
files under:

```text
/Users/natelipp/ai-code/_v2_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-<operation>.json
/Users/natelipp/ai-code/_v2_daily-jobs/imports/YYYYMMDDTHHMMSSmmmZ-<operation>-part-01.json
```

A bounded workflow may emit `-part-01`, `-part-02`, and so on. Each part is a
complete, independently importable handoff. Use UTC. Never overwrite an existing
file; add a numeric suffix when needed. The final response must list every exact
absolute path.

When the agent cannot write local files, return exactly one fenced JSON block and
no surrounding prose. Save that block under the same `imports/` directory before
applying it.

## Applying a handoff

From the repository root, run:

```text
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/apply_handoff.py /absolute/path/to/handoff.json
```

The importer validates the handoff and latest catalog, reapplies stable identity
rules, skips changes already present, rejects unsafe state transitions, validates
the result, and replaces the catalog atomically only when something changed.

The same handoff may be applied repeatedly. The first application may change the
catalog; later applications are no-ops. Handoffs from independent commands may be
researched in parallel and applied in any order because each contains only its own
small changes, never a stale catalog snapshot.

A parallel operation sees only the catalog that existed when it started. For
example, an address run will not include a company discovered by a simultaneous
discovery run; the next address invocation will select it. This is expected and
requires no coordination state.


## Agent completion response

When an agent can write repository files, its final response must contain:

- `Status: complete`, `Status: partial`, or `Status: no-op`;
- a brief summary of work performed, changes found, and issues;
- the exact absolute path of every handoff file; and
- a `Next step` with literal, copy-pastable commands using the actual paths, for
  example:

```text
cd /Users/natelipp/ai-code/_v2_daily-jobs
.venv/bin/python scripts/apply_handoff.py "/Users/natelipp/ai-code/_v2_daily-jobs/imports/20260807T120000000Z-company-discover-part-01.json"
```

Never use `<path>`, `FILE`, or another placeholder in a completion command. If no
file was written, say so explicitly and provide no importer command.

## Operation changes

- `company-discover`: each change is one complete new company record.
- `validate-company-address`: each change identifies `company_id`, the
  `expected_status` observed by the agent, and the proposed `map_location`.
- `company-job-fetch`: each change identifies `company_id` and one or more
  `new_jobs`.

The operation field is the only routing identifier. There is no run ledger, hash,
generated fallback identity, or second state store.
