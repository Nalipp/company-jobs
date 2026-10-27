"""SQLite claims, scan reconciliation, and unfiltered job persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any
from uuid import uuid4

from .db import apply_migrations, connect, transaction, utc_now_string
from .providers import Capture, CaptureError, FetchFailure, fetch_structured, normalize_url


LEASE_MINUTES = 120
SCAN_INTERVAL_HOURS = 24


class CollectionError(RuntimeError):
    """A scan cannot safely claim, submit, or reconcile its source."""


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _later(now: str, *, hours: int = 0, minutes: int = 0, days: int = 0) -> str:
    return _stamp(_instant(now) + timedelta(hours=hours, minutes=minutes, days=days))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_structured(source: dict[str, Any]) -> bool:
    return (
        source["provider"] in {"ashby", "greenhouse", "lever"}
        and source["structured_data"] == 1
        and source["access_mode"] == "structured_feed"
        and bool(source["board_id"])
    )


def _source_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(
        """
        SELECT s.*, last.scan_id AS last_scan_id, last.status AS last_status,
               last.source_record_revision AS last_source_revision,
               last.next_due_at AS last_next_due_at,
               last.lease_expires_at AS last_lease_expires_at,
               last.notes AS last_notes,
               last.error_class AS last_error_class,
               last.fetch_summary_json AS last_fetch_summary_json
          FROM collectible_sources AS s
          LEFT JOIN job_scans AS last ON last.scan_id = (
              SELECT scan_id FROM job_scans
               WHERE source_id = s.source_id
               ORDER BY started_at DESC, rowid DESC LIMIT 1
          )
         ORDER BY s.source_id
        """
    )]


def _due(source: dict[str, Any], now: str) -> bool:
    if source["last_status"] == "running":
        return source["last_lease_expires_at"] <= now
    if source["last_scan_id"] is None:
        return True
    if source["last_source_revision"] != source["source_record_revision"]:
        return True
    return source["last_next_due_at"] is not None and source["last_next_due_at"] <= now


def status(database: Path) -> dict[str, Any]:
    connection = connect(database, read_only=True)
    try:
        now = utc_now_string()
        sources = _source_rows(connection)
        scans = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM job_scans GROUP BY status"
            )
        }
        return {
            "database": str(database.resolve()),
            "collectible_sources": len(sources),
            "due_structured": sum(_due(s, now) and is_structured(s) for s in sources),
            "due_manual": sum(_due(s, now) and not is_structured(s) for s in sources),
            "active_jobs": connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'active'"
            ).fetchone()[0],
            "all_jobs": connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "scan_counts": scans,
        }
    finally:
        connection.close()


def claim_next(
    database: Path, *, mode: str, claimed_by: str,
    source_id: str | None = None,
) -> dict[str, Any]:
    if mode not in {"structured", "manual"}:
        raise CollectionError("mode must be structured or manual")
    if not claimed_by.strip():
        raise CollectionError("claimed_by is required")
    connection = connect(database)
    try:
        apply_migrations(connection)
        now = utc_now_string()
        with transaction(connection):
            connection.execute(
                """
                UPDATE job_scans
                   SET status = 'abandoned', finished_at = ?, next_due_at = ?,
                       notes = 'lease expired before finalization'
                 WHERE status = 'running' AND lease_expires_at <= ?
                """,
                (now, now, now),
            )
            selected = next(
                (
                    row for row in _source_rows(connection)
                    if (source_id is None or row["source_id"] == source_id)
                    and is_structured(row) == (mode == "structured")
                    and _due(row, now)
                ),
                None,
            )
            if selected is None:
                return {"execution_status": "no_task", "mode": mode}
            scan_id = f"scan:{uuid4()}"
            claim_token = secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO job_scans(
                    scan_id, source_id, source_record_revision, claimed_by,
                    claim_token, started_at, lease_expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    scan_id, selected["source_id"], selected["source_record_revision"],
                    claimed_by, claim_token, now, _later(now, minutes=LEASE_MINUTES),
                ),
            )
            recipe = connection.execute(
                "SELECT source_record_revision, recipe_json FROM source_collection_recipes "
                "WHERE source_id = ?",
                (selected["source_id"],),
            ).fetchone()
            source = {
                key: value for key, value in selected.items()
                if not key.startswith("last_")
            }
            return {
                "execution_status": "claimed", "mode": mode,
                "scan_id": scan_id, "claim_token": claim_token,
                "lease_expires_at": _later(now, minutes=LEASE_MINUTES),
                "source": source,
                "previous_scan": {
                    "status": selected["last_status"],
                    "notes": selected["last_notes"],
                    "error_class": selected["last_error_class"],
                    "summary": json.loads(selected["last_fetch_summary_json"])
                    if selected["last_fetch_summary_json"] else None,
                },
                "recipe": (
                    json.loads(recipe["recipe_json"])
                    if recipe is not None
                    and recipe["source_record_revision"] == selected["source_record_revision"]
                    else None
                ),
            }
    finally:
        connection.close()


def renew(database: Path, *, scan_id: str, claim_token: str) -> dict[str, Any]:
    connection = connect(database)
    try:
        now = utc_now_string()
        lease = _later(now, minutes=LEASE_MINUTES)
        with transaction(connection):
            cursor = connection.execute(
                """
                UPDATE job_scans SET lease_expires_at = ?
                 WHERE scan_id = ? AND claim_token = ? AND status = 'running'
                   AND lease_expires_at > ?
                """,
                (lease, scan_id, claim_token, now),
            )
            if cursor.rowcount != 1:
                raise CollectionError("scan claim is no longer active")
        return {"scan_id": scan_id, "lease_expires_at": lease}
    finally:
        connection.close()


def _validated_jobs(jobs: list[dict[str, Any]], source_id: str) -> list[dict[str, Any]]:
    if not isinstance(jobs, list):
        raise CollectionError("jobs must be an array")
    validated: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_urls: set[str] = set()
    for item in jobs:
        if not isinstance(item, dict):
            raise CollectionError("each job must be an object")
        url = normalize_url(item.get("canonical_url"))
        provider_id = item.get("source_job_id")
        if provider_id is not None and not str(provider_id).strip():
            raise CollectionError("source_job_id cannot be empty")
        key = str(provider_id).strip() if provider_id is not None else url
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise CollectionError("each job needs a title")
        raw = item.get("raw_posting")
        if not isinstance(raw, dict) or not raw:
            raise CollectionError("each job needs the captured original posting")
        details = item.get("details") or {"capture_method": "manual"}
        if not isinstance(details, dict) or not details:
            raise CollectionError("job details must be an object")
        locations = item.get("locations") or []
        if not isinstance(locations, list) or any(not isinstance(x, str) for x in locations):
            raise CollectionError("job locations must be an array of strings")
        if key in seen_keys or url in seen_urls:
            raise CollectionError("duplicate job identity or canonical URL in one scan")
        seen_keys.add(key)
        seen_urls.add(url)
        job_id = "job-" + sha256(f"{source_id}\0{key}".encode()).hexdigest()[:24]
        validated.append({
            "job_id": job_id, "source_id": source_id,
            "source_job_key": key,
            "source_job_id": str(provider_id).strip() if provider_id is not None else None,
            "canonical_url": url, "title": title.strip(),
            "description_text": str(item.get("description_text") or ""),
            "posting_locations_json": _json(locations),
            "workplace_text": item.get("workplace_text"),
            "employment_text": item.get("employment_text"),
            "compensation_text": item.get("compensation_text"),
            "published_at": item.get("published_at"),
            "job_details_json": _json(details),
            "raw_posting_json": _json(raw),
        })
    return validated


def finish_scan(
    database: Path, *, scan_id: str, claim_token: str,
    jobs: list[dict[str, Any]], complete: bool,
    summary: dict[str, Any] | None = None,
    notes: str | None = None, recipe: dict[str, Any] | None = None,
    manual: bool = False, completeness_evidence: str | None = None,
    identity_evidence: str | None = None,
    retry_after_hours: int | None = None,
) -> dict[str, Any]:
    if not isinstance(complete, bool):
        raise CollectionError("complete must be true or false")
    if manual and complete and not (completeness_evidence or "").strip():
        raise CollectionError("complete manual scans require enumeration evidence")
    if recipe is not None and (not isinstance(recipe, dict) or not recipe):
        raise CollectionError("recipe must be a nonempty object")
    if retry_after_hours is not None and (
        not isinstance(retry_after_hours, int) or isinstance(retry_after_hours, bool)
        or retry_after_hours < 1 or retry_after_hours > 168
    ):
        raise CollectionError("retry_after_hours must be between 1 and 168")
    connection = connect(database)
    try:
        scan = connection.execute(
            "SELECT * FROM job_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if scan is None or scan["claim_token"] != claim_token or scan["status"] != "running":
            raise CollectionError("scan claim is missing or no longer active")
        now = utc_now_string()
        if scan["lease_expires_at"] <= now:
            raise CollectionError("scan lease expired; recover by claiming again")
        validated = _validated_jobs(jobs, scan["source_id"])
        with transaction(connection):
            scan = connection.execute(
                "SELECT * FROM job_scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if (
                scan is None or scan["claim_token"] != claim_token
                or scan["status"] != "running" or scan["lease_expires_at"] <= now
            ):
                raise CollectionError("scan claim changed before finalization")
            source = connection.execute(
                "SELECT * FROM collectible_sources WHERE source_id = ?",
                (scan["source_id"],),
            ).fetchone()
            if source is None or source["source_record_revision"] != scan["source_record_revision"]:
                raise CollectionError("source is no longer collectible at the claimed revision")
            if manual and complete and source["stable_job_identity"] == 0 and not (identity_evidence or "").strip():
                raise CollectionError("stable job identity requires evidence for this source")
            created = 0
            updated = 0
            for item in validated:
                existing = connection.execute(
                    "SELECT 1 FROM jobs WHERE job_id = ?", (item["job_id"],)
                ).fetchone()
                if existing is None:
                    created += 1
                else:
                    updated += 1
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, source_id, source_job_key, source_job_id,
                        canonical_url, title, description_text,
                        posting_locations_json, workplace_text, employment_text,
                        compensation_text, published_at, source_record_revision,
                        first_seen_at, last_seen_at, status, inactive_at,
                        raw_posting_json, job_details_json, last_scan_id
                    ) VALUES (
                        :job_id, :source_id, :source_job_key, :source_job_id,
                        :canonical_url, :title, :description_text,
                        :posting_locations_json, :workplace_text, :employment_text,
                        :compensation_text, :published_at, :source_record_revision,
                        :seen_at, :seen_at, 'active', NULL,
                        :raw_posting_json, :job_details_json, :last_scan_id
                    )
                    ON CONFLICT(job_id) DO UPDATE SET
                        source_job_id = excluded.source_job_id,
                        canonical_url = excluded.canonical_url,
                        title = excluded.title,
                        description_text = excluded.description_text,
                        posting_locations_json = excluded.posting_locations_json,
                        workplace_text = excluded.workplace_text,
                        employment_text = excluded.employment_text,
                        compensation_text = excluded.compensation_text,
                        published_at = excluded.published_at,
                        source_record_revision = excluded.source_record_revision,
                        last_seen_at = excluded.last_seen_at,
                        status = 'active', inactive_at = NULL,
                        raw_posting_json = excluded.raw_posting_json,
                        job_details_json = excluded.job_details_json,
                        last_scan_id = excluded.last_scan_id
                    """,
                    {**item, "source_record_revision": scan["source_record_revision"],
                     "seen_at": now, "last_scan_id": scan_id},
                )
            closed = 0
            if complete:
                closed = connection.execute(
                    """
                    UPDATE jobs SET status = 'inactive', inactive_at = ?
                     WHERE source_id = ? AND status = 'active'
                       AND last_scan_id <> ?
                    """,
                    (now, scan["source_id"], scan_id),
                ).rowcount
            scan_status = "complete" if complete else "incomplete"
            due = _later(
                now, hours=SCAN_INTERVAL_HOURS if complete else
                (retry_after_hours or SCAN_INTERVAL_HOURS)
            )
            connection.execute(
                """
                UPDATE job_scans SET status = ?, finished_at = ?, next_due_at = ?,
                       observed_jobs = ?, notes = ?, fetch_summary_json = ?
                 WHERE scan_id = ? AND status = 'running'
                """,
                (
                    scan_status, now, due, len(validated), notes,
                    _json({**(summary or {}),
                           "completeness_evidence": completeness_evidence,
                           "identity_evidence": identity_evidence}), scan_id,
                ),
            )
            if recipe is not None and complete:
                connection.execute(
                    """
                    INSERT INTO source_collection_recipes(
                        source_id, source_record_revision, recipe_json,
                        validated_at, last_scan_id
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        source_record_revision = excluded.source_record_revision,
                        recipe_json = excluded.recipe_json,
                        validated_at = excluded.validated_at,
                        last_scan_id = excluded.last_scan_id
                    """,
                    (scan["source_id"], scan["source_record_revision"], _json(recipe), now, scan_id),
                )
        return {
            "scan_id": scan_id, "source_id": scan["source_id"],
            "status": scan_status, "observed_jobs": len(validated),
            "created": created, "updated": updated, "inactivated": closed,
            "next_due_at": due,
        }
    finally:
        connection.close()


def block_scan(
    database: Path, *, scan_id: str, claim_token: str,
    error_class: str, notes: str,
) -> dict[str, Any]:
    if not notes.strip():
        raise CollectionError("blocked scan requires a reason")
    connection = connect(database)
    try:
        now = utc_now_string()
        if error_class == "environment_network":
            due = _later(now, minutes=5)
        elif error_class in {"source_missing", "unsupported", "invalid_output"}:
            due = _later(now, days=7)
        elif error_class == "rate_limit":
            due = _later(now, hours=4)
        else:
            due = _later(now, hours=1)
        with transaction(connection):
            cursor = connection.execute(
                """
                UPDATE job_scans
                   SET status = 'blocked', finished_at = ?, next_due_at = ?,
                       error_class = ?, notes = ?
                 WHERE scan_id = ? AND claim_token = ? AND status = 'running'
                   AND lease_expires_at > ?
                """,
                (now, due, error_class, notes, scan_id, claim_token, now),
            )
            if cursor.rowcount != 1:
                raise CollectionError("scan claim is missing or no longer active")
        return {"scan_id": scan_id, "status": "blocked", "error_class": error_class,
                "next_due_at": due, "notes": notes}
    finally:
        connection.close()


def run_structured(
    database: Path, *, limit: int = 25,
    source_id: str | None = None, claimed_by: str = "local-structured",
    fetcher: Any = None,
) -> dict[str, Any]:
    if limit < 1:
        raise CollectionError("limit must be positive")
    reports: list[dict[str, Any]] = []
    stop_reason = "limit_reached"
    for _ in range(limit):
        claim = claim_next(database, mode="structured", claimed_by=claimed_by,
                           source_id=source_id)
        if claim["execution_status"] == "no_task":
            stop_reason = "no_due_structured_sources"
            break
        try:
            kwargs = {"fetcher": fetcher} if fetcher is not None else {}
            capture: Capture = fetch_structured(claim["source"], **kwargs)
            report = finish_scan(
                database, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
                jobs=capture.jobs, complete=capture.complete,
                summary=capture.summary,
                notes=capture.summary.get("completeness_reason"),
            )
        except FetchFailure as exc:
            report = block_scan(
                database, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
                error_class=exc.category, notes=str(exc),
            )
            if exc.category == "environment_network":
                reports.append(report)
                stop_reason = "environment_network"
                break
        except (CaptureError, CollectionError, ValueError, TypeError, sqlite3.Error) as exc:
            report = block_scan(
                database, scan_id=claim["scan_id"], claim_token=claim["claim_token"],
                error_class="invalid_output", notes=str(exc),
            )
        reports.append(report)
        if source_id is not None:
            stop_reason = "requested_source_processed"
            break
    return {
        "processed_sources": len(reports),
        "complete": sum(item["status"] == "complete" for item in reports),
        "incomplete": sum(item["status"] == "incomplete" for item in reports),
        "blocked": sum(item["status"] == "blocked" for item in reports),
        "observed_jobs": sum(item.get("observed_jobs", 0) for item in reports),
        "stop_reason": stop_reason,
        "results": reports,
    }
