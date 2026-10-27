"""Build a browser-friendly, read-only projection of canonical job results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _clean_job(job: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields that help a job seeker evaluate a role."""

    allowed = (
        "stable_id",
        "company_id",
        "title",
        "locations",
        "workplace_arrangement",
        "workplace_text",
        "employment_type",
        "employment_text",
        "compensation",
        "compensation_text",
        "department",
        "team",
        "posted_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "description_text",
        "semantic_evidence",
        "role_fit_evidence",
        "ai_involvement_evidence",
        "review_notes",
    )
    cleaned = {key: job.get(key) for key in allowed if job.get(key) not in (None, "", [], {})}
    cleaned["canonical_url"] = _http_url(job.get("canonical_url"))
    return cleaned


def build_site_data(root: Path) -> dict[str, Any]:
    """Return a deterministic projection from canonical companies and snapshots."""

    companies_doc = _read_json(root / "data" / "companies.json")
    profile = _read_json(root / "data" / "profiles" / "ai-product-frontend-v1.json")
    companies_by_id = {
        company["company_id"]: company for company in companies_doc["companies"]
    }

    jobs: list[dict[str, Any]] = []
    source_views: dict[str, dict[str, Any]] = {}
    job_ids: set[str] = set()
    latest_scan: str | None = None

    for snapshot_path in sorted((root / "data" / "jobs" / "sources").glob("*.json")):
        snapshot = _read_json(snapshot_path)
        scan_at = snapshot.get("last_successful_scan_at") or snapshot.get("last_attempt_at")
        if isinstance(scan_at, str) and (latest_scan is None or scan_at > latest_scan):
            latest_scan = scan_at

        source_views[snapshot["source_id"]] = {
            "source_id": snapshot["source_id"],
            "company_id": snapshot["company_id"],
            "last_scanned_at": scan_at,
            "fetch_complete": bool(snapshot.get("fetch_complete")),
        }

        for job in snapshot.get("jobs", []):
            if job.get("status") != "active":
                continue
            stable_id = job.get("stable_id")
            if not isinstance(stable_id, str) or not stable_id:
                raise ValueError(f"Active job without a stable ID in {snapshot_path}")
            if stable_id in job_ids:
                raise ValueError(f"Duplicate active job stable ID: {stable_id}")
            job_ids.add(stable_id)
            cleaned = _clean_job(job)
            jobs.append(cleaned)

    job_ids_by_company: dict[str, list[str]] = {}
    for job in jobs:
        job_ids_by_company.setdefault(job["company_id"], []).append(job["stable_id"])

    companies: list[dict[str, Any]] = []
    for company_id, company_job_ids in job_ids_by_company.items():
        company = companies_by_id.get(company_id)
        if company is None:
            raise ValueError(f"Job snapshot references unknown company: {company_id}")

        sources = []
        for source in company.get("career_sources", []):
            snapshot_view = source_views.get(source["source_id"])
            if snapshot_view is None:
                continue
            sources.append(
                {
                    "provider": source.get("provider"),
                    "scope": source.get("scope"),
                    "status": source.get("status"),
                    "careers_url": _http_url(source.get("careers_url")),
                    "listings_url": _http_url(source.get("listings_url")),
                    "last_scanned_at": snapshot_view.get("last_scanned_at"),
                    "fetch_complete": snapshot_view.get("fetch_complete"),
                }
            )

        address = company.get("address")
        companies.append(
            {
                "company_id": company_id,
                "company_name": company["company_name"],
                "website_url": _http_url(company.get("website_url")),
                "address": address if isinstance(address, dict) else None,
                "address_status": company.get("address_status"),
                "address_source_url": _http_url(company.get("address_source_url")),
                "verified_date": company.get("verified_date"),
                "job_ids": sorted(company_job_ids),
                "job_count": len(company_job_ids),
                "sources": sorted(
                    sources,
                    key=lambda item: ((item.get("provider") or ""), item.get("scope") or ""),
                ),
            }
        )

    companies.sort(key=lambda item: (-item["job_count"], item["company_name"].casefold()))
    jobs.sort(key=lambda item: (item["company_id"], item["title"].casefold(), item["stable_id"]))

    return {
        "generated_at": latest_scan,
        "profile": {
            "profile_id": profile["profile_id"],
            "profile_name": profile["profile_name"],
            "description": profile["description"],
        },
        "summary": {"company_count": len(companies), "job_count": len(jobs)},
        "companies": companies,
        "jobs": jobs,
    }


def render_site_data(root: Path) -> str:
    """Serialize the projection as a browser-loadable JavaScript assignment."""

    payload = json.dumps(
        build_site_data(root),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        "// Generated from canonical V4 data. Do not edit by hand.\n"
        f"window.JOB_EXPLORER_DATA={payload};\n"
    )
