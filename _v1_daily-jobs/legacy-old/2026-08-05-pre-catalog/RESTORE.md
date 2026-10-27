# Pre-catalog legacy snapshot

This directory is a byte-for-byte snapshot of the Daily Jobs workflow immediately
before the company-catalog migration was activated on 2026-08-05.

It contains the governing prompts and documentation, approved profile, schemas,
employer-source registry, discovery checkpoints, verification runs, and research
notes. Files in this directory are archival and must not be edited.

`manifest.sha256` records the SHA-256 checksum of every archived file except the
manifest itself. Verify the snapshot from the repository root with:

```text
shasum -a 256 -c legacy-old/2026-08-05-pre-catalog/manifest.sha256
```

To roll back, copy the archived top-level files and directories back to their
original repository paths only after preserving any post-migration work separately.
The catalog can then be removed from active use without losing the legacy evidence.
