# V3 Daily Jobs

V3 starts with one reusable asset: a catalog of technology companies associated with San Francisco. Job constraints, job records, ATS integrations, and job fetching are deliberately deferred.

## Data flow

1. `/company-ingest` reads company names from sources or data supplied with the command. It appends new companies and fills missing data on existing companies.
2. `/company-validate` selects 10 pending companies by default, researches a physical address, geocodes it, and records whether the company is in San Francisco.
3. Both commands write a JSON handoff in `imports/`. `scripts/apply_handoff.py` validates and applies that handoff to `data/companies.json` atomically and idempotently.

The source used merely to discover a company is not retained. An address source is retained because it supports the validation decision.

## Setup

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
claude
```

Then run, for example:

```text
/company-ingest https://www.builtin.com/companies/location/san-francisco
/company-validate
```

The command may apply its handoff automatically. If it cannot, it will print the exact `apply_handoff.py` command to copy and paste into a normal terminal.

## Address validation policy

A single credible source that clearly associates a physical office with the company is sufficient. Prefer official contact, about, privacy, terms, or careers pages; then use map/business listings or reputable reporting/directories. Missing suite numbers are acceptable. Virtual mailboxes, registered-agent addresses, and ambiguous matches are not.

Coordinates must come from a map or geocoder result and must correspond to the researched street address. The command records:

- `verified` when the physical address is in San Francisco;
- `outside_sf` when a credible physical office is found, but not in San Francisco;
- `not_found` after a bounded search finds no credible physical address;
- `pending` until validation is attempted.

## Manual handoff application

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py /absolute/path/to/handoff.json
```

Use `--dry-run` to see what would change without writing the catalog.

