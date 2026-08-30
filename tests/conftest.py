from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from v4_daily_jobs.json_store import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-08-30T12:00:00Z"


def company_record(*, verified: bool, with_source: bool) -> dict[str, Any]:
    company = {
        "company_id": "example-company",
        "company_name": "Example Company",
        "record_revision": 1,
        "website_url": None,
        "address": None,
        "latitude": None,
        "longitude": None,
        "address_status": "pending",
        "address_source_url": None,
        "address_source_label": None,
        "verified_date": None,
        "added_date": STAMP,
        "updated_date": STAMP,
        "career_sources": [],
    }
    if verified:
        company.update(
            {
                "website_url": "https://example.com/",
                "address": {
                    "street": "1 Market St",
                    "city": "San Francisco",
                    "region": "CA",
                    "postal_code": "94105",
                    "country": "US",
                },
                "latitude": 37.7936,
                "longitude": -122.3958,
                "address_status": "verified",
                "address_source_url": "https://example.com/contact",
                "address_source_label": "Official contact page",
                "verified_date": STAMP,
            }
        )
    if with_source:
        company["career_sources"] = [
            {
                "source_id": "example-company-ashby",
                "company_id": "example-company",
                "record_revision": 1,
                "careers_url": "https://example.com/careers",
                "listings_url": "https://jobs.ashbyhq.com/example",
                "provider": "ashby",
                "board_id": "example",
                "access_mode": "structured_feed",
                "scope": "All Example Company roles",
                "instructions": "Use the official Ashby public job-board endpoint.",
                "capabilities": {
                    "complete_enumeration": True,
                    "stable_job_identity": True,
                    "structured_data": True,
                },
                "status": "ready",
                "official_relationship_url": "https://example.com/careers",
                "checked_at": STAMP,
                "last_success_at": STAMP,
                "added_date": STAMP,
                "updated_date": STAMP,
            }
        ]
    return company


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def make_repo(root: Path, *, verified: bool = False, with_source: bool = False) -> Path:
    company = company_record(verified=verified, with_source=with_source)
    write_json(
        root / "data" / "companies.json",
        {
            "schema_version": 1,
            "revision": 1,
            "updated_at": STAMP,
            "companies": [company],
            "application_ledger": [],
            "migration": {},
        },
    )
    empty = {"schema_version": 1, "revision": 0, "updated_at": None, "tasks": []}
    write_json(root / "data" / "queues" / "company-queue.json", copy.deepcopy(empty))
    write_json(root / "data" / "queues" / "job-queue.json", copy.deepcopy(empty))
    profile = json.loads(
        (PROJECT_ROOT / "data" / "profiles" / "ai-product-frontend-v1.json").read_text(
            encoding="utf-8"
        )
    )
    write_json(root / "data" / "profiles" / "ai-product-frontend-v1.json", profile)
    write_json(
        root / "archive" / "v3" / "company-research.json",
        {
            "schema_version": 1,
            "source": "test",
            "handoff_count": 0,
            "record_count": 0,
            "company_count": 0,
            "records": [],
        },
    )
    (root / "data" / "jobs" / "sources").mkdir(parents=True, exist_ok=True)
    (root / "data" / "results" / "companies").mkdir(parents=True, exist_ok=True)
    (root / "data" / "results" / "jobs").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def repo_factory(tmp_path: Path):
    def factory(*, verified: bool = False, with_source: bool = False) -> Path:
        return make_repo(tmp_path / "repo", verified=verified, with_source=with_source)

    return factory

