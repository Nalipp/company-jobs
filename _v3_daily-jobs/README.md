# V3 Daily Jobs

V3 separates stable San Francisco company identity, reusable career-source access, versioned search constraints, and profile-specific job-result state.

## Data flow

1. `/company-ingest` reads company names from sources or data supplied with the command. It appends new companies and fills missing data on existing companies.
2. `/company-validate` selects 50 pending companies by default, researches a physical address, geocodes it, and records whether the company is in San Francisco.
3. `/career-source-discover` records reusable official career and job-listing sources for verified companies. It does not collect jobs.
4. `/job-scan` enumerates ready career sources, applies one exact search profile, and maintains that profile's active and inactive jobs.
5. Every command writes a JSON handoff in `imports/`. `scripts/apply_handoff.py` validates and applies the handoff to the appropriate canonical state file, atomically for that file and idempotently.

The source used merely to discover a company is not retained. An address source is retained because it supports the validation decision.

## Career and search state

- `data/companies.json` contains company identity and physical-location state.
- `data/career_sources.json` is the separate registry for reusable official careers and job-listing access instructions.
- `config/search-profiles/` contains versioned role constraints. The first profile is `ai-product-frontend-v1`.
- `data/job-results/` contains one canonical result file per profile so the same career sources can be evaluated under different constraints.

See `instructions/career-source-discovery.md` and `instructions/job-scan.md` for the operational contracts.

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
/job-scan ai-product-frontend-v1
```

External research commands write a handoff and stop. They print exact dry-run and apply commands for Codex or the user.

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
.venv/bin/python scripts/apply_handoff.py imports/20260828T200727Z-company-validate.json
```

When a command creates a handoff, its completion response must include these
two literal lines with that run's actual filename; never leave a placeholder.

Use `--dry-run` to see what would change without writing the catalog.
