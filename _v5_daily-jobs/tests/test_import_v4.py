from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from v5_daily_jobs.db import connect
from v5_daily_jobs.import_v4 import (
    V4ImportError,
    canonical_json,
    import_snapshot,
    load_v4_snapshot,
    preview_report,
    verify_database,
)


def database_dump(path: Path) -> str:
    connection = connect(path, read_only=True)
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def test_preview_maps_fixture_without_database(v4_fixture: Path, database_path: Path) -> None:
    snapshot = load_v4_snapshot(v4_fixture)
    report = preview_report(snapshot)
    assert report["v4_counts"]["companies"] == 4
    assert report["v4_counts"]["sources"] == 3
    assert report["mapped_counts"]["current_locations"] == 3
    assert report["mapped_counts"]["eligible_companies"] == 1
    assert report["mapped_counts"]["collectible_sources"] == 1
    assert report["mapped_counts"]["total_research_records"] == 3
    assert not database_path.exists()


def test_import_reconciles_eligibility_and_research(
    v4_fixture: Path,
    database_path: Path,
) -> None:
    snapshot = load_v4_snapshot(v4_fixture)
    report = import_snapshot(database_path, snapshot)
    assert report["changed"] is True
    assert report["verification"]["ok"] is True

    connection = connect(database_path, read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM company_locations").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM eligible_companies").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM collectible_sources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute(
            "SELECT instructions FROM career_sources WHERE source_id = 'verified-ashby'"
        ).fetchone()[0] == "Use the official structured feed."
        assert (
            connection.execute("SELECT company_id FROM eligible_companies").fetchone()[0]
            == "verified-co"
        )
        assert (
            connection.execute("SELECT source_id FROM collectible_sources").fetchone()[0]
            == "verified-ashby"
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM company_research_records").fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM company_research_records "
                "WHERE instr(payload_json, 'claim_token') > 0"
            ).fetchone()[0]
            == 0
        )
        unknown = connection.execute(
            "SELECT company_id, referenced_company_id FROM company_research_records "
            "WHERE research_record_id = 'v3-archive:unknown:issue:1'"
        ).fetchone()
        assert unknown["company_id"] is None
        assert unknown["referenced_company_id"] == "unknown-co"
    finally:
        connection.close()


def test_identical_import_is_byte_for_byte_logical_no_op(
    v4_fixture: Path,
    database_path: Path,
) -> None:
    snapshot = load_v4_snapshot(v4_fixture)
    first = import_snapshot(database_path, snapshot)
    before = database_dump(database_path)
    second = import_snapshot(database_path, snapshot)
    after = database_dump(database_path)
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["no_op_reason"] == "source_digest_already_imported"
    assert before == after


def test_queue_resolution_recovers_missing_applied_result(
    missing_result_fixture: Path,
    database_path: Path,
) -> None:
    snapshot = load_v4_snapshot(missing_result_fixture)
    assert len(snapshot.applied_results) == 1
    assert snapshot.applied_results[0].recovered_from_queue is True
    report = import_snapshot(database_path, snapshot)
    assert report["verification"]["ok"] is True
    connection = connect(database_path, read_only=True)
    try:
        payload = connection.execute(
            "SELECT payload_json FROM company_research_records "
            "WHERE source_kind = 'v4_company_result'"
        ).fetchone()[0]
        assert json.loads(payload)["provenance"] == "recovered_from_v4_company_queue"
    finally:
        connection.close()


def test_newly_verified_company_becomes_eligible_on_next_import(
    v4_fixture: Path,
    database_path: Path,
) -> None:
    first = load_v4_snapshot(v4_fixture)
    import_snapshot(database_path, first)

    canonical_path = v4_fixture / "data" / "companies.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    pending = next(item for item in canonical["companies"] if item["company_id"] == "pending-co")
    pending.update(
        {
            "record_revision": 2,
            "address_status": "verified",
            "latitude": 37.79,
            "longitude": -122.39,
            "address_source_url": "https://pending-co.example/contact",
            "address_source_label": "Official contact page",
            "verified_date": "2026-09-02T12:00:00Z",
            "updated_date": "2026-09-02T12:00:00Z",
            "career_sources": [
                {
                    **deepcopy(canonical["companies"][0]["career_sources"][0]),
                    "source_id": "pending-ashby",
                    "company_id": "pending-co",
                    "board_id": "pending-co",
                    "careers_url": "https://pending-co.example/careers",
                    "listings_url": "https://jobs.ashbyhq.com/pending-co",
                    "official_relationship_url": "https://pending-co.example/careers",
                }
            ],
        }
    )
    canonical["revision"] += 1
    canonical_path.write_text(canonical_json(canonical), encoding="utf-8")

    second = load_v4_snapshot(v4_fixture)
    report = import_snapshot(database_path, second)
    assert report["verification"]["ok"] is True
    assert report["verification"]["database_counts"]["eligible_companies"] == 2
    assert report["verification"]["database_counts"]["collectible_sources"] == 2


def test_invalid_v4_input_does_not_create_import_run(
    v4_fixture: Path,
    database_path: Path,
) -> None:
    valid = load_v4_snapshot(v4_fixture)
    import_snapshot(database_path, valid)
    canonical_path = v4_fixture / "data" / "companies.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    verified = next(item for item in canonical["companies"] if item["address_status"] == "verified")
    verified["latitude"] = None
    canonical["revision"] += 1
    canonical_path.write_text(canonical_json(canonical), encoding="utf-8")

    with pytest.raises(V4ImportError, match="lacks latitude"):
        load_v4_snapshot(v4_fixture)
    connection = connect(database_path, read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_verify_matches_imported_fixture(v4_fixture: Path, database_path: Path) -> None:
    snapshot = load_v4_snapshot(v4_fixture)
    import_snapshot(database_path, snapshot)
    report = verify_database(database_path, snapshot)
    assert report["ok"] is True
    assert report["integrity"]["ok"] is True
    assert report["unexpected_job_tables"] == []
    assert report["source_instruction_mismatches"] == []


def test_verify_detects_changed_source_instructions(
    v4_fixture: Path, database_path: Path
) -> None:
    snapshot = load_v4_snapshot(v4_fixture)
    import_snapshot(database_path, snapshot)
    connection = connect(database_path)
    try:
        connection.execute(
            "UPDATE career_sources SET instructions = 'Changed' "
            "WHERE source_id = 'verified-ashby'"
        )
    finally:
        connection.close()
    report = verify_database(database_path, snapshot)
    assert report["ok"] is False
    assert report["source_instruction_mismatches"] == ["verified-ashby"]
