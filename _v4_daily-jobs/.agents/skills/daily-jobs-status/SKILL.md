---
name: daily-jobs-status
description: Produce the deterministic read-only V4 Daily Jobs progress summary from local canonical and queue JSON. Use for `$daily-jobs-status` when the user wants queue counts, company/source coverage, job scan coverage, attention items, integrity warnings, or recent activity without enqueueing, recovering, researching, or changing data.
---

# Daily Jobs Status

From `/Users/natelipp/ai-code/_v4_daily-jobs`, run:

```bash
.venv/bin/python scripts/v4-queue status
```

Return the command output without performing additional research or recounting data yourself. The helper takes shared locks, validates inputs, and is byte-for-byte read-only. It reports queue/model/status counts, company address statuses, career-source readiness/providers, job snapshot coverage, qualifying/unresolved jobs, attention items, integrity warnings, and recent activity.

If the command exits nonzero, report the invalid/unavailable section and error. Do not attempt recovery, audit/enqueue, or JSON edits as part of this skill.
