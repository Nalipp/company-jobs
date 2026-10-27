# Company catalog migration plan

Status: completed on 2026-08-05. The contract, rollback snapshot, historical
catalog migration, collection helpers, active-agent documentation, and both
controlled workflow trials are complete. The rollback snapshot is under
`../legacy-old/2026-08-05-pre-catalog/`; command behavior remains governed by the
active agent specification.

## 1. Freeze the approved contract — completed

- Confirm the catalog-admission gate and planned slash commands.
- Confirm that posting text explicitly naming San Francisco is sufficient for
  initial admission and address verification remains a separate pass.
- Validate the company schema, profile schema, and examples.

## 2. Preserve the old workflow — completed

- Copy the pre-migration workflow into a clearly labeled, checksummed `legacy-old/`
  rollback snapshot without discarding evidence.
- Preserve active discoveries and runs as readable history while removing the old
  employer registry from active use; the snapshot remains the authoritative copy
  of that registry.
- Add a migration manifest recording and checksumming every archived path.
- Do not treat every legacy employer-source entry as a qualifying company.

## 3. Build company files from qualifying historical jobs — completed

- Re-evaluate historical canonical jobs against the current approved profile.
- Require every posting-level hard constraint before creating a company file.
- Merge qualifying jobs by ATS job ID and canonical URL.
- Import supported San Francisco address evidence when already available, but do
  not run new address searches during migration.
- Leave `address_verified` as `null` unless the existing evidence includes a
  supported address and coordinates inside San Francisco.
- Preserve rejected and ambiguous leads outside the company catalog as lightweight
  history so repeated work can be avoided.

## 4. Implement the two collection workflows — completed

- Add structured collectors for supported Ashby, Greenhouse, and Lever sources.
- Implement `/refresh` for known catalog companies.
- Implement `/discover` for broader company and job expansion.
- Send candidates from either workflow through the same `/verify` admission gate.
- Use IDs and deterministic hashes so unchanged jobs are not analyzed again.
- Add lightweight outcome memory, dry-run-first writes, offline fixtures/tests, and
  an operational runbook for future agents.

## 5. Update the active agent specification — completed

- Replace the legacy slash-command descriptions in `init-prompt.md` and `README.md`.
- Update `AGENTS.md`, schemas, examples, and history instructions together.
- Document `/companies`, `/refresh`, the new `/fetch` default, and
  `/validate-addresses`.

## 6. Validate with two trial runs — completed

- Completed one Ampersand known-company refresh on 2026-08-05. It backfilled a
  migrated full description, admitted one new qualifying job, excluded a platform
  variant, and produced a zero-candidate second poll with two unchanged jobs.
- Confirmed that only passing jobs entered the company file, unchanged jobs reused
  stored descriptions and evaluations, and address research did not run.
- Completed one limited catalog-expansion search on 2026-08-05. Four external leads
  were queued; two active Edison Scientific roles passed verification and entered
  one new company file, while LiteLLM failed the role-specific AI evidence rule and
  a stale Levelpath posting failed the activity and schedule checks.
- Confirmed that a second pass queued zero candidates: included jobs were found in
  the company catalog and excluded jobs were suppressed by candidate memory.
- Confirmed that expansion did not perform address research; the new company's San
  Francisco office retains nullable address, coordinate, and verification fields.
