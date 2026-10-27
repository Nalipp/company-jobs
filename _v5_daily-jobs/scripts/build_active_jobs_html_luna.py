#!/usr/bin/env python3
"""Build Luna's company/job browser from a read-only SQLite snapshot.

Company metrics and title classifications are provisional presentation data.
Nothing calculated by this export is persisted to SQL.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "v5.sqlite"
JEV_CACHE = ROOT.parent / "jev" / "jev_cache.sqlite"
OUTPUT = ROOT / "html" / "active-jobs-luna.html"
SOURCES = ROOT / "html" / "luna"
CATEGORIES = ("Engineering/technical", "Sales/business development", "Product", "Design/UX", "Other")
WORKPLACE_MODES = ("Hybrid", "On-site", "Remote", "Not listed")
JEV_SF_FEATURE = "job.location.san_francisco"
JEV_SF_THRESHOLD = 0.80

# The browser uses this small, explicit registry to render the available Jev
# chips.  Multiple selected workplace chips are alternatives (on-site OR
# hybrid), while the remaining selected chips narrow the result set.
JEV_FILTERS = (
    {"key": "job.location.san_francisco", "label": "San Francisco", "threshold": 0.80, "default": True},
    {"key": "job.workplace.onsite", "label": "On-site", "threshold": 0.80, "matchGroup": "workplace"},
    {"key": "job.workplace.hybrid", "label": "Hybrid", "threshold": 0.80, "matchGroup": "workplace"},
    {"key": "job.workplace.remote", "label": "Remote", "threshold": 0.80, "matchGroup": "workplace"},
    {"key": "job.role.technical", "label": "Technical", "threshold": 0.80},
    {"key": "job.role.software_engineering", "label": "Software eng", "threshold": 0.80},
    {"key": "job.role.frontend_engineering", "label": "Frontend", "threshold": 0.80},
    {"key": "job.role.product_ux_design", "label": "Design role", "threshold": 0.80},
    {"key": "job.work.applied_ai", "label": "Applied AI", "threshold": 0.80},
    {"key": "job.work.product_ownership", "label": "Product owner", "threshold": 0.80},
    {"key": "job.work.product_ui_ux", "label": "Product UI/UX", "threshold": 0.80},
    {"key": "job.work.rapid_prototyping", "label": "Rapid proto", "threshold": 0.80},
    {"key": "job.work.growth_experimentation", "label": "Growth", "threshold": 0.80},
    {"key": "job.work.data_filtering_ranking", "label": "Data filters", "threshold": 0.80},
    {"key": "job.work.web_data_acquisition", "label": "Web scraping", "threshold": 0.80},
    {"key": "job.work.geospatial_maps", "label": "Maps", "threshold": 0.80},
    {"key": "job.work.legacy_code_modernization", "label": "Legacy", "threshold": 0.80},
    {"key": "job.mentions.varied_backgrounds", "label": "Varied backgrounds", "threshold": 0.80},
    {"key": "job.workflow.typesafe_or_jev_like", "label": "TypeSafe/Jev", "threshold": 0.80},
    {"key": "job.requirement.korean_language", "label": "Korean", "threshold": 0.80},
    {"key": "job.travel.south_korea", "label": "SK travel", "threshold": 0.80},
    {"key": "job.work.south_korea_business", "label": "SK business", "threshold": 0.80},
    {"key": "job.culture.extreme_work_expectations", "label": "Hustle flag", "threshold": 0.80, "mode": "exclude"},
)


def normalized(value: str | None) -> str:
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()


def classify_title(title: str) -> str | None:
    """One category per posting; specific functions precede broad product terms.

    Do not classify from descriptions mentioning unrelated teams or from whole
    companies. Exclude e.g. engineering recruiters and product marketing roles.
    """
    title = normalized(title)
    if re.search(r"\b(recruit\w*|talent (?:acquisition|partner|manager)|human resources|hrbp|(?:hr|people) (?:partner|business partner|operations)|marketing|accountant|legal|counsel|procurement|executive assistant|administrative assistant|financial analyst|finance manager|strategic finance|policy design)\b", title):
        return None
    if re.search(r"\b(sales|business development|account executive|account manager|account director|account management|sdr|bdr|revenue operations|partnerships?|partner development|channel manager)\b", title):
        return "Sales/business development"
    if re.search(r"\b(engineer\w*|developer\w*|programmer\w*|architect\w*|data scien\w*|research scien\w*|machine learning|devops|devsecops|sre|cybersecurity|technical|technician\w*|it (?:support|manager|director|specialist|administrator)|systems? administrator|database administrator|security (?:analyst|researcher)|quality assurance|qa|software|full[ -]?stack|frontend|backend|front[ -]end|back[ -]end)\b", title):
        return "Engineering/technical"
    if re.search(r"\b(designer\w*|design|ux|ui|user experience|user research|interaction design|content design)\b", title):
        return "Design/UX"
    if re.search(r"\bproduct\b", title):
        return "Product"
    return None


def street_only(street: str | None, city: str | None = "San Francisco") -> str:
    """Use the structured street, removing any appended city/state.

    Keep suite/floor details. Never replace an office street with job city text.
    """
    value = re.sub(r"\s+", " ", street or "").strip()
    for locality in dict.fromkeys([city or "San Francisco", "San Francisco"]):
        value = re.split(r"\b" + re.escape(locality) + r"\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"[,;]\s*(?:CA|California|USA|US|United States)\b.*$", "", value, flags=re.I)
    return value.strip(" ,;|-–")


def workplace(value: str | None) -> str | None:
    key = re.sub(r"[^a-z]", "", normalized(value))
    if "hybrid" in key or "hibrid" in key:
        return "Hybrid"
    if any(word in key for word in ("remote", "telecommut", "wfh")):
        return "Remote"
    if any(word in key for word in ("onsite", "inoffice", "inperson")):
        return "On-site"
    return None


def dominant_workplace(values: list[str | None]) -> str:
    counts = Counter(mode for value in values if (mode := workplace(value)))
    if not counts:
        return "Not listed"
    highest = max(counts.values())
    winners = [mode for mode, count in counts.items() if count == highest]
    return winners[0] if len(winners) == 1 else "Hybrid"


def parse_locations(value: str | None) -> list[str]:
    try:
        locations = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(locations, list):
        return []
    return [str(location).strip() for location in locations if str(location).strip()]


def is_san_francisco_posting(locations: list[str]) -> bool:
    return any(
        "san francisco" in normalized(location)
        or re.search(r"\bsf(?:,?\s*ca)?\b", normalized(location))
        for location in locations
    )


def workplace_mix(values: list[str | None]) -> dict[str, int]:
    counts = Counter(workplace(value) or "Not listed" for value in values)
    return {mode: counts[mode] for mode in WORKPLACE_MODES if counts[mode]}


def load_jev_scores(cache: Path = JEV_CACHE) -> dict[str, dict[str, float]]:
    """Load reusable job-level Jev probabilities without changing the cache."""
    if not cache.exists():
        raise FileNotFoundError(f"Jev cache not found: {cache}")
    connection = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT entity_id, feature_key, probability
            FROM feature_results
            WHERE scope = 'job'
            ORDER BY entity_id, feature_key
            """
        ).fetchall()
    finally:
        connection.close()

    scores: dict[str, dict[str, float]] = {}
    for row in rows:
        scores.setdefault(row["entity_id"], {})[row["feature_key"]] = float(row["probability"])
    return scores


def build_snapshot(database: Path = DATABASE, jev_cache: Path = JEV_CACHE) -> dict:
    jev_scores = load_jev_scores(jev_cache)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        # Same cohort for list and map. No prior 1,000-job sample limit.
        companies = connection.execute("""
            SELECT c.company_id, c.company_name, c.website_url, c.validation_checked_at,
                   l.street, l.city, l.region, l.postal_code, l.country,
                   l.latitude, l.longitude, l.verified_at
              FROM eligible_companies c
              JOIN eligible_company_locations l ON l.company_id = c.company_id
             WHERE EXISTS (
                 SELECT 1 FROM jobs j JOIN career_sources s USING(source_id)
                  WHERE s.company_id = c.company_id AND j.status = 'active'
             )
               AND l.location_id = (
                 SELECT location_id FROM eligible_company_locations candidate
                  WHERE candidate.company_id = c.company_id
                  ORDER BY is_primary_map_location DESC, location_id LIMIT 1
               )
             ORDER BY c.company_name COLLATE NOCASE, c.company_id
        """).fetchall()
        rows = connection.execute("""
            SELECT j.job_id, s.company_id, j.title, j.canonical_url,
                   j.posting_locations_json, j.workplace_text, j.employment_text,
                   j.compensation_text, j.published_at, j.last_seen_at,
                   s.source_id, s.provider, s.careers_url, s.listings_url,
                   s.status AS source_status, s.last_success_at
              FROM jobs j
              JOIN career_sources s USING(source_id)
              JOIN eligible_companies c USING(company_id)
             WHERE j.status = 'active'
             ORDER BY j.title COLLATE NOCASE, j.job_id
        """).fetchall()
    finally:
        connection.close()

    grouped = {row["company_id"]: [] for row in companies}
    modes = {row["company_id"]: [] for row in companies}
    sources = {row["company_id"]: {} for row in companies}
    counts = Counter({category: 0 for category in CATEGORIES})
    for row in rows:
        company_id = row["company_id"]
        if company_id not in grouped:
            continue
        modes[company_id].append(row["workplace_text"])
        category = classify_title(row["title"]) or "Other"
        locations = parse_locations(row["posting_locations_json"])
        counts[category] += 1
        grouped[company_id].append({
            "id": row["job_id"], "title": row["title"], "url": row["canonical_url"],
            "locations": locations, "isSanFrancisco": is_san_francisco_posting(locations),
            "jev": jev_scores.get(row["job_id"], {}),
            "workplace": workplace(row["workplace_text"]) or "Not listed",
            "employment": row["employment_text"], "compensation": row["compensation_text"],
            "published": row["published_at"], "lastSeen": row["last_seen_at"],
            "category": category,
        })
        sources[company_id].setdefault(row["source_id"], {
            "id": row["source_id"], "provider": row["provider"],
            "careersUrl": row["careers_url"], "listingsUrl": row["listings_url"],
            "status": row["source_status"], "lastSuccessAt": row["last_success_at"],
        })
    result = []
    for row in companies:
        company_id = row["company_id"]
        jobs = grouped[company_id]
        jev_sf_count = sum(
            job["jev"].get(JEV_SF_FEATURE, 0) >= JEV_SF_THRESHOLD for job in jobs
        )
        company_categories = Counter(job["category"] for job in jobs)
        location_counts = Counter()
        for job in jobs:
            location_counts.update(dict.fromkeys(job["locations"], 1))
        career_sources = sorted(
            sources[company_id].values(),
            key=lambda source: (source["status"] != "ready", normalized(source["provider"]), source["id"]),
        )
        result.append({
            "id": company_id, "name": row["company_name"],
            "street": street_only(row["street"], row["city"]),
            "address": {
                "street": street_only(row["street"], row["city"]), "city": row["city"],
                "region": row["region"], "postalCode": row["postal_code"], "country": row["country"],
            },
            "latitude": row["latitude"], "longitude": row["longitude"],
            "websiteUrl": row["website_url"], "validationCheckedAt": row["validation_checked_at"],
            "locationVerifiedAt": row["verified_at"],
            "workplace": dominant_workplace(modes[company_id]),
            "workplaceMix": workplace_mix(modes[company_id]),
            "activeJobCount": len(jobs),
            "sanFranciscoJobCount": sum(job["isSanFrancisco"] for job in jobs),
            "jevSanFranciscoJobCount": jev_sf_count,
            "lastCollectedAt": max((job["lastSeen"] for job in jobs if job["lastSeen"]), default=None),
            "compensationJobCount": sum(bool(job["compensation"]) for job in jobs),
            "categoryCounts": {category: company_categories[category] for category in CATEGORIES if company_categories[category]},
            "topPostingLocations": [
                {"name": location, "jobs": count}
                for location, count in location_counts.most_common(6)
            ],
            "careerSources": career_sources,
            "jobs": jobs,
        })
    all_jobs = [job for company_jobs in grouped.values() for job in company_jobs]
    jev_filters = [
        {
            **filter_definition,
            "count": sum(
                job["jev"].get(filter_definition["key"], 0)
                >= filter_definition["threshold"]
                for job in all_jobs
            ),
        }
        for filter_definition in JEV_FILTERS
    ]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classification": "luna-title-rules-v1",
        "categoryCounts": dict(counts),
        "totalActiveJobs": len(rows),
        "jevFilters": jev_filters,
        "jevScoredJobs": sum(bool(job["jev"]) for jobs in grouped.values() for job in jobs),
        "companies": result,
    }


def main() -> None:
    snapshot = build_snapshot()
    embedded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        embedded = embedded.replace(char, escaped)
    template = (SOURCES / "page.html").read_text(encoding="utf-8")
    template = template.replace("__LUNA_CSS__", (SOURCES / "styles.css").read_text(encoding="utf-8"))
    template = template.replace("__LUNA_JS__", (SOURCES / "app_v2.js").read_text(encoding="utf-8"))
    OUTPUT.write_text(template.replace("__JOBS_JSON__", embedded), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "companies": len(snapshot["companies"]),
                      "jobs": snapshot["totalActiveJobs"],
                      "jevScoredJobs": snapshot["jevScoredJobs"],
                      "companiesWithJobs": sum(bool(company["jobs"]) for company in snapshot["companies"]),
                      "categories": snapshot["categoryCounts"]}, indent=2))


if __name__ == "__main__":
    main()
