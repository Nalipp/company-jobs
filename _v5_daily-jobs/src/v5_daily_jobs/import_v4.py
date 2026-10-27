"""Read-only V4 snapshot loading and transactional Phase 1 import."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from .db import (
    apply_migrations,
    connect,
    integrity_report,
    schema_version,
    transaction,
    utc_now_string,
)


IMPORT_MAPPING_VERSION = "v4_company_foundation_v1"
COMPANY_STATUSES = {"pending", "verified", "outside_sf", "not_found"}
SOURCE_STATUSES = {"ready", "no_careers_found", "blocked", "unsupported", "stale"}


class V4ImportError(RuntimeError):
    """Raised when V4 input or its mapped database state is unsafe."""


@dataclass(frozen=True)
class AppliedCompanyResult:
    digest: str
    relative_path: str
    document: dict[str, Any]
    recovered_from_queue: bool = False


@dataclass(frozen=True)
class V4Snapshot:
    root: Path
    canonical: dict[str, Any]
    canonical_sha256: str
    research: dict[str, Any]
    research_sha256: str
    applied_results: tuple[AppliedCompanyResult, ...]
    source_digest: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _load_json_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise V4ImportError(f"Cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise V4ImportError(f"JSON document must be an object: {path}")
    return value, raw


@contextmanager
def _shared_lock(path: Path) -> Iterator[None]:
    try:
        # A shared flock needs only a readable descriptor. Opening read-only is
        # deliberate: V5 must never create or modify a V4 lock artifact.
        handle = path.open("rb")
    except OSError as exc:
        raise V4ImportError(f"Cannot open V4 lock {path}: {exc}") from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V4ImportError(f"{label} must be a non-empty string")
    return value


def _validate_address_company(company: Mapping[str, Any]) -> None:
    company_id = str(company.get("company_id") or "<unknown>")
    status = company.get("address_status")
    address = company.get("address")
    latitude = company.get("latitude")
    longitude = company.get("longitude")
    if status in {"verified", "outside_sf"}:
        if not isinstance(address, Mapping):
            raise V4ImportError(f"{company_id}: {status} company lacks address")
        if not isinstance(latitude, (int, float)) or isinstance(latitude, bool):
            raise V4ImportError(f"{company_id}: {status} company lacks latitude")
        if not isinstance(longitude, (int, float)) or isinstance(longitude, bool):
            raise V4ImportError(f"{company_id}: {status} company lacks longitude")
        _require_text(company.get("address_source_url"), f"{company_id}.address_source_url")
        _require_text(
            company.get("address_source_label"),
            f"{company_id}.address_source_label",
        )
        _require_text(company.get("verified_date"), f"{company_id}.verified_date")
    if status == "not_found":
        forbidden = (
            address,
            latitude,
            longitude,
            company.get("address_source_url"),
            company.get("address_source_label"),
        )
        if any(value is not None for value in forbidden):
            raise V4ImportError(f"{company_id}: not_found company retains address facts")
        _require_text(company.get("verified_date"), f"{company_id}.verified_date")


def _validate_canonical(document: Mapping[str, Any]) -> None:
    revision = document.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise V4ImportError("V4 companies revision must be a positive integer")
    companies = document.get("companies")
    if not isinstance(companies, list):
        raise V4ImportError("V4 canonical document lacks companies array")
    company_ids: set[str] = set()
    source_ids: set[str] = set()
    for company in companies:
        if not isinstance(company, Mapping):
            raise V4ImportError("V4 company entries must be objects")
        company_id = _require_text(company.get("company_id"), "company_id")
        if company_id in company_ids:
            raise V4ImportError(f"Duplicate V4 company ID {company_id}")
        company_ids.add(company_id)
        _require_text(company.get("company_name"), f"{company_id}.company_name")
        status = company.get("address_status")
        if status not in COMPANY_STATUSES:
            raise V4ImportError(f"{company_id}: invalid address status {status!r}")
        source_revision = company.get("record_revision")
        if not isinstance(source_revision, int) or source_revision < 1:
            raise V4ImportError(f"{company_id}: invalid record revision")
        _validate_address_company(company)
        sources = company.get("career_sources")
        if not isinstance(sources, list):
            raise V4ImportError(f"{company_id}: career_sources must be an array")
        for source in sources:
            if not isinstance(source, Mapping):
                raise V4ImportError(f"{company_id}: source entries must be objects")
            source_id = _require_text(source.get("source_id"), "source_id")
            if source_id in source_ids:
                raise V4ImportError(f"Duplicate V4 source ID {source_id}")
            source_ids.add(source_id)
            if source.get("company_id") != company_id:
                raise V4ImportError(f"{source_id}: source ownership mismatch")
            if source.get("status") not in SOURCE_STATUSES:
                raise V4ImportError(f"{source_id}: invalid status {source.get('status')!r}")
            capabilities = source.get("capabilities")
            if not isinstance(capabilities, Mapping):
                raise V4ImportError(f"{source_id}: missing capabilities")
            for key in ("complete_enumeration", "stable_job_identity", "structured_data"):
                if not isinstance(capabilities.get(key), bool):
                    raise V4ImportError(f"{source_id}: capability {key} must be boolean")
    ledger = document.get("application_ledger")
    if not isinstance(ledger, list):
        raise V4ImportError("V4 canonical document lacks application_ledger")
    ledger_digests: set[str] = set()
    for entry in ledger:
        if not isinstance(entry, Mapping):
            raise V4ImportError("V4 application ledger entries must be objects")
        digest = entry.get("result_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise V4ImportError("V4 application ledger contains invalid result digest")
        if digest in ledger_digests:
            raise V4ImportError(f"Duplicate V4 application result digest {digest}")
        ledger_digests.add(digest)


def _validate_research(document: Mapping[str, Any]) -> None:
    records = document.get("records")
    if not isinstance(records, list):
        raise V4ImportError("V4 research archive lacks records array")
    if document.get("record_count") != len(records):
        raise V4ImportError("V4 research record_count does not match records")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise V4ImportError("V4 research records must be objects")
        record_id = _require_text(record.get("record_id"), "research record_id")
        if record_id in seen:
            raise V4ImportError(f"Duplicate V4 research record {record_id}")
        seen.add(record_id)
        if not isinstance(record.get("issue"), Mapping):
            raise V4ImportError(f"Research record {record_id} lacks issue object")


def _company_result_paths(root: Path) -> tuple[Path, ...]:
    values = set((root / "data" / "results" / "companies").glob("*.json"))
    values.update((root / "data" / "results").glob("company-*.json"))
    return tuple(sorted(values))


def _sanitize_company_result(document: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(dict(document))
    sanitized.pop("claim_token", None)
    return sanitized


def _load_applied_company_results(
    root: Path,
    canonical: Mapping[str, Any],
) -> tuple[AppliedCompanyResult, ...]:
    expected = {
        str(entry["result_digest"]): entry
        for entry in canonical.get("application_ledger", [])
    }
    found: dict[str, AppliedCompanyResult] = {}
    for path in _company_result_paths(root):
        document, _ = _load_json_bytes(path)
        if document.get("workflow") != "validate_companies":
            continue
        digest = sha256_json(document)
        if digest not in expected:
            continue
        if digest in found:
            raise V4ImportError(f"Applied company result digest appears twice: {digest}")
        ledger = expected[digest]
        if document.get("task_id") != ledger.get("task_id"):
            raise V4ImportError(f"Company result task mismatch for digest {digest}")
        if document.get("attempt_id") != ledger.get("attempt_id"):
            raise V4ImportError(f"Company result attempt mismatch for digest {digest}")
        found[digest] = AppliedCompanyResult(
            digest=digest,
            relative_path=str(path.relative_to(root)),
            document=_sanitize_company_result(document),
        )
    missing = sorted(set(expected) - set(found))
    if missing:
        queue_path = root / "data" / "queues" / "company-queue.json"
        queue_lock = root / "data" / "queues" / "company-queue.lock"
        if not queue_path.exists() or not queue_lock.exists():
            raise V4ImportError(
                f"Cannot account for {len(missing)} applied V4 company results and "
                "the company queue recovery source is unavailable"
            )
        with _shared_lock(queue_lock):
            queue, _ = _load_json_bytes(queue_path)
        tasks = {
            str(task.get("task_id")): task
            for task in queue.get("tasks", [])
            if isinstance(task, Mapping) and task.get("task_id")
        }
        for digest in missing:
            ledger = expected[digest]
            task = tasks.get(str(ledger.get("task_id")))
            resolution = task.get("resolution") if isinstance(task, Mapping) else None
            if not isinstance(resolution, Mapping) or resolution.get("result_digest") != digest:
                attempts = task.get("attempts", []) if isinstance(task, Mapping) else []
                matching_attempt = next(
                    (
                        attempt
                        for attempt in attempts
                        if isinstance(attempt, Mapping)
                        and attempt.get("attempt_id") == ledger.get("attempt_id")
                        and isinstance(attempt.get("resolution"), Mapping)
                        and attempt["resolution"].get("result_digest") == digest
                    ),
                    None,
                )
                resolution = (
                    matching_attempt.get("resolution")
                    if isinstance(matching_attempt, Mapping)
                    else None
                )
            if not isinstance(task, Mapping) or not isinstance(resolution, Mapping):
                raise V4ImportError(
                    f"Applied V4 company result {digest} has neither a result file "
                    "nor a terminal queue resolution"
                )
            if resolution.get("result_digest") != digest:
                raise V4ImportError(
                    f"Queue recovery digest mismatch for task {ledger.get('task_id')}"
                )
            document = {
                "schema_version": 1,
                "provenance": "recovered_from_v4_company_queue",
                "original_result_file_missing": True,
                "workflow": "validate_companies",
                "task_id": task.get("task_id"),
                "attempt_id": ledger.get("attempt_id"),
                "model": task.get("expected_model"),
                "created_at": resolution.get("resolved_at") or task.get("updated_at"),
                "target": deepcopy(task.get("target")),
                "outcome": resolution.get("outcome"),
                "reason_code": resolution.get("reason_code"),
                "reason_codes": deepcopy(task.get("reason_codes", [])),
                "evidence_count": resolution.get("evidence_count"),
                "item_results": deepcopy(resolution.get("item_results", [])),
                "notes": resolution.get("notes"),
                "relevant_prior_evidence": deepcopy(
                    task.get("context", {}).get("relevant_evidence", [])
                ),
                "ledger": deepcopy(dict(ledger)),
            }
            found[digest] = AppliedCompanyResult(
                digest=digest,
                relative_path=(
                    "data/queues/company-queue.json#task=" + str(task.get("task_id"))
                ),
                document=document,
                recovered_from_queue=True,
            )
    return tuple(found[key] for key in sorted(found))


def load_v4_snapshot(root: Path) -> V4Snapshot:
    resolved = root.resolve()
    companies_path = resolved / "data" / "companies.json"
    lock_path = resolved / "data" / "companies.lock"
    research_path = resolved / "archive" / "v3" / "company-research.json"
    for path in (companies_path, lock_path, research_path):
        if not path.exists():
            raise V4ImportError(f"Required V4 input does not exist: {path}")
    with _shared_lock(lock_path):
        canonical, canonical_raw = _load_json_bytes(companies_path)
    _validate_canonical(canonical)
    research, research_raw = _load_json_bytes(research_path)
    _validate_research(research)
    applied_results = _load_applied_company_results(resolved, canonical)
    canonical_sha = sha256_bytes(canonical_raw)
    research_sha = sha256_bytes(research_raw)
    digest_material = "\0".join(
        (
            IMPORT_MAPPING_VERSION,
            canonical_sha,
            research_sha,
            *(
                f"{result.digest}:{sha256_json(result.document)}"
                for result in applied_results
            ),
        )
    ).encode("utf-8")
    return V4Snapshot(
        root=resolved,
        canonical=canonical,
        canonical_sha256=canonical_sha,
        research=research,
        research_sha256=research_sha,
        applied_results=applied_results,
        source_digest=sha256_bytes(digest_material),
    )


def _normalized_domain(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host or None


def _address_values(company: Mapping[str, Any]) -> dict[str, Any] | None:
    address = company.get("address")
    if not isinstance(address, Mapping):
        return None
    values = {
        key: address.get(key)
        for key in ("street", "city", "region", "postal_code", "country")
    }
    if not any(value is not None and str(value).strip() for value in values.values()):
        return None
    return values


def _normalize_address_value(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    return text.casefold() or None


def _location_mapping(company: Mapping[str, Any]) -> dict[str, Any] | None:
    address = _address_values(company)
    if address is None:
        return None
    normalized = {
        key: _normalize_address_value(address.get(key))
        for key in ("street", "city", "region", "postal_code", "country")
    }
    address_hash = sha256_bytes(compact_json(normalized).encode("utf-8"))
    location_id = "loc-" + sha256_bytes(
        f"{company['company_id']}\0{address_hash}".encode("utf-8")
    )[:24]
    status = company["address_status"]
    verified = status in {"verified", "outside_sf"}
    return {
        "location_id": location_id,
        "company_id": company["company_id"],
        "address_hash": address_hash,
        **address,
        "latitude": company.get("latitude"),
        "longitude": company.get("longitude"),
        "validation_status": "verified" if verified else "candidate",
        "is_physical": 1 if verified else 0,
        "is_in_san_francisco": (
            1 if status == "verified" else 0 if status == "outside_sf" else None
        ),
        "is_primary_map_location": 1 if status == "verified" else 0,
        "verified_at": company.get("verified_date") if verified else None,
        "source_system": "v4",
        "source_record_revision": company["record_revision"],
        "is_current": 1,
        "source_created_at": company["added_date"],
        "source_updated_at": company["updated_date"],
    }


def _source_records(snapshot: V4Snapshot) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for company in snapshot.canonical["companies"]:
        records.extend(deepcopy(company["career_sources"]))
    return records


def _duplicate_groups(snapshot: V4Snapshot) -> dict[str, Any]:
    domains: dict[str, list[str]] = defaultdict(list)
    boards: dict[tuple[str, str], list[str]] = defaultdict(list)
    for company in snapshot.canonical["companies"]:
        domain = _normalized_domain(company.get("website_url"))
        if domain:
            domains[domain].append(company["company_id"])
        for source in company["career_sources"]:
            if source.get("board_id"):
                boards[(source["provider"], source["board_id"])].append(source["source_id"])
    return {
        "website_domains": {
            key: values for key, values in sorted(domains.items()) if len(values) > 1
        },
        "provider_board_ids": [
            {"provider": key[0], "board_id": key[1], "source_ids": values}
            for key, values in sorted(boards.items())
            if len(values) > 1
        ],
    }


def preview_report(snapshot: V4Snapshot) -> dict[str, Any]:
    companies = snapshot.canonical["companies"]
    sources = _source_records(snapshot)
    locations = [value for company in companies if (value := _location_mapping(company))]
    evidence_count = sum(
        1
        for company in companies
        if _location_mapping(company) is not None
        and (company.get("address_source_url") or company.get("address_source_label"))
    )
    known_ids = {company["company_id"] for company in companies}
    archived_unknown = sorted(
        {
            str(record.get("issue", {}).get("company_id"))
            for record in snapshot.research["records"]
            if record.get("issue", {}).get("company_id")
            and record.get("issue", {}).get("company_id") not in known_ids
        }
    )
    verified_ids = {
        company["company_id"]
        for company in companies
        if company["address_status"] == "verified"
    }
    return {
        "schema_version": 1,
        "phase": "company_source_foundation",
        "mapping_version": IMPORT_MAPPING_VERSION,
        "dry_run": True,
        "changed": False,
        "would_change": True,
        "source": {
            "root": str(snapshot.root),
            "revision": snapshot.canonical["revision"],
            "source_digest": snapshot.source_digest,
            "company_document_sha256": snapshot.canonical_sha256,
            "research_document_sha256": snapshot.research_sha256,
        },
        "v4_counts": {
            "companies": len(companies),
            "companies_by_status": dict(
                sorted(Counter(company["address_status"] for company in companies).items())
            ),
            "sources": len(sources),
            "sources_by_status": dict(
                sorted(Counter(source["status"] for source in sources).items())
            ),
            "sources_by_provider": dict(
                sorted(Counter(source["provider"] for source in sources).items())
            ),
            "application_ledger": len(snapshot.canonical["application_ledger"]),
        },
        "mapped_counts": {
            "current_locations": len(locations),
            "verified_sf_locations": sum(
                value["validation_status"] == "verified"
                and value["is_in_san_francisco"] == 1
                for value in locations
            ),
            "current_location_evidence": evidence_count,
            "v3_archive_research": len(snapshot.research["records"]),
            "applied_v4_company_results": len(snapshot.applied_results),
            "v4_results_recovered_from_queue": sum(
                result.recovered_from_queue for result in snapshot.applied_results
            ),
            "total_research_records": (
                len(snapshot.research["records"]) + len(snapshot.applied_results)
            ),
            "eligible_companies": len(verified_ids),
            "collectible_sources": sum(
                source["status"] == "ready" and source["company_id"] in verified_ids
                for source in sources
            ),
        },
        "preserved_warnings": {
            "duplicates": _duplicate_groups(snapshot),
            "research_unknown_company_ids": archived_unknown,
        },
        "excluded": [
            "V4 job snapshots",
            "V4 job results",
            "V4 job queues and claims",
            "V4 profile verdicts",
        ],
    }


def _upsert_companies(
    connection: sqlite3.Connection,
    snapshot: V4Snapshot,
    import_run_id: str,
) -> None:
    connection.execute(
        "UPDATE companies SET is_current = 0 "
        "WHERE source_system = 'v4' AND is_current = 1"
    )
    statement = """
        INSERT INTO companies(
            company_id, company_name, website_url, sf_validation_status,
            validation_checked_at, source_record_revision, source_added_at,
            source_updated_at, source_system, is_current, last_import_run_id
        ) VALUES (
            :company_id, :company_name, :website_url, :address_status,
            :verified_date, :record_revision, :added_date,
            :updated_date, 'v4', 1, :import_run_id
        )
        ON CONFLICT(company_id) DO UPDATE SET
            company_name = excluded.company_name,
            website_url = excluded.website_url,
            sf_validation_status = excluded.sf_validation_status,
            validation_checked_at = excluded.validation_checked_at,
            source_record_revision = excluded.source_record_revision,
            source_added_at = excluded.source_added_at,
            source_updated_at = excluded.source_updated_at,
            source_system = 'v4',
            is_current = 1,
            last_import_run_id = excluded.last_import_run_id
    """
    for company in snapshot.canonical["companies"]:
        connection.execute(statement, {**company, "import_run_id": import_run_id})


def _upsert_locations_and_evidence(
    connection: sqlite3.Connection,
    snapshot: V4Snapshot,
    import_run_id: str,
) -> tuple[int, int]:
    connection.execute(
        """
        UPDATE company_locations
           SET is_current = 0,
               is_primary_map_location = 0,
               validation_status = CASE
                   WHEN validation_status IN ('candidate', 'verified') THEN 'stale'
                   ELSE validation_status
               END
         WHERE source_system = 'v4' AND is_current = 1
        """
    )
    location_statement = """
        INSERT INTO company_locations(
            location_id, company_id, address_hash, street, city, region,
            postal_code, country, latitude, longitude, validation_status,
            is_physical, is_in_san_francisco, is_primary_map_location,
            verified_at, source_system, source_record_revision, is_current,
            source_created_at, source_updated_at, last_import_run_id
        ) VALUES (
            :location_id, :company_id, :address_hash, :street, :city, :region,
            :postal_code, :country, :latitude, :longitude, :validation_status,
            :is_physical, :is_in_san_francisco, :is_primary_map_location,
            :verified_at, :source_system, :source_record_revision, :is_current,
            :source_created_at, :source_updated_at, :last_import_run_id
        )
        ON CONFLICT(location_id) DO UPDATE SET
            street = excluded.street,
            city = excluded.city,
            region = excluded.region,
            postal_code = excluded.postal_code,
            country = excluded.country,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            validation_status = excluded.validation_status,
            is_physical = excluded.is_physical,
            is_in_san_francisco = excluded.is_in_san_francisco,
            is_primary_map_location = excluded.is_primary_map_location,
            verified_at = excluded.verified_at,
            source_record_revision = excluded.source_record_revision,
            is_current = 1,
            source_created_at = excluded.source_created_at,
            source_updated_at = excluded.source_updated_at,
            last_import_run_id = excluded.last_import_run_id
    """
    evidence_statement = """
        INSERT INTO company_location_evidence(
            evidence_id, company_id, location_id, evidence_type, url, label,
            finding, observed_at, disposition, source_system, source_record_id,
            payload_json, last_import_run_id
        ) VALUES (
            :evidence_id, :company_id, :location_id, :evidence_type, :url, :label,
            :finding, :observed_at, :disposition, 'v4', :source_record_id,
            :payload_json, :last_import_run_id
        )
        ON CONFLICT(evidence_id) DO UPDATE SET
            url = excluded.url,
            label = excluded.label,
            finding = excluded.finding,
            observed_at = excluded.observed_at,
            disposition = excluded.disposition,
            payload_json = excluded.payload_json,
            last_import_run_id = excluded.last_import_run_id
    """
    location_count = 0
    evidence_count = 0
    for company in snapshot.canonical["companies"]:
        location = _location_mapping(company)
        if location is None:
            continue
        connection.execute(
            location_statement,
            {**location, "last_import_run_id": import_run_id},
        )
        location_count += 1
        url = company.get("address_source_url")
        label = company.get("address_source_label")
        if not url and not label:
            continue
        source_record_id = (
            f"company:{company['company_id']}:address:{location['address_hash']}"
        )
        evidence_id = "evidence-" + sha256_bytes(
            f"v4\0{source_record_id}".encode("utf-8")
        )[:24]
        payload = {
            "address_source_url": url,
            "address_source_label": label,
            "address_status": company["address_status"],
            "verified_date": company.get("verified_date"),
        }
        connection.execute(
            evidence_statement,
            {
                "evidence_id": evidence_id,
                "company_id": company["company_id"],
                "location_id": location["location_id"],
                "evidence_type": "canonical_address_source",
                "url": url,
                "label": label,
                "finding": label or "Canonical V4 address evidence",
                "observed_at": company.get("verified_date") or company["updated_date"],
                "disposition": (
                    "supports"
                    if company["address_status"] in {"verified", "outside_sf"}
                    else "context"
                ),
                "source_record_id": source_record_id,
                "payload_json": compact_json(payload),
                "last_import_run_id": import_run_id,
            },
        )
        evidence_count += 1
    return location_count, evidence_count


def _upsert_sources(
    connection: sqlite3.Connection,
    snapshot: V4Snapshot,
    import_run_id: str,
) -> int:
    connection.execute(
        "UPDATE career_sources SET is_current = 0 "
        "WHERE source_system = 'v4' AND is_current = 1"
    )
    statement = """
        INSERT INTO career_sources(
            source_id, company_id, careers_url, listings_url, provider, board_id,
            access_mode, scope, instructions, complete_enumeration,
            stable_job_identity, structured_data, status,
            official_relationship_url, checked_at, last_success_at,
            source_record_revision, source_added_at, source_updated_at,
            source_system, is_current, last_import_run_id
        ) VALUES (
            :source_id, :company_id, :careers_url, :listings_url, :provider, :board_id,
            :access_mode, :scope, :instructions, :complete_enumeration,
            :stable_job_identity, :structured_data, :status,
            :official_relationship_url, :checked_at, :last_success_at,
            :record_revision, :added_date, :updated_date, 'v4', 1, :last_import_run_id
        )
        ON CONFLICT(source_id) DO UPDATE SET
            company_id = excluded.company_id,
            careers_url = excluded.careers_url,
            listings_url = excluded.listings_url,
            provider = excluded.provider,
            board_id = excluded.board_id,
            access_mode = excluded.access_mode,
            scope = excluded.scope,
            instructions = excluded.instructions,
            complete_enumeration = excluded.complete_enumeration,
            stable_job_identity = excluded.stable_job_identity,
            structured_data = excluded.structured_data,
            status = excluded.status,
            official_relationship_url = excluded.official_relationship_url,
            checked_at = excluded.checked_at,
            last_success_at = excluded.last_success_at,
            source_record_revision = excluded.source_record_revision,
            source_added_at = excluded.source_added_at,
            source_updated_at = excluded.source_updated_at,
            source_system = 'v4',
            is_current = 1,
            last_import_run_id = excluded.last_import_run_id
    """
    sources = _source_records(snapshot)
    for source in sources:
        capabilities = source["capabilities"]
        connection.execute(
            statement,
            {
                **source,
                "complete_enumeration": int(capabilities["complete_enumeration"]),
                "stable_job_identity": int(capabilities["stable_job_identity"]),
                "structured_data": int(capabilities["structured_data"]),
                "last_import_run_id": import_run_id,
            },
        )
    return len(sources)


def _insert_research(
    connection: sqlite3.Connection,
    snapshot: V4Snapshot,
    import_run_id: str,
) -> int:
    known_ids = {
        company["company_id"] for company in snapshot.canonical["companies"]
    }
    statement = """
        INSERT INTO company_research_records(
            research_record_id, company_id, referenced_company_id, source_kind,
            operation, outcome, reason_code, occurred_at, source_path,
            source_sha256, payload_json, last_import_run_id
        ) VALUES (
            :research_record_id, :company_id, :referenced_company_id, :source_kind,
            :operation, :outcome, :reason_code, :occurred_at, :source_path,
            :source_sha256, :payload_json, :last_import_run_id
        )
        ON CONFLICT(research_record_id) DO NOTHING
    """
    count = 0
    for record in snapshot.research["records"]:
        issue = record["issue"]
        referenced = issue.get("company_id")
        payload = compact_json(record)
        connection.execute(
            statement,
            {
                "research_record_id": f"v3-archive:{record['record_id']}",
                "company_id": referenced if referenced in known_ids else None,
                "referenced_company_id": referenced,
                "source_kind": "v3_archive",
                "operation": record.get("operation") or "unknown",
                "outcome": "issue",
                "reason_code": issue.get("code"),
                "occurred_at": record.get("created_at"),
                "source_path": record.get("handoff") or "archive/v3/company-research.json",
                "source_sha256": sha256_bytes(canonical_json(record).encode("utf-8")),
                "payload_json": payload,
                "last_import_run_id": import_run_id,
            },
        )
        count += 1
    for result in snapshot.applied_results:
        document = result.document
        company_ids = document.get("target", {}).get("company_ids", [])
        referenced = company_ids[0] if isinstance(company_ids, list) and company_ids else None
        connection.execute(
            statement,
            {
                "research_record_id": f"v4-result:{result.digest}",
                "company_id": referenced if referenced in known_ids else None,
                "referenced_company_id": referenced,
                "source_kind": "v4_company_result",
                "operation": document.get("workflow") or "validate_companies",
                "outcome": document.get("outcome"),
                "reason_code": document.get("reason_code"),
                "occurred_at": document.get("created_at"),
                "source_path": result.relative_path,
                "source_sha256": result.digest,
                "payload_json": compact_json(document),
                "last_import_run_id": import_run_id,
            },
        )
        count += 1
    return count


def _count_by(connection: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT {column} AS value, COUNT(*) AS count FROM {table} "
        "WHERE is_current = 1 GROUP BY value ORDER BY value"
    )
    return {str(row["value"]): int(row["count"]) for row in rows}


def reconciliation_report(
    connection: sqlite3.Connection,
    snapshot: V4Snapshot,
) -> dict[str, Any]:
    preview = preview_report(snapshot)
    current_company_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM companies WHERE is_current = 1"
        ).fetchone()[0]
    )
    current_source_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM career_sources WHERE is_current = 1"
        ).fetchone()[0]
    )
    current_location_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM company_locations WHERE is_current = 1"
        ).fetchone()[0]
    )
    eligible_count = int(
        connection.execute("SELECT COUNT(*) FROM eligible_companies").fetchone()[0]
    )
    collectible_count = int(
        connection.execute("SELECT COUNT(*) FROM collectible_sources").fetchone()[0]
    )
    research_count = int(
        connection.execute("SELECT COUNT(*) FROM company_research_records").fetchone()[0]
    )
    v3_research_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM company_research_records WHERE source_kind = 'v3_archive'"
        ).fetchone()[0]
    )
    v4_result_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM company_research_records "
            "WHERE source_kind = 'v4_company_result'"
        ).fetchone()[0]
    )
    token_leaks = int(
        connection.execute(
            "SELECT COUNT(*) FROM company_research_records "
            "WHERE source_kind = 'v4_company_result' "
            "AND instr(payload_json, 'claim_token') > 0"
        ).fetchone()[0]
    )
    stored_instructions = {
        row["source_id"]: row["instructions"]
        for row in connection.execute(
            "SELECT source_id, instructions FROM career_sources WHERE is_current = 1"
        )
    }
    source_instruction_mismatches = sorted(
        source["source_id"]
        for source in _source_records(snapshot)
        if stored_instructions.get(source["source_id"]) != source["instructions"]
    )
    wrong_eligible = [
        row["company_id"]
        for row in connection.execute(
            """
            SELECT company_id FROM eligible_companies
             WHERE sf_validation_status <> 'verified'
             ORDER BY company_id
            """
        )
    ]
    missing_eligible = [
        row["company_id"]
        for row in connection.execute(
            """
            SELECT company_id FROM companies
             WHERE is_current = 1 AND sf_validation_status = 'verified'
               AND company_id NOT IN (SELECT company_id FROM eligible_companies)
             ORDER BY company_id
            """
        )
    ]
    unexpected_job_tables = [
        row["name"]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
             WHERE type = 'table'
               AND name IN ('fetches', 'job_versions', 'job_version_locations')
             ORDER BY name
            """
        )
    ]
    job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    imported_digest = connection.execute(
        "SELECT status FROM import_runs WHERE source_digest = ?",
        (snapshot.source_digest,),
    ).fetchone()
    expected = preview["mapped_counts"]
    errors: list[str] = []
    checks = (
        (current_company_count == preview["v4_counts"]["companies"], "company count mismatch"),
        (current_source_count == preview["v4_counts"]["sources"], "source count mismatch"),
        (
            _count_by(connection, "companies", "sf_validation_status")
            == preview["v4_counts"]["companies_by_status"],
            "company status split mismatch",
        ),
        (
            _count_by(connection, "career_sources", "status")
            == preview["v4_counts"]["sources_by_status"],
            "source status split mismatch",
        ),
        (current_location_count == expected["current_locations"], "location count mismatch"),
        (eligible_count == expected["eligible_companies"], "eligible company count mismatch"),
        (
            collectible_count == expected["collectible_sources"],
            "collectible source count mismatch",
        ),
        (v3_research_count == expected["v3_archive_research"], "V3 research count mismatch"),
        (
            v4_result_count == expected["applied_v4_company_results"],
            "V4 company result count mismatch",
        ),
        (research_count == expected["total_research_records"], "research total mismatch"),
        (not wrong_eligible, "ineligible company appears in eligible view"),
        (not missing_eligible, "verified company missing from eligible view"),
        (not source_instruction_mismatches, "career-source instructions differ from V4"),
        (not unexpected_job_tables, "unexpected job tables are present"),
        (token_leaks == 0, "claim token leaked into research payload"),
        (
            imported_digest is not None and imported_digest["status"] == "completed",
            "current V4 source digest is not recorded as completed",
        ),
    )
    for passed, message in checks:
        if not passed:
            errors.append(message)
    database_integrity = integrity_report(connection)
    if not database_integrity["ok"]:
        errors.append("SQLite integrity check failed")
    return {
        "schema_version": 1,
        "database_schema_version": schema_version(connection),
        "source_digest": snapshot.source_digest,
        "database_counts": {
            "current_companies": current_company_count,
            "current_companies_by_status": _count_by(
                connection, "companies", "sf_validation_status"
            ),
            "current_sources": current_source_count,
            "current_sources_by_status": _count_by(
                connection, "career_sources", "status"
            ),
            "current_locations": current_location_count,
            "eligible_companies": eligible_count,
            "collectible_sources": collectible_count,
            "jobs": job_count,
            "research_records": research_count,
            "v3_archive_research": v3_research_count,
            "v4_company_results": v4_result_count,
        },
        "eligibility": {
            "wrongly_eligible_company_ids": wrong_eligible,
            "verified_but_ineligible_company_ids": missing_eligible,
        },
        "unexpected_job_tables": unexpected_job_tables,
        "source_instruction_mismatches": source_instruction_mismatches,
        "claim_token_payload_matches": token_leaks,
        "integrity": database_integrity,
        "errors": errors,
        "ok": not errors,
    }


def import_snapshot(database: Path, snapshot: V4Snapshot) -> dict[str, Any]:
    connection = connect(database)
    try:
        applied_migrations = apply_migrations(connection)
        existing = connection.execute(
            "SELECT report_json FROM import_runs "
            "WHERE source_digest = ? AND status = 'completed'",
            (snapshot.source_digest,),
        ).fetchone()
        if existing is not None:
            report = json.loads(existing["report_json"])
            verification = reconciliation_report(connection, snapshot)
            if not verification["ok"]:
                raise V4ImportError(
                    "Existing V4 import no longer reconciles: "
                    + "; ".join(verification["errors"])
                )
            report["verification"] = verification
            report["changed"] = False
            report["no_op_reason"] = "source_digest_already_imported"
            report["applied_migrations"] = applied_migrations
            return report

        import_run_id = f"import-v4:{snapshot.source_digest[:24]}"
        timestamp = utc_now_string()
        preview = preview_report(snapshot)
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO import_runs(
                    import_run_id, source_system, source_root, source_revision,
                    source_digest, company_document_sha256,
                    research_document_sha256, started_at, status
                ) VALUES (?, 'v4', ?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    import_run_id,
                    str(snapshot.root),
                    snapshot.canonical["revision"],
                    snapshot.source_digest,
                    snapshot.canonical_sha256,
                    snapshot.research_sha256,
                    timestamp,
                ),
            )
            _upsert_companies(connection, snapshot, import_run_id)
            location_count, evidence_count = _upsert_locations_and_evidence(
                connection, snapshot, import_run_id
            )
            source_count = _upsert_sources(connection, snapshot, import_run_id)
            research_count = _insert_research(connection, snapshot, import_run_id)
            connection.execute(
                "UPDATE import_runs SET completed_at = ?, status = 'completed' "
                "WHERE import_run_id = ?",
                (timestamp, import_run_id),
            )
            verification = reconciliation_report(connection, snapshot)
            if not verification["ok"]:
                raise V4ImportError(
                    "V4 import reconciliation failed: " + "; ".join(verification["errors"])
                )
            report = {
                **preview,
                "dry_run": False,
                "changed": True,
                "import_run_id": import_run_id,
                "completed_at": timestamp,
                "applied_migrations": applied_migrations,
                "verification": verification,
            }
            connection.execute(
                """
                UPDATE import_runs
                   SET completed_at = ?, status = 'completed',
                       company_count = ?, source_count = ?, location_count = ?,
                       evidence_count = ?, research_record_count = ?,
                       report_json = ?
                 WHERE import_run_id = ?
                """,
                (
                    timestamp,
                    len(snapshot.canonical["companies"]),
                    source_count,
                    location_count,
                    evidence_count,
                    research_count,
                    compact_json(report),
                    import_run_id,
                ),
            )
        return report
    finally:
        connection.close()


def verify_database(database: Path, snapshot: V4Snapshot) -> dict[str, Any]:
    connection = connect(database, read_only=True)
    try:
        report = reconciliation_report(connection, snapshot)
        report["database"] = str(database.resolve())
        report["v4_root"] = str(snapshot.root)
        return report
    finally:
        connection.close()


__all__ = [
    "V4ImportError",
    "V4Snapshot",
    "import_snapshot",
    "load_v4_snapshot",
    "preview_report",
    "reconciliation_report",
    "verify_database",
]
