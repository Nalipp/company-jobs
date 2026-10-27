#!/usr/bin/env python3
"""Create a self-contained, local-browser view of the first 1,000 active jobs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Active jobs</title>
  <style>
    :root {
      --white: #ffffff;
      --ink: #171717;
      --mist: #e8e8e8;
      --blue: #1e90ff;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      padding: 8px;
      background: var(--white);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 13px;
      line-height: 1.05;
    }
    header {
      display: flex;
      align-items: baseline;
      gap: 10px;
      height: 27px;
      overflow: hidden;
      white-space: nowrap;
    }
    h1 { margin: 0; font-size: 16px; }
    #summary { font-size: 12px; }
    .table-wrap {
      height: calc(100vh - 43px);
      overflow: auto;
      border: 1px solid var(--mist);
    }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    thead { position: sticky; top: 0; z-index: 1; }
    th {
      height: 24px;
      padding: 0;
      background: var(--ink);
      color: var(--white);
      text-align: left;
    }
    th button {
      width: 100%;
      height: 24px;
      border: 0;
      padding: 2px 6px;
      background: var(--ink);
      color: var(--white);
      font: inherit;
      font-weight: 700;
      line-height: 1;
      text-align: left;
      cursor: pointer;
      white-space: nowrap;
    }
    th button:focus-visible, a:focus-visible { outline: 2px solid var(--blue); outline-offset: -2px; }
    td {
      height: 22px;
      padding: 3px 6px;
      border-top: 1px solid var(--mist);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      vertical-align: middle;
    }
    tbody tr:nth-child(even) { background: var(--mist); }
    tbody tr:hover { background: var(--mist); }
    a { color: var(--blue); font-weight: 700; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .company { width: 15%; }
    .title { width: 29%; }
    .location { width: 20%; }
    .workplace { width: 10%; }
    .employment { width: 10%; }
    .published { width: 9%; }
    .open { width: 7%; }
  </style>
</head>
<body>
  <header>
    <h1>Active jobs</h1>
    <span id="summary"></span>
  </header>
  <main class="table-wrap">
    <table aria-label="First 1,000 active jobs">
      <thead><tr id="headers"></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const JOBS = __JOBS_JSON__;
    const TOTAL_ACTIVE_JOBS = __TOTAL_ACTIVE_JOBS__;
    const GENERATED_AT = __GENERATED_AT__;
    const columns = [
      ['company', 'Company', 'company'],
      ['title', 'Job title', 'title'],
      ['location', 'Location', 'location'],
      ['workplace', 'Workplace', 'workplace'],
      ['employment', 'Type', 'employment'],
      ['published', 'Posted', 'published'],
      ['open', 'Open', null]
    ];
    let sort = { key: 'lastSeen', direction: 'desc' };

    const compare = (left, right) => String(left ?? '').localeCompare(
      String(right ?? ''), undefined, { numeric: true, sensitivity: 'base' }
    );

    function render() {
      const ordered = [...JOBS].sort((left, right) => {
        const result = compare(left[sort.key], right[sort.key]);
        return sort.direction === 'asc' ? result : -result;
      });
      const headers = document.getElementById('headers');
      const rows = document.getElementById('rows');
      headers.replaceChildren();
      rows.replaceChildren();

      for (const [className, label, key] of columns) {
        const th = document.createElement('th');
        th.className = className;
        if (!key) {
          th.textContent = label;
        } else {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = `${label}${sort.key === key ? (sort.direction === 'asc' ? ' ↑' : ' ↓') : ''}`;
          button.setAttribute('aria-label', `Sort by ${label}`);
          button.addEventListener('click', () => {
            sort = sort.key === key
              ? { key, direction: sort.direction === 'asc' ? 'desc' : 'asc' }
              : { key, direction: 'asc' };
            render();
          });
          th.append(button);
        }
        headers.append(th);
      }

      const fragment = document.createDocumentFragment();
      for (const job of ordered) {
        const tr = document.createElement('tr');
        for (const [className, , key] of columns) {
          const td = document.createElement('td');
          td.className = className;
          if (key) {
            td.textContent = job[key] || '—';
            td.title = job[key] || '';
          } else {
            const link = document.createElement('a');
            link.href = job.url;
            link.target = '_blank';
            link.rel = 'noreferrer';
            link.textContent = 'View';
            td.append(link);
          }
          tr.append(td);
        }
        fragment.append(tr);
      }
      rows.append(fragment);
    }

    document.getElementById('summary').textContent =
      `${JOBS.length.toLocaleString()} of ${TOTAL_ACTIVE_JOBS.toLocaleString()} active jobs · generated ${GENERATED_AT}`;
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/v5.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("html/active-jobs.html"))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be positive")

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    try:
        total_active = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'active'"
        ).fetchone()[0]
        records = connection.execute(
            """
            SELECT c.company_name,
                   j.title,
                   j.posting_locations_json,
                   j.workplace_text,
                   j.employment_text,
                   j.published_at,
                   j.last_seen_at,
                   j.canonical_url
              FROM jobs AS j
              JOIN career_sources AS s ON s.source_id = j.source_id
              JOIN companies AS c ON c.company_id = s.company_id
             WHERE j.status = 'active'
             ORDER BY j.last_seen_at DESC, j.job_id ASC
             LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    finally:
        connection.close()

    jobs = []
    for record in records:
        locations = json.loads(record["posting_locations_json"])
        jobs.append({
            "company": record["company_name"],
            "title": record["title"],
            "location": " · ".join(str(location) for location in locations) or "—",
            "workplace": record["workplace_text"] or "—",
            "employment": record["employment_text"] or "—",
            "published": (record["published_at"] or "")[:10] or "—",
            "lastSeen": record["last_seen_at"],
            "url": record["canonical_url"],
        })

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = json.dumps(jobs, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (PAGE_TEMPLATE
            .replace("__JOBS_JSON__", payload)
            .replace("__TOTAL_ACTIVE_JOBS__", str(total_active))
            .replace("__GENERATED_AT__", json.dumps(generated_at)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    print(f"Wrote {len(jobs)} embedded active jobs to {args.output}")


if __name__ == "__main__":
    main()
