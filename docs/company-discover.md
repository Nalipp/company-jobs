# Company Discover

## Purpose

Find companies absent from the catalog that currently offer at least one official
job satisfying every hard constraint in `config/search.json`. Canonical
verification happens during discovery; there is no later job-verification step.

## Inputs

Read `config/search.json`, `data/companies.json`, `docs/source-policy.md`,
`docs/data-contract.md`, `docs/handoff-protocol.md`, and the search, company, and
handoff schemas. Stop on malformed inputs rather than treating them as empty.

## Procedure

1. Build an exclusion set from saved ATS provider and board ID pairs, primary
   domains, and normalized jobs URLs.
2. Derive varied queries from configured titles, role families, AI responsibility
   signals, city, job boards, and ATS domains.
3. Treat search results and third-party boards only as leads. Resolve every
   included job to a source allowed by `docs/source-policy.md`.
4. On the canonical posting, verify active status, exact configured city, allowed
   workplace arrangement, published compensation threshold, employment type,
   title or family, seniority, and role-specific AI work.
5. Omit the job when any required fact is missing, ambiguous, third-party-only, or
   failing. Employer AI branding alone is not role-specific evidence.
6. After one role qualifies, inspect the official board and include every other
   current role that independently passes all gates.
7. Stop at the configured target of new companies. This is a stopping target, not
   a completeness claim.

## Research budget and checkpoints

Keep one invocation bounded so the result is not lost to context limits. Stop the
current pass after 12 web searches, 30 canonical fetches, or 3 newly qualifying
companies, whichever comes first. Do not attempt exhaustive coverage in one pass.

Write an independently valid checkpoint handoff after each 2 qualifying companies
or 8 search/fetch actions, whichever comes first. Name checkpoints with
`-part-01`, `-part-02`, and so on before `.json`; never overwrite a checkpoint. A
later invocation can continue from the catalog after those parts are applied.

If the budget is reached before the configured company target, mark the handoff as
partial in the final response and explain that another invocation can continue.

Preserve exact salary text and otherwise save concise, source-supported evidence.
Do not persist rejected leads, descriptions, raw HTML, scores, or reasoning.

## Change shape

Each `changes` item is a complete company object conforming to
`schemas/companies.schema.json#/$defs/company`. Initialize its configured
`map_location` as `pending` with every address-result field null.

Do not include companies already represented by any exact identity key. If the
identity is ambiguous, omit the company and add an issue.

Write the common handoff defined in `docs/handoff-protocol.md`, with operation
`company-discover`. The external agent never edits the catalog. An empty changes
array is a valid completed run.
