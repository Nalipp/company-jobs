# V3 Daily Jobs — Claude instructions

Read `AGENTS.md` before making changes. The canonical company catalog is `data/companies.json`.

Use the project commands for operational work:

- `/company-ingest <source or instructions>`
- `/company-validate [limit or retry instructions]`

Do not directly edit `data/companies.json`. Commands write a schema-valid JSON handoff to `imports/` and then apply it with:

```bash
.venv/bin/python scripts/apply_handoff.py /absolute/path/to/handoff.json
```

Every command must end with a brief status, what changed, the absolute handoff path, and the exact copy-pastable next command if automatic application did not succeed.

