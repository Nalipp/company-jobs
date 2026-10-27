"""Build a self-contained, read-only snapshot of the first 1,000 active jobs.

Run from any directory: python3 /path/to/_v5_daily-jobs/html/build_astra.py
The generated HTML needs no server, dependencies, or network connection.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "html" / "active-jobs-astra.html"

HEAD = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Active jobs · Astra · V5 Daily Jobs</title>
<style>
:root { --white: #ffffff; --dark: #181818; --light: #e9e9e9; --blue: #1e90ff; --row: clamp(17px, calc((100dvh - 160px) / 30), 24px); }
* { box-sizing: border-box; }
body { margin: 0; padding: 10px 14px; color: var(--dark); background: var(--white); font: 12px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
button, input, select { font: inherit; color: var(--dark); background: var(--white); border: 1px solid var(--light); border-radius: 3px; }
button { cursor: pointer; padding: 3px 8px; }
button:hover, select:hover { background: var(--light); }
button:disabled { cursor: default; color: var(--dark); background: var(--light); }
button:disabled:hover { background: var(--light); }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 1px; }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
h1 { margin: 0; font-size: 20px; line-height: 26px; font-weight: 650; letter-spacing: -.5px; }
.brand { color: var(--blue); font-size: 11px; font-weight: 600; letter-spacing: .7px; margin-right: 8px; }
#snapshot { font-size: 11px; text-align: right; }
.toolbar { display: flex; align-items: center; gap: 8px; min-height: 31px; margin-bottom: 5px; }
#search { width: 320px; max-width: 40vw; padding: 4px 8px; height: 27px; }
input::placeholder { color: var(--dark); opacity: 1; }
.scope { flex: 1; font-size: 11px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--light); border-radius: 3px; }
table { border-collapse: collapse; width: 100%; min-width: 880px; table-layout: fixed; }
col.company { width: 15%; } col.role { width: 31%; } col.location { width: 21%; }
col.mode { width: 9%; } col.pay { width: 13%; } col.posted { width: 8%; } col.source { width: 3%; }
th { height: 28px; padding: 0; background: var(--light); text-align: left; font-weight: 600; }
th button { border: 0; border-radius: 0; background: var(--light); font-weight: 600; width: 100%; text-align: left; padding: 5px 8px; white-space: nowrap; }
th[aria-sort="ascending"] button, th[aria-sort="descending"] button { color: var(--blue); }
.sort-arrow { display: inline-block; width: 12px; margin-left: 4px; }
td { height: var(--row); padding: 0 8px; border-top: 1px solid var(--light); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: calc(var(--row) - 1px); }
tr:hover td { background: var(--light); }
.role-button { border: 0; border-radius: 0; padding: 0; background: none; display: block; width: 100%; text-align: left; line-height: inherit; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.role-button:hover { text-decoration: underline; text-decoration-color: var(--blue); }
.source-link { color: var(--blue); text-decoration: none; display: block; font-size: 12px; line-height: inherit; }
td.source-cell { text-align: center; padding: 0 4px; }
.empty { text-align: center; height: 90px; }
footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 7px; min-height: 28px; }
.pagination { display: flex; gap: 6px; align-items: center; }
#page-select { padding: 3px 4px; }
.hint { font-size: 11px; }
dialog { color: var(--dark); background: var(--white); border: 2px solid var(--dark); border-radius: 4px; padding: 16px; width: min(760px, 94vw); max-height: 88dvh; }
dialog::backdrop { background: var(--light); }
.detail-head { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
h2 { margin: 2px 0 6px; font-size: 19px; }
#detail-company { font-weight: 600; color: var(--blue); }
#detail-facts { margin: 6px 0; line-height: 1.6; }
#detail-source { color: var(--blue); }
#detail-description { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.55; margin: 12px 0 0; }
.detail-note { font-size: 11px; border-top: 1px solid var(--light); padding-top: 6px; }
@media (max-width: 750px) { body { padding: 8px; } header { align-items: start; } #snapshot { max-width: 170px; } .brand { display: none; } .scope, .hint { display: none; } #search { flex: 1; max-width: none; } .toolbar { justify-content: space-between; } footer { gap: 4px; } }
</style>
</head>
<body>
<header>
  <h1><span class="brand">V5 / ASTRA</span>Active jobs</h1>
  <span id="snapshot"></span>
</header>
<div class="toolbar">
  <input id="search" type="search" aria-label="Search these 1,000 jobs" placeholder="Search title, company, location…" autocomplete="off">
  <span class="scope" id="scope"></span>
  <button type="button" id="reset">Reset view</button>
</div>
<div class="table-wrap">
<table aria-label="Active jobs; activate a column heading to sort">
  <colgroup><col class="company"><col class="role"><col class="location"><col class="mode"><col class="pay"><col class="posted"><col class="source"></colgroup>
  <thead><tr>
    <th scope="col" aria-sort="ascending"><button type="button" data-sort="company">Company<span class="sort-arrow" aria-hidden="true">↑</span></button></th>
    <th scope="col" aria-sort="none"><button type="button" data-sort="title">Job title<span class="sort-arrow" aria-hidden="true">↕</span></button></th>
    <th scope="col" aria-sort="none"><button type="button" data-sort="location">Location<span class="sort-arrow" aria-hidden="true">↕</span></button></th>
    <th scope="col" aria-sort="none"><button type="button" data-sort="mode">Work mode<span class="sort-arrow" aria-hidden="true">↕</span></button></th>
    <th scope="col" aria-sort="none"><button type="button" data-sort="pay">Compensation<span class="sort-arrow" aria-hidden="true">↕</span></button></th>
    <th scope="col" aria-sort="none"><button type="button" data-sort="date">Posted<span class="sort-arrow" aria-hidden="true">↕</span></button></th>
    <th scope="col" title="Open original posting" aria-label="Original posting">↗</th>
  </tr></thead>
  <tbody id="jobs"></tbody>
</table>
</div>
<footer>
  <span id="results" aria-live="polite" role="status"></span>
  <span class="hint">Click a title for details · ↗ original posting · 30 rows per page</span>
  <nav class="pagination" aria-label="Job pages">
    <button type="button" id="previous" aria-label="Previous page">← Prev</button>
    <label for="page-select">Page</label><select id="page-select" aria-label="Choose page"></select>
    <button type="button" id="next" aria-label="Next page">Next →</button>
  </nav>
</footer>
<dialog id="detail" aria-labelledby="detail-title">
  <div class="detail-head"><div><span id="detail-company"></span><h2 id="detail-title"></h2></div><button type="button" id="close-detail" aria-label="Close job details">Close ×</button></div>
  <div id="detail-facts"></div>
  <a id="detail-source" target="_blank" rel="noopener noreferrer">Open original posting ↗</a>
  <p id="detail-description"></p>
  <p class="detail-note">Saved posting text. Job locations are as listed by the source; office matching has not been verified.</p>
</dialog>
<noscript>Enable JavaScript to sort and browse the embedded job snapshot.</noscript>
<script id="job-data" type="application/json">'''

TAIL = r'''</script>
<script>
"use strict";
const snapshot = JSON.parse(document.getElementById("job-data").textContent);
const PAGE_SIZE = 30;
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const byId = id => document.getElementById(id);
const formats = { onsite: "On-site", inoffice: "On-site", hybrid: "Hybrid", hybridworking: "Hybrid", remote: "Remote", fullyremote: "Remote" };
function parseDate(raw) {
  if (!raw) return null;
  let value = String(raw);
  if (/^\d+(\.\d+)?$/.test(value)) {
    const n = Number(value); value = n < 100000000000 ? n * 1000 : n;
  }
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}
function shortDate(time) { return time === null ? "—" : new Date(time).toISOString().slice(0, 10); }
function safeUrl(value) {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
const jobs = snapshot.jobs.map((j, index) => {
  const location = j.locations.join("; ");
  const mode = formats[String(j.workplace || "").toLowerCase().replace(/[^a-z]/g, "")] || j.workplace || "";
  const date = parseDate(j.published);
  return { ...j, index, location, mode, date, url: safeUrl(j.url), search: [j.company, j.title, location, mode, j.pay, j.employment].join(" ").toLowerCase() };
});
let sortKey = "company", direction = 1, currentPage = 1, filtered = [];
byId("snapshot").textContent = `Static snapshot · ${snapshot.generated.slice(0, 10)}`;
byId("snapshot").title = `Generated ${snapshot.generated}. Refresh by running python3 html/build_astra.py.`;
byId("scope").textContent = `First ${jobs.length.toLocaleString()} of ${snapshot.total.toLocaleString()} active jobs · insertion order`;
const headings = [...document.querySelectorAll("[data-sort]")];
for (const button of headings) button.addEventListener("click", () => {
  const key = button.dataset.sort;
  direction = sortKey === key ? -direction : 1;
  sortKey = key; currentPage = 1; update();
});
function update() {
  const terms = byId("search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  filtered = jobs.filter(j => terms.every(term => j.search.includes(term)));
  filtered.sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    const aEmpty = av === null || av === "", bEmpty = bv === null || bv === "";
    if (aEmpty !== bEmpty) return aEmpty ? 1 : -1;
    const value = sortKey === "date" ? (av ?? 0) - (bv ?? 0) : collator.compare(String(av ?? ""), String(bv ?? ""));
    return direction * value || a.index - b.index;
  });
  for (const button of headings) {
    const active = sortKey === button.dataset.sort;
    button.parentElement.setAttribute("aria-sort", active ? (direction === 1 ? "ascending" : "descending") : "none");
    button.querySelector(".sort-arrow").textContent = active ? (direction === 1 ? "↑" : "↓") : "↕";
  }
  render();
}
function cell(value) { const td = document.createElement("td"); td.textContent = value || "—"; td.title = value || "Not supplied by source"; return td; }
function render() {
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  currentPage = Math.max(1, Math.min(currentPage, pageCount));
  const start = (currentPage - 1) * PAGE_SIZE, visible = filtered.slice(start, start + PAGE_SIZE);
  const fragment = document.createDocumentFragment();
  for (const job of visible) {
    const row = document.createElement("tr"); row.dataset.jobId = job.id;
    row.append(cell(job.company));
    const title = document.createElement("td"), button = document.createElement("button");
    button.type = "button"; button.className = "role-button"; button.textContent = job.title; button.title = job.title;
    button.setAttribute("aria-label", `View ${job.title} at ${job.company}`); button.addEventListener("click", () => showJob(job));
    title.append(button); row.append(title, cell(job.location), cell(job.mode), cell(job.pay), cell(shortDate(job.date)));
    const source = document.createElement("td"); source.className = "source-cell";
    if (job.url) {
      const link = document.createElement("a"); link.href = job.url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.className = "source-link"; link.textContent = "↗";
      link.setAttribute("aria-label", `Open original ${job.title} posting`); link.title = "Open original posting"; source.append(link);
    } else source.textContent = "—";
    row.append(source); fragment.append(row);
  }
  if (!visible.length) { const row = document.createElement("tr"), td = cell("No matches in this snapshot. Clear the search to see all jobs."); td.colSpan = 7; td.className = "empty"; row.append(td); fragment.append(row); }
  byId("jobs").replaceChildren(fragment);
  byId("results").textContent = filtered.length ? `${(start + 1).toLocaleString()}–${Math.min(start + PAGE_SIZE, filtered.length).toLocaleString()} of ${filtered.length.toLocaleString()} jobs` : "0 jobs";
  byId("previous").disabled = currentPage === 1; byId("next").disabled = currentPage === pageCount;
  const options = document.createDocumentFragment();
  for (let n = 1; n <= pageCount; n++) { const option = document.createElement("option"); option.value = String(n); option.textContent = `${n} / ${pageCount}`; option.selected = n === currentPage; options.append(option); }
  byId("page-select").replaceChildren(options);
}
function showJob(job) {
  byId("detail-company").textContent = job.company;
  byId("detail-title").textContent = job.title;
  byId("detail-facts").textContent = [job.location, job.mode, job.employment, job.pay, job.date !== null ? `Posted ${shortDate(job.date)}` : "", `Last collected ${shortDate(parseDate(job.lastSeen))}`].filter(Boolean).join(" · ");
  const link = byId("detail-source"); link.hidden = !job.url;
  if (job.url) link.href = job.url; else link.removeAttribute("href");
  byId("detail-description").textContent = job.description || "No description was saved for this posting.";
  byId("detail").showModal(); byId("detail").scrollTop = 0;
}
byId("close-detail").addEventListener("click", () => byId("detail").close());
byId("search").addEventListener("input", () => { currentPage = 1; update(); });
byId("previous").addEventListener("click", () => { currentPage--; render(); });
byId("next").addEventListener("click", () => { currentPage++; render(); });
byId("page-select").addEventListener("change", event => { currentPage = Number(event.target.value); render(); });
byId("reset").addEventListener("click", () => { byId("search").value = ""; sortKey = "company"; direction = 1; currentPage = 1; update(); });
update();
</script>
</body>
</html>
'''


def main():
    database = ROOT / "data" / "v5.sqlite"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM jobs WHERE status = 'active'").fetchone()[0]
        rows = connection.execute("""
            SELECT j.job_id AS id, c.company_name AS company, j.title,
                   j.canonical_url AS url, j.posting_locations_json,
                   j.workplace_text AS workplace, j.employment_text AS employment,
                   j.compensation_text AS pay, j.published_at AS published,
                   j.last_seen_at AS lastSeen, j.description_text AS description
              FROM jobs j
              JOIN career_sources s ON s.source_id = j.source_id
              JOIN companies c ON c.company_id = s.company_id
             WHERE j.status = 'active'
             ORDER BY j.rowid ASC
             LIMIT 1000
        """).fetchall()
    finally:
        connection.close()
    jobs = []
    for row in rows:
        job = dict(row)
        job["locations"] = json.loads(job.pop("posting_locations_json"))
        jobs.append(job)
    data = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "total": total, "jobs": jobs}
    # Prevent source text from closing the data script or being parsed as markup.
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        embedded = embedded.replace(char, escaped)
    OUTPUT.write_text(HEAD + embedded + TAIL, encoding="utf-8")
    print(f"Created {OUTPUT}\nEmbedded {len(jobs):,} of {total:,} active jobs; {OUTPUT.stat().st_size:,} bytes.")


if __name__ == "__main__":
    main()
