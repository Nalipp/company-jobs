# Job matching filters

`explore_jobs.py` evaluates captured postings with Jev and caches each answer in
`jev_cache.sqlite`. It reads the V5 jobs database without modifying it.

## New filters

| Tab | Feature key | Match |
| --- | --- | --- |
| Research UX/UI | `job.work.research_ux_ui` | Software development combined with user/product discovery, user research, usability testing, or experimental software prototypes used to validate ideas. |
| Education | `job.domain.education` | A team, product, or substantial responsibility centered on teaching, learning, or skill development, including education labs. |

Both tabs use a score threshold of `0.80` and start unselected. Education is
independent of job function; select Software eng as well to restrict it to
engineering roles. Research UX/UI already requires software development.
The definitions exclude incidental degree requirements and academic credentials;
Education also excludes tuition benefits, onboarding, and routine internal
training. All existing tabs remain available.

## Evaluate or resume

From the repository root, set `TYPESAFE_API_KEY` in your environment or local
`jev/.env`, then run:

```bash
python3 jev/explore_jobs.py --all \
  --filter job.location.san_francisco=0.80 \
  --filter job.work.research_ux_ui=0.80 \
  --filter job.domain.education=0.80 \
  --output _v5_daily-jobs/data/manual-results/new-filter-matches.json
python3 _v5_daily-jobs/scripts/build_active_jobs_html_luna.py
```

Each question is scored independently and saved for reuse. This JSON report
contains jobs passing **San Francisco and both new filters**; the browser lets
you select either new tab separately. Repeating the command skips answers whose feature version
and posting input are unchanged. It does not recollect job postings.
