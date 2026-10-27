"""Canonical company/source invariants and allow-listed result application."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Mapping
from urllib.parse import urlparse

from .schemas import validate


COMPANY_CHANGE_FIELDS = frozenset(
    {
        "website_url",
        "address",
        "latitude",
        "longitude",
        "address_status",
        "address_source_url",
        "address_source_label",
        "verified_date",
    }
)
SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "company_id",
        "careers_url",
        "listings_url",
        "provider",
        "board_id",
        "access_mode",
        "scope",
        "instructions",
        "capabilities",
        "status",
        "official_relationship_url",
        "checked_at",
        "last_success_at",
        "added_date",
        "updated_date",
        "expected_record_revision",
    }
)


class CompanyInvariantError(ValueError):
    """Raised for unsafe or inconsistent canonical patches."""


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host or None


def company_index(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {company["company_id"]: company for company in document["companies"]}


def source_index(document: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    result: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for company in document["companies"]:
        for source in company["career_sources"]:
            result[source["source_id"]] = (company, source)
    return result


def integrity_report(document: Mapping[str, Any]) -> dict[str, Any]:
    company_ids = [company["company_id"] for company in document["companies"]]
    source_ids: list[str] = []
    orphans: list[str] = []
    domains: dict[str, list[str]] = {}
    for company in document["companies"]:
        domain = normalize_domain(company.get("website_url"))
        if domain:
            domains.setdefault(domain, []).append(company["company_id"])
        for source in company["career_sources"]:
            source_ids.append(source["source_id"])
            if source["company_id"] != company["company_id"]:
                orphans.append(source["source_id"])
    return {
        "duplicate_company_ids": sorted(
            item for item, count in Counter(company_ids).items() if count > 1
        ),
        "duplicate_source_ids": sorted(
            item for item, count in Counter(source_ids).items() if count > 1
        ),
        "orphan_source_ids": sorted(orphans),
        "duplicate_website_domains": {
            domain: ids for domain, ids in sorted(domains.items()) if len(ids) > 1
        },
    }


def validate_integrity(document: Mapping[str, Any]) -> None:
    validate(document, "companies.schema.json", "Canonical company database")
    report = integrity_report(document)
    if report["duplicate_company_ids"]:
        raise CompanyInvariantError(
            f"Duplicate company IDs: {report['duplicate_company_ids'][:10]}"
        )
    if report["duplicate_source_ids"]:
        raise CompanyInvariantError(
            f"Duplicate source IDs: {report['duplicate_source_ids'][:10]}"
        )
    if report["orphan_source_ids"]:
        raise CompanyInvariantError(
            f"Sources owned by the wrong embedded company: {report['orphan_source_ids'][:10]}"
        )
    for company in document["companies"]:
        _validate_address_state(company)


def _validate_address_state(company: Mapping[str, Any]) -> None:
    status = company["address_status"]
    if status == "pending" and company.get("verified_date") is not None:
        raise CompanyInvariantError("pending company cannot have verified_date")
    if status == "not_found":
        fields = (
            "address",
            "latitude",
            "longitude",
            "address_source_url",
            "address_source_label",
        )
        if any(company.get(field) is not None for field in fields):
            raise CompanyInvariantError("not_found company must clear address evidence fields")
        if company.get("verified_date") is None:
            raise CompanyInvariantError("not_found company requires a completed validation date")
    if status in {"verified", "outside_sf"}:
        required = (
            "address",
            "latitude",
            "longitude",
            "address_source_url",
            "address_source_label",
            "verified_date",
        )
        if any(company.get(field) is None for field in required):
            raise CompanyInvariantError(f"{status} company lacks complete address evidence")


def apply_company_patch(
    document: dict[str, Any],
    patch: Mapping[str, Any],
    *,
    applied_at: str,
) -> tuple[bool, list[str]]:
    """Apply an exact target-scoped patch to a locked canonical document."""

    company_id = patch.get("company_id")
    companies = company_index(document)
    company = companies.get(company_id)
    if company is None:
        raise CompanyInvariantError(f"Unknown company ID {company_id!r}")
    expected = patch.get("expected_record_revision")
    if company["record_revision"] != expected:
        raise CompanyInvariantError(
            f"Company {company_id} revision conflict: expected {expected}, "
            f"found {company['record_revision']}"
        )

    changes = patch.get("company_changes")
    if not isinstance(changes, Mapping):
        raise CompanyInvariantError("company_changes must be an object")
    unknown = set(changes) - COMPANY_CHANGE_FIELDS
    if unknown:
        raise CompanyInvariantError(f"Company patch contains forbidden fields: {sorted(unknown)}")

    working = copy.deepcopy(company)
    descriptions: list[str] = []
    for field, value in changes.items():
        if working.get(field) != value:
            working[field] = copy.deepcopy(value)
            descriptions.append(f"company {company_id}: {field} changed")

    source_upserts = patch.get("source_upserts")
    if not isinstance(source_upserts, list):
        raise CompanyInvariantError("source_upserts must be an array")
    sources_by_id = {source["source_id"]: source for source in working["career_sources"]}
    seen_upserts: set[str] = set()
    for incoming_raw in source_upserts:
        if not isinstance(incoming_raw, Mapping):
            raise CompanyInvariantError("Each source upsert must be an object")
        incoming = copy.deepcopy(dict(incoming_raw))
        unknown_source = set(incoming) - SOURCE_FIELDS
        if unknown_source:
            raise CompanyInvariantError(
                f"Source patch contains forbidden fields: {sorted(unknown_source)}"
            )
        source_id = incoming.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise CompanyInvariantError("Source upsert requires source_id")
        if source_id in seen_upserts:
            raise CompanyInvariantError(f"Duplicate source upsert {source_id}")
        seen_upserts.add(source_id)
        if incoming.get("company_id") != company_id:
            raise CompanyInvariantError(f"Source {source_id} cannot be assigned to another company")
        existing = sources_by_id.get(source_id)
        expected_source_revision = incoming.pop("expected_record_revision", None)
        if existing is None:
            if expected_source_revision is not None:
                raise CompanyInvariantError(
                    f"New source {source_id} must have null expected_record_revision"
                )
            incoming["record_revision"] = 1
            incoming.setdefault("added_date", applied_at)
            incoming["updated_date"] = applied_at
            working["career_sources"].append(incoming)
            sources_by_id[source_id] = incoming
            descriptions.append(f"company {company_id}: source {source_id} added")
            continue
        if existing["company_id"] != company_id:
            raise CompanyInvariantError(f"Source {source_id} already belongs to another company")
        if expected_source_revision != existing["record_revision"]:
            raise CompanyInvariantError(
                f"Source {source_id} revision conflict: expected {expected_source_revision}, "
                f"found {existing['record_revision']}"
            )
        replacement = copy.deepcopy(incoming)
        replacement["record_revision"] = existing["record_revision"]
        replacement["added_date"] = existing["added_date"]
        replacement["updated_date"] = applied_at
        comparable = copy.deepcopy(replacement)
        comparable["updated_date"] = existing["updated_date"]
        if comparable != existing:
            replacement["record_revision"] += 1
            index = working["career_sources"].index(existing)
            working["career_sources"][index] = replacement
            sources_by_id[source_id] = replacement
            descriptions.append(f"company {company_id}: source {source_id} updated")

    _validate_address_state(working)
    if working == company:
        return False, []
    working["record_revision"] = company["record_revision"] + 1
    working["updated_date"] = applied_at
    index = document["companies"].index(company)
    document["companies"][index] = working
    validate_integrity(document)
    return True, descriptions
