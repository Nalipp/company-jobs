# V4 Daily Jobs HTML results

This dependency-free static site is a read-only view of canonical V4 company and job data.

Regenerate `data.js` after job workers finalize new results:

```bash
cd /Users/natelipp/ai-code/_v4_daily-jobs
.venv/bin/python scripts/build-results-site
```

Serve the directory with any static web server, then open `index.html`. The site uses plain HTML, CSS, and JavaScript; it does not write to canonical or queue data.
