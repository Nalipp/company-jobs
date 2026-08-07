# Validate Company Address

## Purpose

Locate a reasonable map address and coordinates for a deterministic batch of saved
companies. A saved qualifying job already establishes the target city; this
operation does not rerun job discovery or require proof of an exact worksite.

## Batch selection

An ordinary run selects the first 10 companies whose `map_location.status` is
`pending`, sorted by `discovered_at` and then `company_id`. Accept an explicit
limit from 1 through 25.

With an explicit retry request, select only `not_found` companies in the same
order. Never implicitly retry `not_found` or reprocess `located`. When nothing is
eligible, do not browse; return a handoff with empty changes and issues.

## Address procedure

For each selected company:

1. Confirm a saved job identifies the map location's city. Otherwise add a
   `city_anchor_missing` issue and emit no change.
2. Use an exact street address from an official saved job when readily available.
3. Otherwise search `"<company name>" "<city> office" address`. If needed, try at
   most two focused variants using the company domain.
4. Prefer an official company page, then a clearly associated Google business or
   Maps result, reputable directory, or official legal page.
5. Accept one clearly associated street address whose geocoded result is in the
   saved city and region. A missing suite is acceptable; a second source is not
   required.
6. Reject an address that belongs to another company, resolves outside the city,
   is only an obvious mailbox or registered agent, or remains materially
   ambiguous.

For a usable result, propose `located`, its normalized address and coordinates,
one source, checked time, and either `exact_worksite` or `company_office_proxy`.
After an accessible but unsuccessful search, propose `not_found`. If broad search
or geocoding failure prevents a real attempt, add an issue and emit no change.

## Change shape

Each `changes` item contains:

```json
{
  "company_id": "example-ai",
  "expected_status": "pending",
  "map_location": {}
}
```

Copy city, region, and country from the catalog. `expected_status` is the state the
agent selected: `pending` ordinarily or `not_found` in retry mode. This lets the
importer reject a stale transition without rejecting unrelated concurrent work.

Write the common handoff with operation `validate-company-address`. Do not copy
company or job records and never edit the canonical catalog.
