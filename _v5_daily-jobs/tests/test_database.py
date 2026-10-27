from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from v5_daily_jobs.db import apply_migrations, connect, integrity_report, schema_version


def test_schema_migration_and_integrity(database_path: Path) -> None:
    connection = connect(database_path)
    try:
        assert apply_migrations(connection) == [1, 2, 3]
        assert apply_migrations(connection) == []
        assert schema_version(connection) == 3
        assert integrity_report(connection)["ok"] is True

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "companies" in tables
        assert "company_locations" in tables
        assert "career_sources" in tables
        assert "jobs" in tables
        assert "job_scans" in tables
        assert "source_collection_recipes" in tables
        assert "job_versions" not in tables
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        connection.close()


def test_jobs_require_source_and_stable_source_scoped_identity(database_path: Path) -> None:
    connection = connect(database_path)
    try:
        apply_migrations(connection)
        connection.execute(
            """
            INSERT INTO import_runs(
                import_run_id, source_system, source_root, source_revision,
                source_digest, company_document_sha256, research_document_sha256,
                started_at, completed_at, status
            ) VALUES ('run', 'v4', '/fixture', 1, ?, ?, ?, 't', 't', 'completed')
            """,
            ("a" * 64, "b" * 64, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO companies(
                company_id, company_name, sf_validation_status,
                source_record_revision, source_added_at, source_updated_at,
                source_system, last_import_run_id
            ) VALUES ('co', 'Company', 'verified', 1, 't', 't', 'v4', 'run')
            """
        )
        connection.execute(
            """
            INSERT INTO career_sources(
                source_id, company_id, provider, scope, instructions,
                complete_enumeration, stable_job_identity, structured_data,
                status, checked_at, source_record_revision, source_added_at,
                source_updated_at, source_system, last_import_run_id
            ) VALUES (
                'source', 'co', 'ashby', 'All roles', 'Enumerate the board',
                1, 1, 1, 'ready', 't', 1, 't', 't', 'v4', 'run'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO job_scans(
                scan_id, source_id, source_record_revision, claimed_by,
                claim_token, started_at, lease_expires_at, status
            ) VALUES ('scan', 'source', 1, 'test', ?, 't', 'u', 'running')
            """,
            ("x" * 32,),
        )
        statement = """
            INSERT INTO jobs(
                job_id, source_id, source_job_key, canonical_url, title,
                source_record_revision, first_seen_at, last_seen_at, status,
                raw_posting_json, job_details_json, last_scan_id
            ) VALUES (?, ?, ?, ?, ?, 1, 't', 't', 'active', '{}',
                      '{"capture_method":"test"}', 'scan')
        """
        with pytest.raises(sqlite3.IntegrityError, match="not currently collectible"):
            connection.execute(
                statement, ("job-0", "source", "provider-0", "https://example.org/0", "Engineer")
            )
        connection.execute(
            """
            INSERT INTO company_locations(
                location_id, company_id, address_hash, validation_status,
                is_physical, is_in_san_francisco, latitude, longitude,
                source_system, source_record_revision, source_created_at,
                source_updated_at, last_import_run_id
            ) VALUES (
                'location', 'co', ?, 'verified', 1, 1, 37.7, -122.4,
                'v4', 1, 't', 't', 'run'
            )
            """,
            ("d" * 64,),
        )
        connection.execute(
            statement, ("job-1", "source", "provider-123", "https://example.org/1", "Engineer")
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                statement, ("job-2", "source", "provider-123", "https://example.org/2", "Engineer")
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                statement, ("job-3", "missing", "provider-456", "https://example.org/3", "Engineer")
            )
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        connection.close()


def test_schema_allows_only_one_primary_location(database_path: Path) -> None:
    connection = connect(database_path)
    try:
        apply_migrations(connection)
        connection.execute(
            """
            INSERT INTO import_runs(
                import_run_id, source_system, source_root, source_revision,
                source_digest, company_document_sha256, research_document_sha256,
                started_at, completed_at, status
            ) VALUES ('run', 'v4', '/fixture', 1, ?, ?, ?, 't', 't', 'completed')
            """,
            ("a" * 64, "b" * 64, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO companies(
                company_id, company_name, sf_validation_status,
                source_record_revision, source_added_at, source_updated_at,
                source_system, last_import_run_id
            ) VALUES ('co', 'Company', 'verified', 1, 't', 't', 'v4', 'run')
            """
        )
        statement = """
            INSERT INTO company_locations(
                location_id, company_id, address_hash, validation_status,
                is_physical, is_in_san_francisco, is_primary_map_location,
                latitude, longitude, source_system, source_record_revision,
                is_current, source_created_at, source_updated_at, last_import_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        first = (
            "loc-1", "co", "c" * 64, "verified", 1, 1, 1,
            37.7, -122.4, "v4", 1, 1, "t", "t", "run",
        )
        connection.execute(statement, first)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, ("loc-2", *first[1:2], "d" * 64, *first[3:]))
    finally:
        connection.close()
