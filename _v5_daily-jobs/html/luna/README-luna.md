# Luna company view

Rebuild from the repository root:

```sh
python3 scripts/build_active_jobs_html_luna.py
```

Open `../active-jobs-luna.html` directly. The build embeds this directory's CSS,
JavaScript and data. MapLibre uses the existing local vendor assets; base-map
tiles require internet access. No application server is needed.

The build also reads the job-level Jev cache from
`../../jev/jev_cache.sqlite`. Each embedded job now includes its reusable Jev
probabilities under `job.jev`, and the detail table displays the San Francisco
probability. The default San Francisco detail filter uses the configured Jev
threshold (`0.80`); the existing deterministic `isSanFrancisco` field remains
embedded separately for comparison.

- One company per stable SQL company ID, with one eligible office chosen by
  primary-location priority. The cohort is `eligible_companies` with active jobs
  (451 companies at this update). The previous 1,000-job limit is removed.
- The map/list view displays only the 430 cohort companies with at least one
  matching job; zero-job companies remain in the embedded data for auditability
  but receive no marker or list row. Marker counts describe matching jobs, not
  jobs verified to require commuting to that office.
- `classify_title` in the Luna builder applies local, mutually exclusive title
  rules for Engineering/technical, Sales/business development, Product and
  Design/UX. No saved classification matching the user's earlier category totals
  was found. These rules are provisional; SQL records remain unmodified.
- Company headers use the structured office street, with appended locality
  removed. Workplace is the most frequent recognized arrangement across that
  company's active postings. Unknown values do not outvote known ones; ties
  resolve to Hybrid. All-unknown arrangements display “Not listed.”
- Marker hover/focus opens a clickable company/count card. Clicking the marker or
  card clears search and automatically expands that company. The list is always
  limited to companies inside the current map bounds; map movement or zoom and
  Escape clear selection and collapse the current-bounds list. Search filters
  within those bounds. Reset fits the map to all non-empty markers and restores
  alphabetical ordering.

Source ownership: this directory, `scripts/build_active_jobs_html_luna.py`, and
`html/active-jobs-luna.html` are Luna's files. Other agents' variants are separate.
