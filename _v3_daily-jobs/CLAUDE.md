# V3 Daily Jobs — Claude instructions

Read `AGENTS.md` before making changes. The canonical company catalog is `data/companies.json`; reusable career sources belong in `data/career_sources.json`; versioned search constraints belong under `config/search-profiles/`; profile-specific job state belongs under `data/job-results/`.

Use the project commands for operational work:

- `/company-ingest <source or instructions>`
- `/company-validate [limit or retry instructions]`
- `/career-source-discover [limit or company selectors]`
- `/job-scan <profile_id> [source selectors]`

Do not directly edit `data/companies.json`, `data/career_sources.json`, or files
under `data/job-results/`. External
research runs write one schema-valid JSON handoff to `imports/` and stop. Codex
reviews and applies it with:

```bash
cd /Users/natelipp/ai-code/_v3_daily-jobs
.venv/bin/python scripts/apply_handoff.py imports/20260828T200727Z-company-validate.json
```

Every command that writes a handoff must end with a brief status, what was found,
the absolute handoff path, and a fenced bash block containing the two literal
apply lines with the actual generated filename substituted. Do not leave
placeholders, and do not apply the handoff yourself.

Keep routine success output concise. For every access failure, unsupported source,
ambiguous identity, unresolved record, or partial result:

- say exactly what failed and which company or source was affected;
- say what canonical state was deliberately left unchanged;
- give the human a literal copy-pastable remediation or retry command using the
  actual company ID and file path;
- provide one command per affected company when actions differ; and
- never use `<company-id>`, `<path>`, `FILE`, or another placeholder in a final
  completion response.
