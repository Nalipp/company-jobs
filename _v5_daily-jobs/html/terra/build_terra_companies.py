#!/usr/bin/env python3
"""Build the Terra company-collapsed, static active-jobs snapshot.

Run: python3 html/terra/build_terra_companies.py
The resulting HTML has its data embedded and opens directly from disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "v5.sqlite"
OUTPUT = ROOT / "html" / "terra" / "active-companies-terra.html"
LIMIT = 1000


PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Active companies · Terra · V5 Daily Jobs</title>
  <style>
    :root {
      --white: #ffffff;
      --dark: #181818;
      --light: #e9e9e9;
      --blue: #1e90ff;
      --row: clamp(18px, calc((100dvh - 178px) / 30), 25px);
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      padding: 8px 12px;
      background: var(--white);
      color: var(--dark);
      font: 12px/1.2 Arial, Helvetica, sans-serif;
    }
    button, select { font: inherit; color: var(--dark); background: var(--white); }
    button { cursor: pointer; }
    :focus-visible { outline: 2px solid var(--blue); outline-offset: 1px; }
    header {
      display: flex;
      align-items: baseline;
      gap: 10px;
      min-height: 25px;
      white-space: nowrap;
    }
    h1 { margin: 0; font-size: 17px; line-height: 22px; }
    #snapshot { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
    .filters {
      display: flex;
      align-items: center;
      gap: 6px;
      height: 34px;
    }
    .chip {
      min-height: 23px;
      padding: 2px 8px;
      border: 1px solid var(--light);
      border-radius: 11px;
      font-weight: 700;
    }
    .chip[aria-pressed="true"] { border-color: var(--blue); color: var(--blue); }
    .chip-count {
      position: relative;
      top: -0.35em;
      margin-left: 3px;
      font-size: 0.4em;
      vertical-align: super;
    }
    #scope-note { margin-left: 4px; font-size: 11px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--light); }
    table { width: 100%; min-width: 760px; border-collapse: collapse; table-layout: fixed; }
    col.company { width: 30%; }
    col.jobs { width: 8%; }
    col.location { width: 28%; }
    col.workplace { width: 16%; }
    col.seen { width: 18%; }
    thead { position: sticky; top: 0; z-index: 1; }
    th {
      height: 25px;
      padding: 0;
      background: var(--dark);
      color: var(--white);
      text-align: left;
      white-space: nowrap;
    }
    th button {
      width: 100%;
      height: 25px;
      border: 0;
      padding: 1px 7px;
      background: var(--dark);
      color: var(--white);
      font-weight: 700;
      text-align: left;
    }
    th[aria-sort="ascending"] button, th[aria-sort="descending"] button { color: var(--blue); }
    .sort-mark { display: inline-block; width: 10px; margin-left: 3px; }
    td {
      height: var(--row);
      padding: 0 7px;
      border-top: 1px solid var(--light);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      vertical-align: middle;
    }
    .company-row { cursor: pointer; }
    .company-row:hover td { background: var(--light); }
    .company-toggle {
      width: 100%;
      border: 0;
      padding: 0;
      background: var(--white);
      color: var(--dark);
      font-weight: 700;
      line-height: inherit;
      overflow: hidden;
      text-align: left;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .company-row:hover .company-toggle { background: var(--light); }
    .company-toggle[aria-expanded="true"] { color: var(--blue); }
    .disclosure { display: inline-block; width: 13px; color: var(--blue); }
    .job-count { font-weight: 700; text-align: right; }
    .expanded-cell { padding: 0; white-space: normal; }
    .jobs-list { border-top: 1px solid var(--light); }
    .job-item {
      display: flex;
      align-items: baseline;
      min-height: 20px;
      gap: 7px;
      padding: 2px 10px 2px 20px;
      border-top: 1px solid var(--light);
    }
    .job-item:first-child { border-top: 0; }
    .job-title {
      flex: 1 1 48%;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--blue);
      font-weight: 700;
      text-decoration: none;
    }
    .job-title:hover { text-decoration: underline; }
    .job-meta {
      flex: 1 1 52%;
      min-width: 0;
      overflow: hidden;
      text-align: right;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .empty { height: 70px; text-align: center; }
    footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 31px;
      font-size: 11px;
    }
    .pagination { display: flex; align-items: center; gap: 5px; }
    .pagination button, #page-select { min-height: 22px; border: 1px solid var(--light); padding: 1px 6px; }
    .pagination button:disabled { cursor: default; background: var(--light); }
    @media (max-width: 700px) {
      body { padding: 6px; }
      #scope-note { display: none; }
      footer { gap: 4px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Active companies</h1>
    <span id="snapshot"></span>
  </header>
  <div class="filters" aria-label="Location filter">
    <button class="chip" type="button" data-scope="all" aria-pressed="true">All<span class="chip-count" id="all-count"></span></button>
    <button class="chip" type="button" data-scope="sf" aria-pressed="false">San Francisco<span class="chip-count" id="sf-count"></span></button>
    <span id="scope-note"></span>
  </div>
  <main class="table-wrap">
    <table aria-label="Active companies; select a company to reveal its job titles">
      <colgroup><col class="company"><col class="jobs"><col class="location"><col class="workplace"><col class="seen"></colgroup>
      <thead><tr>
        <th scope="col" aria-sort="ascending"><button type="button" data-sort="company">Company<span class="sort-mark" aria-hidden="true">↑</span></button></th>
        <th scope="col" aria-sort="none"><button type="button" data-sort="jobCount">Jobs<span class="sort-mark" aria-hidden="true">↕</span></button></th>
        <th scope="col" aria-sort="none"><button type="button" data-sort="location">Location<span class="sort-mark" aria-hidden="true">↕</span></button></th>
        <th scope="col" aria-sort="none"><button type="button" data-sort="workplace">Workplace<span class="sort-mark" aria-hidden="true">↕</span></button></th>
        <th scope="col" aria-sort="none"><button type="button" data-sort="lastSeen">Last collected<span class="sort-mark" aria-hidden="true">↕</span></button></th>
      </tr></thead>
      <tbody id="companies"></tbody>
    </table>
  </main>
  <footer>
    <span id="results" aria-live="polite"></span>
    <nav class="pagination" aria-label="Company pages">
      <button type="button" id="previous" aria-label="Previous company page">← Prev</button>
      <label for="page-select">Page</label><select id="page-select" aria-label="Choose company page"></select>
      <button type="button" id="next" aria-label="Next company page">Next →</button>
    </nav>
  </footer>
  <noscript>Enable JavaScript to browse this embedded company snapshot.</noscript>
  <script id="job-data" type="application/json">"""


PAGE_TAIL = """</script>
  <script>
    "use strict";
    const snapshot = JSON.parse(document.getElementById("job-data").textContent);
    const PAGE_SIZE = 30;
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    const byId = id => document.getElementById(id);
    const headings = [...document.querySelectorAll("[data-sort]")];
    const expanded = new Set();
    let scope = "all";
    let sortKey = "company";
    let direction = 1;
    let page = 1;
    let groups = [];

    function normalizedWorkplace(value) {
      const compact = String(value || "").toLowerCase().replace(/[^a-z]/g, "");
      if (compact.includes("hybrid")) return "Hybrid";
      if (compact.includes("remote")) return "Remote";
      if (compact.includes("onsite") || compact.includes("inoffice") || compact.includes("office")) return "On-site";
      return value || "Not specified";
    }

    function dateValue(value) {
      const time = new Date(value || "").getTime();
      return Number.isFinite(time) ? time : 0;
    }

    function shortDate(value) {
      const time = dateValue(value);
      return time ? new Date(time).toISOString().slice(0, 10) : "—";
    }

    function safeUrl(value) {
      try {
        const url = new URL(value);
        return ["http:", "https:"].includes(url.protocol) ? url.href : null;
      } catch { return null; }
    }

    function dominantWorkplace(jobs) {
      const counts = new Map();
      for (const job of jobs) {
        const name = normalizedWorkplace(job.workplace);
        counts.set(name, (counts.get(name) || 0) + 1);
      }
      const highest = Math.max(...counts.values());
      const leaders = [...counts].filter(([, count]) => count === highest).map(([name]) => name);
      return leaders.length > 1 ? "Hybrid" : leaders[0];
    }

    function locationSummary(jobs) {
      const locations = [...new Set(jobs.flatMap(job => job.locations).filter(Boolean))];
      const sf = locations.filter(location => /san francisco/i.test(location));
      if (sf.length && locations.length === sf.length) return "San Francisco";
      if (sf.length) return `San Francisco + ${locations.length - sf.length} other`;
      if (!locations.length) return "Not supplied";
      return locations.length === 1 ? locations[0] : `${locations[0]} + ${locations.length - 1} other`;
    }

    function buildGroups() {
      const visibleJobs = snapshot.jobs.filter(job => scope === "all" || job.isSanFrancisco);
      const grouped = new Map();
      for (const job of visibleJobs) {
        const group = grouped.get(job.companyId) || { id: job.companyId, company: job.company, jobs: [] };
        group.jobs.push(job);
        grouped.set(job.companyId, group);
      }
      groups = [...grouped.values()].map(group => ({
        ...group,
        jobCount: group.jobs.length,
        location: locationSummary(group.jobs),
        workplace: dominantWorkplace(group.jobs),
        lastSeen: Math.max(...group.jobs.map(job => dateValue(job.lastSeen))),
      }));
      groups.sort((left, right) => {
        const a = left[sortKey];
        const b = right[sortKey];
        const comparison = typeof a === "number" && typeof b === "number"
          ? a - b
          : collator.compare(String(a || ""), String(b || ""));
        return direction * comparison || collator.compare(left.company, right.company);
      });
    }

    function textCell(value, className = "") {
      const cell = document.createElement("td");
      cell.className = className;
      cell.textContent = value;
      cell.title = value;
      return cell;
    }

    function expandedRow(group) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "expanded-cell";
      const list = document.createElement("div");
      list.className = "jobs-list";
      const jobs = [...group.jobs].sort((a, b) => collator.compare(a.title, b.title));
      for (const job of jobs) {
        const item = document.createElement("div");
        item.className = "job-item";
        const url = safeUrl(job.url);
        const title = document.createElement(url ? "a" : "span");
        title.className = "job-title";
        title.textContent = job.title || "Untitled job";
        title.title = job.title || "Untitled job";
        if (url) {
          title.href = url;
          title.target = "_blank";
          title.rel = "noopener noreferrer";
        }
        const meta = document.createElement("span");
        meta.className = "job-meta";
        meta.textContent = [job.locations.join(" · "), normalizedWorkplace(job.workplace), shortDate(job.published)].filter(Boolean).join(" · ");
        item.append(title, meta);
        list.append(item);
      }
      cell.append(list);
      row.append(cell);
      return row;
    }

    function render() {
      buildGroups();
      const pageCount = Math.max(1, Math.ceil(groups.length / PAGE_SIZE));
      page = Math.max(1, Math.min(page, pageCount));
      const start = (page - 1) * PAGE_SIZE;
      const visible = groups.slice(start, start + PAGE_SIZE);
      const fragment = document.createDocumentFragment();
      for (const group of visible) {
        const row = document.createElement("tr");
        row.className = "company-row";
        const companyCell = document.createElement("td");
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "company-toggle";
        toggle.setAttribute("aria-expanded", String(expanded.has(group.id)));
        toggle.setAttribute("aria-label", `${expanded.has(group.id) ? "Collapse" : "Expand"} ${group.company} jobs`);
        const marker = document.createElement("span");
        marker.className = "disclosure";
        marker.textContent = expanded.has(group.id) ? "⌄" : "›";
        toggle.append(marker, document.createTextNode(group.company));
        companyCell.append(toggle);
        row.append(companyCell, textCell(String(group.jobCount), "job-count"), textCell(group.location), textCell(group.workplace), textCell(shortDate(group.lastSeen)));
        const flip = () => {
          if (expanded.has(group.id)) expanded.delete(group.id);
          else expanded.add(group.id);
          render();
        };
        toggle.addEventListener("click", flip);
        row.addEventListener("click", event => { if (!event.target.closest("button, a")) flip(); });
        fragment.append(row);
        if (expanded.has(group.id)) fragment.append(expandedRow(group));
      }
      if (!visible.length) {
        const row = document.createElement("tr");
        const cell = textCell("No company jobs match this location filter.", "empty");
        cell.colSpan = 5;
        row.append(cell);
        fragment.append(row);
      }
      byId("companies").replaceChildren(fragment);
      byId("results").textContent = groups.length
        ? `${(start + 1).toLocaleString()}–${Math.min(start + PAGE_SIZE, groups.length).toLocaleString()} of ${groups.length.toLocaleString()} companies · ${snapshot.jobs.filter(job => scope === "all" || job.isSanFrancisco).length.toLocaleString()} jobs`
        : "0 companies · 0 jobs";
      byId("previous").disabled = page === 1;
      byId("next").disabled = page === pageCount;
      const options = document.createDocumentFragment();
      for (let number = 1; number <= pageCount; number++) {
        const option = document.createElement("option");
        option.value = String(number);
        option.textContent = `${number} / ${pageCount}`;
        option.selected = number === page;
        options.append(option);
      }
      byId("page-select").replaceChildren(options);
      for (const button of headings) {
        const active = button.dataset.sort === sortKey;
        button.parentElement.setAttribute("aria-sort", active ? (direction === 1 ? "ascending" : "descending") : "none");
        button.querySelector(".sort-mark").textContent = active ? (direction === 1 ? "↑" : "↓") : "↕";
      }
    }

    byId("snapshot").textContent = `First ${snapshot.jobs.length.toLocaleString()} active jobs · ${snapshot.generated.slice(0, 10)}`;
    byId("all-count").textContent = snapshot.jobs.length.toLocaleString();
    byId("sf-count").textContent = snapshot.jobs.filter(job => job.isSanFrancisco).length.toLocaleString();
    byId("scope-note").textContent = "Click a company to show its job titles";
    document.querySelectorAll("[data-scope]").forEach(button => button.addEventListener("click", () => {
      scope = button.dataset.scope;
      page = 1;
      document.querySelectorAll("[data-scope]").forEach(chip => chip.setAttribute("aria-pressed", String(chip === button)));
      render();
    }));
    headings.forEach(button => button.addEventListener("click", () => {
      const key = button.dataset.sort;
      direction = sortKey === key ? -direction : 1;
      sortKey = key;
      page = 1;
      render();
    }));
    byId("previous").addEventListener("click", () => { page--; render(); });
    byId("next").addEventListener("click", () => { page++; render(); });
    byId("page-select").addEventListener("change", event => { page = Number(event.target.value); render(); });
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    connection = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        total_active = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'active'"
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT j.job_id,
                   c.company_id,
                   c.company_name,
                   j.title,
                   j.canonical_url,
                   j.posting_locations_json,
                   j.workplace_text,
                   j.published_at,
                   j.last_seen_at
              FROM jobs AS j
              JOIN career_sources AS s ON s.source_id = j.source_id
              JOIN companies AS c ON c.company_id = s.company_id
             WHERE j.status = 'active'
             ORDER BY j.last_seen_at DESC, j.job_id ASC
             LIMIT ?
            """,
            (LIMIT,),
        ).fetchall()
    finally:
        connection.close()

    jobs = []
    for row in rows:
        locations = json.loads(row["posting_locations_json"] or "[]")
        locations = [str(location) for location in locations if str(location).strip()]
        jobs.append({
            "id": row["job_id"],
            "companyId": row["company_id"],
            "company": row["company_name"],
            "title": row["title"],
            "url": row["canonical_url"],
            "locations": locations,
            "workplace": row["workplace_text"],
            "published": row["published_at"],
            "lastSeen": row["last_seen_at"],
            "isSanFrancisco": any("san francisco" in location.lower() for location in locations),
        })

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totalActive": total_active,
        "jobs": jobs,
    }
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        embedded = embedded.replace(character, escaped)
    OUTPUT.write_text(PAGE_HEAD + embedded + PAGE_TAIL, encoding="utf-8")
    sf_count = sum(job["isSanFrancisco"] for job in jobs)
    print(f"Created {OUTPUT}")
    print(f"Embedded {len(jobs):,} of {total_active:,} active jobs; {sf_count:,} list San Francisco.")


if __name__ == "__main__":
    main()
