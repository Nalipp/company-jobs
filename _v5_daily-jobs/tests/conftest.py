from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from v5_daily_jobs.import_v4 import canonical_json, sha256_json


STAMP = "2026-09-01T12:00:00Z"


def source(source_id: str, company_id: str, *, status: str = "ready") -> dict[str, Any]:
    return {
        "source_id": source_id,
        "company_id": company_id,
        "record_revision": 1,
        "careers_url": f"https://{company_id}.example/careers",
        "listings_url": f"https://jobs.ashbyhq.com/{company_id}",
        "provider": "ashby",
        "board_id": company_id,
        "access_mode": "structured_feed",
        "scope": "All roles",
        "instructions": "Use the official structured feed.",
        "capabilities": {
            "complete_enumeration": True,
            "stable_job_identity": True,
            "structured_data": True,
        },
        "status": status,
        "official_relationship_url": f"https://{company_id}.example/careers",
        "checked_at": STAMP,
        "last_success_at": STAMP if status == "ready" else None,
        "added_date": STAMP,
        "updated_date": STAMP,
    }


def company(
    company_id: str,
    status: str,
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if status in {"verified", "outside_sf", "pending"}:
        city = "San Francisco" if status != "outside_sf" else "Oakland"
        address = {
            "street": f"1 {company_id.title()} Street",
            "city": city,
            "region": "CA",
            "postal_code": "94105" if city == "San Francisco" else "94612",
            "country": "US",
        }
    else:
        address = None
    accepted = status in {"verified", "outside_sf"}
    return {
        "company_id": company_id,
        "company_name": company_id.title(),
        "record_revision": 1,
        "website_url": f"https://{company_id}.example/",
        "address": address,
        "latitude": 37.78 if accepted else None,
        "longitude": -122.40 if accepted else None,
        "address_status": status,
        "address_source_url": f"https://{company_id}.example/contact" if accepted else None,
        "address_source_label": "Official contact page" if accepted else None,
        "verified_date": STAMP if status != "pending" else None,
        "added_date": STAMP,
        "updated_date": STAMP,
        "career_sources": sources or [],
    }


def result_document(company_id: str = "verified-co") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workflow": "validate_companies",
        "task_id": f"company:{company_id}:luna:test",
        "attempt_id": "attempt:test",
        "claim_token": "secret-that-must-not-be-imported",
        "model": "luna",
        "created_at": STAMP,
        "target": {"company_ids": [company_id], "source_ids": []},
        "outcome": "resolved",
        "reason_code": "pending_validation",
        "evidence": [
            {
                "url": f"https://{company_id}.example/contact",
                "retrieved_at": STAMP,
                "evidence_type": "official_company_page",
                "finding": "Physical office confirmed.",
            }
        ],
        "patch": {},
        "item_results": [],
        "failure_class": None,
        "notes": "Fixture result.",
    }


def write_fixture(root: Path, *, missing_result_file: bool = False) -> dict[str, Any]:
    (root / "data" / "results" / "companies").mkdir(parents=True)
    (root / "data" / "queues").mkdir(parents=True)
    (root / "archive" / "v3").mkdir(parents=True)
    (root / "data" / "companies.lock").touch()
    (root / "data" / "queues" / "company-queue.lock").touch()

    result = result_document()
    digest = sha256_json(result)
    if not missing_result_file:
        (root / "data" / "results" / "companies" / "result.json").write_text(
            canonical_json(result), encoding="utf-8"
        )

    canonical = {
        "schema_version": 1,
        "revision": 10,
        "updated_at": STAMP,
        "companies": [
            company("verified-co", "verified", sources=[source("verified-ashby", "verified-co")]),
            company(
                "outside-co",
                "outside_sf",
                sources=[source("outside-ashby", "outside-co", status="blocked")],
            ),
            company("pending-co", "pending"),
            company("missing-co", "not_found", sources=[source("missing-ashby", "missing-co")]),
        ],
        "application_ledger": [
            {
                "transaction_id": f"txn:{digest[:24]}",
                "task_id": result["task_id"],
                "attempt_id": result["attempt_id"],
                "result_digest": digest,
                "applied_at": STAMP,
            }
        ],
        "migration": {},
    }
    (root / "data" / "companies.json").write_text(
        canonical_json(canonical), encoding="utf-8"
    )

    records = [
        {
            "created_at": STAMP,
            "handoff": "imports/known.json",
            "handoff_sha256": "a" * 64,
            "issue": {
                "code": "prior_conflict",
                "company_id": "verified-co",
                "details": "Older conflicting address was rejected.",
                "source_url": "https://directory.example/verified-co",
                "subject": "Verified Co",
            },
            "issue_index": 0,
            "operation": "company-validate",
            "record_id": "known:issue:1",
        },
        {
            "created_at": STAMP,
            "handoff": "imports/unknown.json",
            "handoff_sha256": "b" * 64,
            "issue": {
                "code": "identity_ambiguous",
                "company_id": "unknown-co",
                "details": "The identity could not be resolved.",
                "source_url": None,
                "subject": "Unknown Co",
            },
            "issue_index": 0,
            "operation": "company-ingest",
            "record_id": "unknown:issue:1",
        },
    ]
    research = {
        "schema_version": 1,
        "source": "fixture",
        "handoff_count": 2,
        "record_count": len(records),
        "company_count": 2,
        "records": records,
    }
    (root / "archive" / "v3" / "company-research.json").write_text(
        canonical_json(research), encoding="utf-8"
    )

    if missing_result_file:
        resolution = {
            "attempt_id": result["attempt_id"],
            "evidence_count": 1,
            "item_results": [
                {"item": "office", "status": "resolved", "summary": "Confirmed."}
            ],
            "notes": "Recovered fixture.",
            "outcome": result["outcome"],
            "reason_code": result["reason_code"],
            "resolved_at": STAMP,
            "result_digest": digest,
            "result_file": str(root / "data" / "results" / "companies" / "missing.json"),
        }
        queue = {
            "schema_version": 1,
            "revision": 1,
            "updated_at": STAMP,
            "tasks": [
                {
                    "task_id": result["task_id"],
                    "workflow": "validate_companies",
                    "status": "completed",
                    "expected_model": "luna",
                    "reason_codes": [result["reason_code"]],
                    "target": result["target"],
                    "context": {"relevant_evidence": []},
                    "resolution": resolution,
                    "attempts": [],
                    "updated_at": STAMP,
                }
            ],
        }
    else:
        queue = {"schema_version": 1, "revision": 1, "updated_at": STAMP, "tasks": []}
    (root / "data" / "queues" / "company-queue.json").write_text(
        canonical_json(queue), encoding="utf-8"
    )
    return canonical


@pytest.fixture
def v4_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "v4"
    write_fixture(root)
    return root


@pytest.fixture
def missing_result_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "v4-missing-result"
    write_fixture(root, missing_result_file=True)
    return root


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "v5.sqlite"


@pytest.fixture
def fixture_writer():
    return write_fixture

