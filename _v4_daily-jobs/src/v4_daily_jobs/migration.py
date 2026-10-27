"""Deterministic, read-only migration from the V3 repository."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .json_store import JsonStore, canonical_json_bytes
from .schemas import validate, validator


DEFAULT_V3_ROOT = Path("/Users/natelipp/ai-code/_v3_daily-jobs")
HANDOFFS = (
    "20260810T035302000Z-company-ingest-builtin-p005-p005.json",
    "20260810T035319000Z-company-ingest-builtin-p005-p005.json",
    "20260810T035353000Z-company-ingest-builtin-p006-p006.json",
    "20260815T202742000Z-company-ingest.json",
    "20260828T214645Z-company-validate.json",
)
EXPECTED_COMPANY_COUNTS = {
    "total": 3036,
    "verified": 930,
    "outside_sf": 426,
    "not_found": 1553,
    "pending": 127,
}


class MigrationError(RuntimeError):
    """Raised when source inputs do not reproduce the approved checkpoint."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"Expected an object in {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_document(path: Path, document: dict[str, Any], schema_name: str | None = None) -> bool:
    callback = validator(schema_name, str(path)) if schema_name else None
    store = JsonStore(path, validator=callback)
    if not path.exists():
        return store.initialize(document).changed
    with store.locked(exclusive=True) as locked:
        current = locked.read()
        if current == document:
            return False
        return locked.save(document).changed


def _write_exact_bytes(path: Path, data: bytes) -> bool:
    """Atomically preserve a non-canonical input file byte-for-byte."""

    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def _apply_v3_handoffs(v3_root: Path, temporary_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog_path = temporary_root / "companies.json"
    catalog_path.write_bytes((v3_root / "data" / "companies.json").read_bytes())
    career_path = temporary_root / "career_sources.json"
    career_path.write_bytes((v3_root / "data" / "career_sources.json").read_bytes())
    apply_script = v3_root / "scripts" / "apply_handoff.py"
    applications: list[dict[str, Any]] = []
    for name in HANDOFFS:
        handoff_path = v3_root / "imports" / name
        if not handoff_path.is_file():
            raise MigrationError(f"Required migration handoff is missing: {handoff_path}")
        process = subprocess.run(
            [
                sys.executable,
                str(apply_script),
                str(handoff_path),
                "--catalog",
                str(catalog_path),
                "--career-sources",
                str(career_path),
            ],
            cwd=v3_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise MigrationError(
                f"V3 applier rejected {name} with exit {process.returncode}:\n"
                f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}"
            )
        output = json.loads(process.stdout)
        if output.get("conflicts"):
            raise MigrationError(f"Unexpected conflicts while applying {name}: {output['conflicts']}")
        applications.append(
            {
                "file": f"imports/{name}",
                "sha256": _digest(handoff_path),
                "operation": output["operation"],
                "applied": output["applied"],
                "skipped": output["skipped"],
            }
        )
    return _read_json(catalog_path), applications


def _company_counts(catalog: dict[str, Any]) -> dict[str, int]:
    statuses = Counter(company["address_status"] for company in catalog["companies"])
    return {
        "total": len(catalog["companies"]),
        "verified": statuses["verified"],
        "outside_sf": statuses["outside_sf"],
        "not_found": statuses["not_found"],
        "pending": statuses["pending"],
    }


def _collect_research(v3_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    handoff_count = 0
    for path in sorted((v3_root / "imports").glob("*.json")):
        try:
            handoff = _read_json(path)
        except MigrationError:
            continue
        operation = handoff.get("operation")
        if operation not in {"company-ingest", "company-validate", "career-source-discover"}:
            continue
        handoff_count += 1
        for index, issue in enumerate(handoff.get("issues", [])):
            records.append(
                {
                    "record_id": f"{path.stem}:issue:{index + 1}",
                    "handoff": f"imports/{path.name}",
                    "handoff_sha256": _digest(path),
                    "issue_index": index,
                    "operation": operation,
                    "created_at": handoff.get("created_at"),
                    "issue": copy.deepcopy(issue),
                }
            )
    return {
        "schema_version": 1,
        "source": str(v3_root),
        "handoff_count": handoff_count,
        "record_count": len(records),
        "company_count": len(
            {
                record["issue"].get("company_id")
                for record in records
                if record["issue"].get("company_id") is not None
            }
        ),
        "records": records,
    }


def _integrity(companies: list[dict[str, Any]]) -> dict[str, Any]:
    company_ids = [company["company_id"] for company in companies]
    source_ids: list[str] = []
    orphan_sources: list[str] = []
    domain_groups: dict[str, list[str]] = defaultdict(list)
    for company in companies:
        website = company.get("website_url")
        if website:
            from urllib.parse import urlparse

            host = (urlparse(website).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                domain_groups[host].append(company["company_id"])
        for source in company["career_sources"]:
            source_ids.append(source["source_id"])
            if source["company_id"] != company["company_id"]:
                orphan_sources.append(source["source_id"])
    return {
        "duplicate_company_ids": sorted(
            key for key, count in Counter(company_ids).items() if count > 1
        ),
        "duplicate_source_ids": sorted(
            key for key, count in Counter(source_ids).items() if count > 1
        ),
        "orphan_source_ids": sorted(orphan_sources),
        "duplicate_website_domains": {
            domain: ids for domain, ids in sorted(domain_groups.items()) if len(ids) > 1
        },
    }


def build_migration(v3_root: Path = DEFAULT_V3_ROOT) -> dict[str, dict[str, Any]]:
    """Build V4 documents entirely in memory (apart from temporary V3 copies)."""

    v3_root = v3_root.resolve()
    required = (
        v3_root / "data" / "companies.json",
        v3_root / "data" / "career_sources.json",
        v3_root / "config" / "search-profiles" / "ai-product-frontend-v1.json",
        v3_root / "scripts" / "apply_handoff.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise MigrationError(f"Missing V3 migration inputs: {missing}")

    with tempfile.TemporaryDirectory(prefix="v4-daily-jobs-migration-") as temp_name:
        catalog, applications = _apply_v3_handoffs(v3_root, Path(temp_name))
    counts = _company_counts(catalog)
    if counts != EXPECTED_COMPANY_COUNTS:
        raise MigrationError(
            f"Migrated company checkpoint does not match approved counts: "
            f"expected {EXPECTED_COMPANY_COUNTS}, got {counts}"
        )

    sources_doc = _read_json(v3_root / "data" / "career_sources.json")
    sources_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for original in sources_doc["sources"]:
        source = copy.deepcopy(original)
        source["record_revision"] = 1
        sources_by_company[source["company_id"]].append(source)

    companies: list[dict[str, Any]] = []
    for original in catalog["companies"]:
        company = copy.deepcopy(original)
        company["record_revision"] = 1
        company["career_sources"] = sources_by_company.pop(company["company_id"], [])
        companies.append(company)
    if sources_by_company:
        raise MigrationError(
            f"Career sources reference unknown companies: {sorted(sources_by_company)[:20]}"
        )

    integrity = _integrity(companies)
    if integrity["duplicate_company_ids"] or integrity["duplicate_source_ids"] or integrity["orphan_source_ids"]:
        raise MigrationError(f"Migrated canonical integrity failure: {integrity}")

    research = _collect_research(v3_root)
    profile_path = v3_root / "config" / "search-profiles" / "ai-product-frontend-v1.json"
    profile = _read_json(profile_path)
    validate(profile, "search-profile.schema.json", "Migrated search profile")

    source_statuses = Counter(source["status"] for source in sources_doc["sources"])
    provider_ready = Counter(
        source["provider"] for source in sources_doc["sources"] if source["status"] == "ready"
    )
    input_digests = {
        "data/companies.json": _digest(v3_root / "data" / "companies.json"),
        "data/career_sources.json": _digest(v3_root / "data" / "career_sources.json"),
        "config/search-profiles/ai-product-frontend-v1.json": _digest(profile_path),
        "scripts/apply_handoff.py": _digest(v3_root / "scripts" / "apply_handoff.py"),
    }
    canonical = {
        "schema_version": 1,
        "revision": 1,
        "updated_at": catalog["updated_at"],
        "companies": companies,
        "application_ledger": [],
        "migration": {
            "version": "v3_to_v4_v1",
            "source_root": str(v3_root),
            "source_updated_at": catalog["updated_at"],
            "input_digests": input_digests,
            "handoff_applications": applications,
        },
    }
    validate(canonical, "companies.schema.json", "Migrated company database")
    canonical_company_ids = {company["company_id"] for company in companies}
    research_company_ids = {
        record["issue"].get("company_id")
        for record in research["records"]
        if record["issue"].get("company_id") is not None
    }
    unknown_research_company_ids = sorted(
        str(company_id)
        for company_id in research_company_ids
        if company_id not in canonical_company_ids
    )
    baseline_updated_at = _read_json(v3_root / "data" / "companies.json")["updated_at"]
    historical_website_timestamp_ids = [
        "buildops",
        "new-relic",
        "nexhealth",
        "substack",
    ]
    warnings = [
        (
            "The V3 baseline company root updated_at "
            f"({baseline_updated_at}) is newer than the last applied handoff timestamp "
            f"({catalog['updated_at']}); the migrated canonical root preserves the exact "
            "V3 handoff-applier output."
        ),
        (
            "Four pre-existing companies received website URLs from historical handoffs "
            "whose source timestamps can predate their added_date; preserved without silent "
            f"timestamp repair: {', '.join(historical_website_timestamp_ids)}."
        ),
        (
            f"Preserved {len(integrity['duplicate_website_domains'])} duplicate website-domain "
            "groups for explicit company-validation audit."
        ),
        (
            f"The research archive preserves {research['record_count']} issue/evidence rows "
            f"across {research['company_count']} non-null company identifiers; repeated "
            "identifiers remain separate provenance records."
        ),
    ]
    if unknown_research_company_ids:
        warnings.append(
            "Research records reference identifiers absent from the canonical company set; "
            "preserved verbatim for provenance: " + ", ".join(unknown_research_company_ids)
        )
    output_digests = {
        "data/companies.json": hashlib.sha256(canonical_json_bytes(canonical)).hexdigest(),
        "data/profiles/ai-product-frontend-v1.json": _digest(profile_path),
        "archive/v3/company-research.json": hashlib.sha256(
            canonical_json_bytes(research)
        ).hexdigest(),
    }
    manifest = {
        "schema_version": 1,
        "migration_version": "v3_to_v4_v1",
        "source_root": str(v3_root),
        "source_updated_at": catalog["updated_at"],
        "baseline_company_updated_at": baseline_updated_at,
        "inputs": input_digests,
        "handoffs": applications,
        "company_counts": counts,
        "source_count": len(sources_doc["sources"]),
        "source_status_counts": dict(sorted(source_statuses.items())),
        "ready_provider_counts": dict(sorted(provider_ready.items())),
        "research_record_count": research["record_count"],
        "research_company_count": research["company_count"],
        "integrity": integrity,
        "research_unknown_company_ids": unknown_research_company_ids,
        "output_digests": output_digests,
        "warnings": warnings,
        "excluded": [
            "V3 job result state",
            "V3 job-scan handoffs",
            "V3 generated job reports",
        ],
    }
    return {
        "companies": canonical,
        "profile": profile,
        "research": research,
        "manifest": manifest,
    }


def migrate(v4_root: Path, v3_root: Path = DEFAULT_V3_ROOT, *, force: bool = False) -> dict[str, Any]:
    """Write the approved migration to V4 and return a deterministic summary."""

    documents = build_migration(v3_root)
    v4_root = v4_root.resolve()
    companies_path = v4_root / "data" / "companies.json"
    managed_paths = [
        companies_path,
        v4_root / "data" / "profiles" / "ai-product-frontend-v1.json",
        v4_root / "archive" / "v3" / "company-research.json",
        v4_root / "archive" / "v3" / "migration-manifest.json",
    ]
    if companies_path.exists() and not force:
        existing = _read_json(companies_path)
        if existing != documents["companies"]:
            raise MigrationError(
                f"Refusing to overwrite existing V4 canonical data without --force: {companies_path}"
            )

    changed: list[str] = []
    writes = (
        (managed_paths[0], documents["companies"], "companies.schema.json"),
        (managed_paths[2], documents["research"], None),
        (managed_paths[3], documents["manifest"], None),
    )
    for path, document, schema_name in writes:
        if _write_document(path, document, schema_name):
            changed.append(str(path))
    profile_bytes = (
        v3_root / "config" / "search-profiles" / "ai-product-frontend-v1.json"
    ).read_bytes()
    if _write_exact_bytes(managed_paths[1], profile_bytes):
        changed.append(str(managed_paths[1]))

    empty_queue = {"schema_version": 1, "revision": 0, "updated_at": None, "tasks": []}
    for name, schema_name in (
        ("company-queue.json", "company-queue.schema.json"),
        ("job-queue.json", "job-queue.schema.json"),
    ):
        path = v4_root / "data" / "queues" / name
        store = JsonStore(path, validator=validator(schema_name, str(path)))
        if not path.exists() and store.initialize(copy.deepcopy(empty_queue)).changed:
            changed.append(str(path))

    no_ai_source = v3_root / "config" / "search-profiles" / "product-frontend-no-ai-test-v1.json"
    if no_ai_source.is_file():
        no_ai = _read_json(no_ai_source)
        no_ai_path = v4_root / "archive" / "v3" / no_ai_source.name
        if _write_document(no_ai_path, no_ai):
            changed.append(str(no_ai_path))

    for directory in (
        v4_root / "data" / "jobs" / "sources",
        v4_root / "data" / "results" / "companies",
        v4_root / "data" / "results" / "jobs",
        v4_root / "data" / "audit",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    manifest = documents["manifest"]
    return {
        "schema_version": 1,
        "execution_status": "completed",
        "operation": "migrate-v3",
        "changed_paths": changed,
        "company_counts": manifest["company_counts"],
        "source_count": manifest["source_count"],
        "source_status_counts": manifest["source_status_counts"],
        "research_record_count": manifest["research_record_count"],
        "research_company_count": manifest["research_company_count"],
        "warnings": [
            *manifest["warnings"],
        ],
    }
