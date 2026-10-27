from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from v5_daily_jobs.collect_jobs import (
    CollectionError, claim_next, finish_scan, run_structured,
)
from v5_daily_jobs.db import connect
from v5_daily_jobs.import_v4 import import_snapshot, load_v4_snapshot


def _ashby_job(job_id: str) -> dict[str, object]:
    return {
        "id": job_id,
        "title": f"Engineer {job_id}",
        "jobUrl": f"https://jobs.ashbyhq.com/verified-co/{job_id}",
        "descriptionPlain": f"Build the product for {job_id}.",
        "location": "San Francisco, CA",
        "workplaceType": "Hybrid",
        "compensation": {"maxValue": 250000, "currencyCode": "USD", "interval": "year"},
    }


def _make_due(database: Path) -> None:
    connection = connect(database)
    try:
        connection.execute(
            "UPDATE job_scans SET next_due_at = '2000-01-01T00:00:00Z' "
            "WHERE status <> 'running'"
        )
    finally:
        connection.close()


def test_structured_capture_preserves_all_jobs_and_closes_only_after_complete_scan(
    v4_fixture: Path, database_path: Path,
) -> None:
    import_snapshot(database_path, load_v4_snapshot(v4_fixture))

    def first_fetch(_url: str, _timeout: float) -> dict[str, object]:
        return {"apiVersion": 1, "jobs": [_ashby_job("a"), _ashby_job("b")]}

    first = run_structured(
        database_path, limit=1, source_id="verified-ashby", fetcher=first_fetch
    )
    assert first["complete"] == 1
    assert first["observed_jobs"] == 2
    connection = connect(database_path, read_only=True)
    try:
        rows = connection.execute(
            "SELECT source_job_id, status, description_text, raw_posting_json, "
            "job_details_json FROM jobs ORDER BY source_job_id"
        ).fetchall()
        assert [row["source_job_id"] for row in rows] == ["a", "b"]
        assert all(row["status"] == "active" for row in rows)
        assert all("Build the product" in row["description_text"] for row in rows)
        assert all("250000" in row["raw_posting_json"] for row in rows)
        assert all("Hybrid" in row["job_details_json"] for row in rows)
    finally:
        connection.close()

    _make_due(database_path)

    def incomplete_fetch(_url: str, _timeout: float) -> dict[str, object]:
        return {"apiVersion": 1, "meta": {"total": 2}, "jobs": [_ashby_job("a")]}

    second = run_structured(
        database_path, limit=1, source_id="verified-ashby", fetcher=incomplete_fetch
    )
    assert second["incomplete"] == 1
    connection = connect(database_path, read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'active'"
        ).fetchone()[0] == 2
    finally:
        connection.close()

    _make_due(database_path)

    def final_fetch(_url: str, _timeout: float) -> dict[str, object]:
        return {"apiVersion": 1, "jobs": [_ashby_job("a")]}

    third = run_structured(
        database_path, limit=1, source_id="verified-ashby", fetcher=final_fetch
    )
    assert third["complete"] == 1
    assert third["results"][0]["inactivated"] == 1
    connection = connect(database_path, read_only=True)
    try:
        assert dict(connection.execute(
            "SELECT source_job_id, status FROM jobs WHERE source_job_id = 'b'"
        ).fetchone())["status"] == "inactive"
    finally:
        connection.close()


def test_manual_claim_requires_evidence_and_persists_recipe(
    v4_fixture: Path, database_path: Path,
) -> None:
    import_snapshot(database_path, load_v4_snapshot(v4_fixture))
    connection = connect(database_path)
    try:
        connection.execute(
            "UPDATE career_sources SET provider = 'custom', access_mode = 'browser_required', "
            "structured_data = 0 WHERE source_id = 'verified-ashby'"
        )
    finally:
        connection.close()
    claim = claim_next(database_path, mode="manual", claimed_by="luna-test")
    assert claim["source"]["source_id"] == "verified-ashby"
    assert claim_next(database_path, mode="manual", claimed_by="other")["execution_status"] == "no_task"
    job = {
        "canonical_url": "https://verified-co.example/jobs/1",
        "title": "Product Engineer",
        "description_text": "Build the product.",
        "locations": ["San Francisco"],
        "raw_posting": {"url": "https://verified-co.example/jobs/1", "text": "Build the product."},
    }
    with pytest.raises(CollectionError, match="enumeration evidence"):
        finish_scan(
            database_path, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
            jobs=[job], complete=True, manual=True,
        )
    report = finish_scan(
        database_path, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
        jobs=[job], complete=True, manual=True,
        completeness_evidence="The official page shows exactly one role and no next page.",
        recipe={"method": "official_html", "listing_url": "https://verified-co.example/jobs"},
    )
    assert report["created"] == 1
    assert report["status"] == "complete"
    _make_due(database_path)
    next_claim = claim_next(database_path, mode="manual", claimed_by="luna-test")
    assert next_claim["recipe"]["method"] == "official_html"
    with pytest.raises(CollectionError, match="no longer active"):
        finish_scan(
            database_path, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
            jobs=[], complete=True,
        )


def test_invalid_capture_does_not_write_jobs(
    v4_fixture: Path, database_path: Path,
) -> None:
    import_snapshot(database_path, load_v4_snapshot(v4_fixture))
    claim = claim_next(database_path, mode="structured", claimed_by="test")
    with pytest.raises(CollectionError, match="captured original posting"):
        finish_scan(
            database_path, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
            jobs=[{"canonical_url": "https://example.org/1", "title": "Engineer"}],
            complete=True,
        )
    connection = connect(database_path, read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM job_scans WHERE scan_id = ?", (claim["scan_id"],)
        ).fetchone()[0] == "running"
    finally:
        connection.close()
