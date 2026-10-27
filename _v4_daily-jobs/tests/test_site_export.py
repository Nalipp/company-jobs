from __future__ import annotations

import json
from pathlib import Path

import pytest

from v4_daily_jobs.site_export import build_site_data, render_site_data


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_site_data_projects_only_active_user_facing_results(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data/companies.json",
        {
            "companies": [
                {
                    "company_id": "acme",
                    "company_name": "Acme",
                    "website_url": "https://acme.example",
                    "address": {"street": "1 Main", "city": "San Francisco", "region": "CA", "postal_code": "94105", "country": "US"},
                    "address_status": "verified",
                    "address_source_url": "https://example.com/address",
                    "verified_date": "2026-09-01T00:00:00Z",
                    "career_sources": [
                        {
                            "source_id": "acme-ashby",
                            "provider": "ashby",
                            "scope": "All roles",
                            "status": "ready",
                            "careers_url": "https://acme.example/careers",
                            "listings_url": "https://jobs.example/acme",
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        tmp_path / "data/profiles/ai-product-frontend-v1.json",
        {"profile_id": "profile", "profile_name": "AI roles", "description": "Matching roles."},
    )
    _write_json(
        tmp_path / "data/jobs/sources/acme-ashby.json",
        {
            "source_id": "acme-ashby",
            "company_id": "acme",
            "last_successful_scan_at": "2026-09-02T00:00:00Z",
            "fetch_complete": True,
            "jobs": [
                {
                    "stable_id": "job-active",
                    "company_id": "acme",
                    "status": "active",
                    "title": "Product Engineer",
                    "canonical_url": "https://jobs.example/acme/1",
                    "description_text": "Build useful things.",
                    "source_job_id": "internal-source-id",
                },
                {"stable_id": "job-inactive", "company_id": "acme", "status": "inactive", "title": "Old role"},
            ],
        },
    )

    result = build_site_data(tmp_path)

    assert result["summary"] == {"company_count": 1, "job_count": 1}
    assert result["companies"][0]["job_count"] == 1
    assert result["jobs"][0]["stable_id"] == "job-active"
    assert "source_job_id" not in result["jobs"][0]
    assert result["generated_at"] == "2026-09-02T00:00:00Z"


def test_build_site_data_rejects_unknown_company(tmp_path: Path) -> None:
    _write_json(tmp_path / "data/companies.json", {"companies": []})
    _write_json(
        tmp_path / "data/profiles/ai-product-frontend-v1.json",
        {"profile_id": "profile", "profile_name": "AI roles", "description": "Matching roles."},
    )
    _write_json(
        tmp_path / "data/jobs/sources/orphan.json",
        {
            "source_id": "orphan",
            "company_id": "missing",
            "fetch_complete": True,
            "jobs": [{"stable_id": "job-1", "company_id": "missing", "status": "active", "title": "Engineer"}],
        },
    )

    with pytest.raises(ValueError, match="unknown company"):
        build_site_data(tmp_path)


def test_render_site_data_creates_loadable_javascript(tmp_path: Path) -> None:
    _write_json(tmp_path / "data/companies.json", {"companies": []})
    _write_json(
        tmp_path / "data/profiles/ai-product-frontend-v1.json",
        {"profile_id": "profile", "profile_name": "AI roles", "description": "Matching roles."},
    )

    text = render_site_data(tmp_path)
    assert text.startswith("// Generated from canonical V4 data")
    assert "window.JOB_EXPLORER_DATA=" in text
