# V3 Daily Jobs

V3 separates stable San Francisco company identity, reusable career-source access, versioned search constraints, and job-result state. The company catalog is populated and address-validated; career-source discovery and profile-based job scanning are the next operational layers.

## Data flow

1. `/company-ingest` reads company names from sources or data supplied with the command. It appends new companies and fills missing data on existing companies.
2. `/company-validate` selects 10 pending companies by default, researches a physical address, geocodes it, and records whether the company is in San Francisco.
3. `/career-source-discover` records reusable official career and job-listing sources for verified companies. It does not collect jobs.
4. Every command writes a JSON handoff in `imports/`. `scripts/apply_handoff.py` validates and applies company handoffs to `data/companies.json` and career-source handoffs to `data/career_sources.json`, atomically for the target file and idempotently.

The source used merely to discover a company is not retained. An address source is retained because it supports the validation decision.

## Career and search state

- `data/companies.json` contains company identity and physical-location state.
- `data/career_sources.json` is the separate registry for reusable official careers and job-listing access instructions.
- `config/search-profiles/` contains versioned role constraints. The first profile is `ai-product-frontend-v1`.
- Job-result state will remain separate so one career source can be evaluated under several profiles without copying source instructions into each result.

See `instructions/career-source-discovery.md` and `instructions/job-scan.md` for the current operational contracts. Career-source discovery is ready to run; the job-result schema and job-scan importer are the next implementation layer.

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
/career-source-discover
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
