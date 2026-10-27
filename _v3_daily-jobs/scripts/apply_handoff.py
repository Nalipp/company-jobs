#!/usr/bin/env python3
"""Validate and idempotently apply a V3 state handoff."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "data" / "companies.json"
DEFAULT_CAREER_SOURCES = ROOT / "data" / "career_sources.json"
DEFAULT_JOB_RESULTS_DIR = ROOT / "data" / "job-results"
SEARCH_PROFILE_DIR = ROOT / "config" / "search-profiles"
SCHEMA_DIR = ROOT / "schemas"
ADDRESS_FIELDS = (
    "address",
    "latitude",
    "longitude",
    "address_status",
    "address_source_url",
    "address_source_label",
    "verified_date",
)
LEGAL_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "llc", "ltd", "limited"}
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gh_src",
    "lever-origin",
    "lever-source",
    "source",
}
JOB_FACT_FIELDS = (
    "company_id",
    "source_id",
    "source_job_id",
    "canonical_url",
    "title",
    "location_text",
    "location",
    "workplace_arrangement",
    "workplace_text",
    "employment_type",
    "employment_text",
    "seniority",
    "compensation",
    "posted_date",
    "evidence",
)


class ApplyError(Exception):
    """A user-facing validation or application error."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ApplyError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ApplyError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApplyError(f"Expected a JSON object in {path}")
    return value


def schema_registry() -> tuple[Registry, dict[str, dict[str, Any]]]:
    schemas: dict[str, dict[str, Any]] = {}
    resources: list[tuple[str, Resource[Any]]] = []
    for name in (
        "companies.schema.json",
        "career-sources.schema.json",
        "job-results.schema.json",
        "search-profile.schema.json",
        "handoff.schema.json",
    ):
        schema = read_json(SCHEMA_DIR / name)
        schemas[name] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), schemas


def validate_json(instance: Any, schema: dict[str, Any], registry: Registry, label: str) -> None:
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    messages = []
    for error in errors[:12]:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        messages.append(f"{location}: {error.message}")
    if len(errors) > 12:
        messages.append(f"... and {len(errors) - 12} more errors")
    raise ApplyError(f"{label} failed schema validation:\n- " + "\n- ".join(messages))


def normalize_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_value.lower().replace("&", " and "))
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def conflict(
    conflicts: list[dict[str, Any]],
    company_id: str | None,
    field: str,
    catalog_value: Any,
    incoming_value: Any,
    details: str,
) -> None:
    conflicts.append(
        {
            "company_id": company_id,
            "field": field,
            "catalog_value": catalog_value,
            "incoming_value": incoming_value,
            "details": details,
        }
    )


def find_existing(
    companies: list[dict[str, Any]], incoming: dict[str, Any], conflicts: list[dict[str, Any]]
) -> int | None:
    incoming_id = incoming["company_id"]
    exact = [index for index, company in enumerate(companies) if company["company_id"] == incoming_id]
    if exact:
        index = exact[0]
        if normalize_name(companies[index]["company_name"]) != normalize_name(incoming["company_name"]):
            conflict(
                conflicts,
                incoming_id,
                "company_id",
                companies[index]["company_name"],
                incoming["company_name"],
                "The incoming ID belongs to a differently named catalog company.",
            )
            return -1
        return index

    domain = normalize_domain(incoming.get("website_url"))
    if domain:
        matches = [
            index
            for index, company in enumerate(companies)
            if normalize_domain(company.get("website_url")) == domain
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            conflict(
                conflicts,
                incoming_id,
                "website_url",
                [companies[index]["company_id"] for index in matches],
                incoming["website_url"],
                "The website domain matches more than one catalog company.",
            )
            return -1

    name = normalize_name(incoming["company_name"])
    matches = [
        index for index, company in enumerate(companies) if normalize_name(company["company_name"]) == name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        conflict(
            conflicts,
            incoming_id,
            "company_name",
            [companies[index]["company_id"] for index in matches],
            incoming["company_name"],
            "The normalized name matches more than one catalog company.",
        )
        return -1
    return None


def merge_scalar(
    target: dict[str, Any],
    incoming: dict[str, Any],
    field: str,
    conflicts: list[dict[str, Any]],
    company_id: str,
) -> bool:
    incoming_value = incoming.get(field)
    if incoming_value is None:
        return False
    current_value = target.get(field)
    if current_value is None:
        target[field] = copy.deepcopy(incoming_value)
        return True
    if field == "website_url" and normalize_domain(current_value) == normalize_domain(incoming_value):
        return False
    if current_value != incoming_value:
        conflict(
            conflicts,
            company_id,
            field,
            current_value,
            incoming_value,
            "Catalog value preserved; incoming non-null value differs.",
        )
    return False


def merge_address(
    target: dict[str, Any],
    incoming: dict[str, Any],
    conflicts: list[dict[str, Any]],
    company_id: str,
) -> bool:
    incoming_address = incoming.get("address")
    if incoming_address is None:
        return False
    current_address = target.get("address")
    if current_address is None:
        target["address"] = copy.deepcopy(incoming_address)
        return True
    changed = False
    for key, incoming_value in incoming_address.items():
        if incoming_value is None:
            continue
        current_value = current_address.get(key)
        if current_value is None:
            current_address[key] = incoming_value
            changed = True
        elif current_value != incoming_value:
            conflict(
                conflicts,
                company_id,
                f"address.{key}",
                current_value,
                incoming_value,
                "Catalog value preserved; incoming address component differs.",
            )
    return changed


def apply_ingest(
    catalog: dict[str, Any], handoff: dict[str, Any]
) -> tuple[int, int, list[dict[str, Any]]]:
    companies = catalog["companies"]
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []

    for incoming in handoff["changes"]:
        index = find_existing(companies, incoming, conflicts)
        if index == -1:
            skipped += 1
            continue
        if index is None:
            companies.append(copy.deepcopy(incoming))
            applied += 1
            continue

        existing = companies[index]
        changed = merge_scalar(
            existing, incoming, "website_url", conflicts, existing["company_id"]
        )

        if existing["address_status"] not in {"verified", "outside_sf"}:
            address_evidence = any(
                incoming.get(field) is not None
                for field in (
                    "address",
                    "latitude",
                    "longitude",
                    "address_source_url",
                    "address_source_label",
                )
            )
            changed = merge_address(existing, incoming, conflicts, existing["company_id"]) or changed
            for field in (
                "latitude",
                "longitude",
                "address_source_url",
                "address_source_label",
            ):
                changed = (
                    merge_scalar(existing, incoming, field, conflicts, existing["company_id"]) or changed
                )
            if existing["address_status"] == "not_found" and address_evidence:
                existing["address_status"] = "pending"
                existing["verified_date"] = None
                changed = True

        if changed:
            existing["updated_date"] = handoff["created_at"]
            applied += 1
        else:
            skipped += 1

    return applied, skipped, conflicts


def address_result_matches(company: dict[str, Any], result: dict[str, Any], created_at: str) -> bool:
    return all(company.get(field) == result.get(field) for field in ADDRESS_FIELDS) and company.get(
        "updated_date"
    ) == created_at


def apply_validation(
    catalog: dict[str, Any], handoff: dict[str, Any]
) -> tuple[int, int, list[dict[str, Any]]]:
    companies = catalog["companies"]
    by_id = {company["company_id"]: company for company in companies}
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []

    for change in handoff["changes"]:
        company_id = change["company_id"]
        company = by_id.get(company_id)
        if company is None:
            conflict(
                conflicts,
                company_id,
                "company_id",
                None,
                company_id,
                "No catalog company has this ID.",
            )
            skipped += 1
            continue

        if "address_result" not in change:
            if company["address_status"] != change["expected_status"]:
                conflict(
                    conflicts,
                    company_id,
                    "address_status",
                    company["address_status"],
                    change["expected_status"],
                    "Catalog status changed after this handoff was prepared.",
                )
                skipped += 1
                continue
            changed = merge_scalar(company, change, "website_url", conflicts, company_id)
            if changed:
                company["updated_date"] = handoff["created_at"]
                applied += 1
            else:
                skipped += 1
            continue

        result = change["address_result"]
        if address_result_matches(company, result, handoff["created_at"]):
            changed = merge_scalar(company, change, "website_url", conflicts, company_id)
            if changed:
                company["updated_date"] = handoff["created_at"]
                applied += 1
            else:
                skipped += 1
            continue

        if company["address_status"] != change["expected_status"]:
            conflict(
                conflicts,
                company_id,
                "address_status",
                company["address_status"],
                change["expected_status"],
                "Catalog status changed after this handoff was prepared.",
            )
            skipped += 1
            continue

        merge_scalar(company, change, "website_url", conflicts, company_id)
        for field in ADDRESS_FIELDS:
            company[field] = copy.deepcopy(result[field])
        company["updated_date"] = handoff["created_at"]
        applied += 1

    return applied, skipped, conflicts


def source_identity_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether two source records identify the same underlying board."""
    if left["provider"] != right["provider"]:
        return False
    if left["board_id"] is not None and right["board_id"] is not None:
        return left["board_id"] == right["board_id"]
    left_url = left["listings_url"]
    right_url = right["listings_url"]
    return left_url is not None and left_url == right_url


def apply_career_source_discovery(
    catalog: dict[str, Any], career_sources: dict[str, Any], handoff: dict[str, Any]
) -> tuple[int, int, list[dict[str, Any]]]:
    companies = {company["company_id"]: company for company in catalog["companies"]}
    sources = career_sources["sources"]
    by_id = {source["source_id"]: source for source in sources}
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []

    for change in handoff["changes"]:
        incoming = change["source"]
        source_id = incoming["source_id"]
        company_id = incoming["company_id"]
        company = companies.get(company_id)
        if company is None:
            conflict(
                conflicts,
                company_id,
                "company_id",
                None,
                company_id,
                "No catalog company has this ID.",
            )
            skipped += 1
            continue
        if company["address_status"] != "verified":
            conflict(
                conflicts,
                company_id,
                "address_status",
                company["address_status"],
                "verified",
                "Career sources may be recorded only for currently verified companies.",
            )
            skipped += 1
            continue
        if incoming["updated_date"] != handoff["created_at"]:
            conflict(
                conflicts,
                company_id,
                "source.updated_date",
                incoming["updated_date"],
                handoff["created_at"],
                "Source updated_date must equal the handoff created_at timestamp.",
            )
            skipped += 1
            continue

        existing = by_id.get(source_id)
        expected_updated_date = change["expected_updated_date"]
        if existing is None:
            if expected_updated_date is not None:
                conflict(
                    conflicts,
                    company_id,
                    "expected_updated_date",
                    None,
                    expected_updated_date,
                    "The source does not exist, so expected_updated_date must be null.",
                )
                skipped += 1
                continue
            if incoming["added_date"] != handoff["created_at"]:
                conflict(
                    conflicts,
                    company_id,
                    "source.added_date",
                    incoming["added_date"],
                    handoff["created_at"],
                    "A new source added_date must equal the handoff created_at timestamp.",
                )
                skipped += 1
                continue
            duplicate = next(
                (
                    source
                    for source in sources
                    if source_identity_matches(source, incoming)
                ),
                None,
            )
            if duplicate is not None:
                conflict(
                    conflicts,
                    company_id,
                    "source_identity",
                    duplicate["source_id"],
                    source_id,
                    "The provider and board identity already belongs to another source record.",
                )
                skipped += 1
                continue
            copied = copy.deepcopy(incoming)
            sources.append(copied)
            by_id[source_id] = copied
            applied += 1
            continue

        if incoming == existing:
            skipped += 1
            continue
        if expected_updated_date != existing["updated_date"]:
            conflict(
                conflicts,
                company_id,
                "source.updated_date",
                existing["updated_date"],
                expected_updated_date,
                "Source changed after this handoff was prepared.",
            )
            skipped += 1
            continue
        if incoming["company_id"] != existing["company_id"]:
            conflict(
                conflicts,
                company_id,
                "source.company_id",
                existing["company_id"],
                incoming["company_id"],
                "A source cannot be reassigned to a different company.",
            )
            skipped += 1
            continue
        if incoming["added_date"] != existing["added_date"]:
            conflict(
                conflicts,
                company_id,
                "source.added_date",
                existing["added_date"],
                incoming["added_date"],
                "A source added_date is immutable.",
            )
            skipped += 1
            continue
        if existing["status"] == "ready" and incoming["status"] in {
            "blocked",
            "no_careers_found",
            "unsupported",
        }:
            conflict(
                conflicts,
                company_id,
                "source.status",
                existing["status"],
                incoming["status"],
                "A failed refresh must preserve a previously ready source; report it as an issue instead.",
            )
            skipped += 1
            continue
        duplicate = next(
            (
                source
                for source in sources
                if source["source_id"] != source_id and source_identity_matches(source, incoming)
            ),
            None,
        )
        if duplicate is not None:
            conflict(
                conflicts,
                company_id,
                "source_identity",
                duplicate["source_id"],
                source_id,
                "The provider and board identity already belongs to another source record.",
            )
            skipped += 1
            continue

        index = sources.index(existing)
        copied = copy.deepcopy(incoming)
        sources[index] = copied
        by_id[source_id] = copied
        applied += 1

    return applied, skipped, conflicts


def normalize_job_url(value: str) -> str:
    parsed = urlparse(value)
    filtered_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            urlencode(filtered_query, doseq=True),
            parsed.fragment,
        )
    )


def job_identities_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_id = left.get("source_job_id")
    right_id = right.get("source_job_id")
    if left_id is not None and right_id is not None:
        return left_id == right_id
    return normalize_job_url(left["canonical_url"]) == normalize_job_url(right["canonical_url"])


def job_facts(job: dict[str, Any]) -> dict[str, Any]:
    return {field: copy.deepcopy(job[field]) for field in JOB_FACT_FIELDS}


def profile_mismatch(observation: dict[str, Any], profile: dict[str, Any]) -> str | None:
    constraints = profile["hard_constraints"]
    expected_location = constraints["location"]
    location = observation["location"]
    for field in ("city", "region", "country"):
        if location[field].casefold() != expected_location[field].casefold():
            return f"normalized location {field} does not match profile"

    workplace = observation["workplace_arrangement"]
    workplace_policy = constraints["workplace"]
    if workplace == "unknown":
        if workplace_policy["unknown_policy"] != "include":
            return "unknown workplace arrangement is excluded by profile"
    elif workplace not in workplace_policy["allowed_arrangements"]:
        return f"workplace arrangement {workplace!r} is excluded by profile"

    employment = observation["employment_type"]
    employment_policy = constraints["employment"]
    if employment is None:
        if employment_policy["missing_type_policy"] == "exclude":
            return "missing employment type is excluded by profile"
    else:
        normalized_employment = employment.casefold()
        disallowed = {
            value.casefold() for value in employment_policy["explicitly_disallowed_types"]
        }
        allowed = {value.casefold() for value in employment_policy["allowed_types"]}
        if normalized_employment in disallowed:
            return f"employment type {employment!r} is explicitly excluded"
        if normalized_employment not in allowed:
            return f"employment type {employment!r} is not allowed by profile"

    compensation = observation["compensation"]
    compensation_policy = constraints["compensation"]
    for field in ("currency", "basis", "period"):
        if compensation[field] != compensation_policy[field]:
            return f"compensation {field} does not match profile"
    threshold = compensation_policy["threshold"]
    comparison = compensation_policy["comparison"]
    if comparison == "published_maximum_at_least":
        compensation_matches = compensation["maximum"] >= threshold
    elif comparison == "published_minimum_at_least":
        compensation_matches = (
            compensation["minimum"] is not None and compensation["minimum"] >= threshold
        )
    else:
        compensation_matches = (
            compensation["minimum"] is not None
            and compensation["minimum"] <= threshold <= compensation["maximum"]
        )
    if not compensation_matches:
        return "published compensation does not satisfy profile threshold"

    excluded_titles = {
        re.sub(r"\s+", " ", value.strip()).casefold()
        for value in constraints["role"]["excluded_titles"]
    }
    normalized_title = re.sub(r"\s+", " ", observation["title"].strip()).casefold()
    if normalized_title in excluded_titles:
        return f"title {observation['title']!r} is explicitly excluded"
    return None


def find_job_index(jobs: list[dict[str, Any]], observation: dict[str, Any]) -> int | None:
    for index, job in enumerate(jobs):
        if job["source_id"] == observation["source_id"] and job_identities_match(
            job, observation
        ):
            return index
    return None


def validate_job_results_consistency(
    job_results: dict[str, Any], career_sources: dict[str, Any]
) -> None:
    source_by_id = {source["source_id"]: source for source in career_sources["sources"]}
    seen_source_states: set[str] = set()
    for state in job_results["source_states"]:
        source_id = state["source_id"]
        if source_id in seen_source_states:
            raise ApplyError(f"Job results contain duplicate source state for {source_id}")
        if source_id not in source_by_id:
            raise ApplyError(f"Job results reference unknown source state {source_id}")
        seen_source_states.add(source_id)

    jobs = job_results["jobs"]
    for index, job in enumerate(jobs):
        source = source_by_id.get(job["source_id"])
        if source is None:
            raise ApplyError(f"Job results reference unknown source {job['source_id']}")
        if source["company_id"] != job["company_id"]:
            raise ApplyError(
                f"Job {job['canonical_url']} references a source owned by another company"
            )
        for prior in jobs[:index]:
            if prior["source_id"] == job["source_id"] and job_identities_match(prior, job):
                raise ApplyError(
                    f"Job results contain duplicate identity for source {job['source_id']}"
                )


def apply_job_scan(
    catalog: dict[str, Any],
    career_sources: dict[str, Any],
    job_results: dict[str, Any],
    profile: dict[str, Any],
    handoff: dict[str, Any],
) -> tuple[int, int, list[dict[str, Any]]]:
    companies = {company["company_id"]: company for company in catalog["companies"]}
    sources = {source["source_id"]: source for source in career_sources["sources"]}
    source_states = job_results["source_states"]
    source_state_by_id = {state["source_id"]: state for state in source_states}
    jobs = job_results["jobs"]
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    processed_sources: set[str] = set()

    for scan in handoff["changes"]:
        company_id = scan["company_id"]
        source_id = scan["source_id"]
        source = sources.get(source_id)
        company = companies.get(company_id)
        if source_id in processed_sources:
            conflict(
                conflicts,
                company_id,
                "source_id",
                source_id,
                source_id,
                "A job-scan handoff may contain only one result per source.",
            )
            skipped += 1
            continue
        processed_sources.add(source_id)
        if company is None:
            conflict(
                conflicts,
                company_id,
                "company_id",
                None,
                company_id,
                "No catalog company has this ID.",
            )
            skipped += 1
            continue
        if source is None:
            conflict(
                conflicts,
                company_id,
                "source_id",
                None,
                source_id,
                "No career-source record has this ID.",
            )
            skipped += 1
            continue
        if source["company_id"] != company_id:
            conflict(
                conflicts,
                company_id,
                "source.company_id",
                source["company_id"],
                company_id,
                "The source belongs to a different company.",
            )
            skipped += 1
            continue
        if source["status"] != "ready":
            conflict(
                conflicts,
                company_id,
                "source.status",
                source["status"],
                "ready",
                "Only ready career sources may be scanned for jobs.",
            )
            skipped += 1
            continue
        if source["updated_date"] != scan["expected_source_updated_date"]:
            conflict(
                conflicts,
                company_id,
                "source.updated_date",
                source["updated_date"],
                scan["expected_source_updated_date"],
                "Career-source instructions changed after this handoff was prepared.",
            )
            skipped += 1
            continue
        if scan["outcome"] == "failed":
            skipped += 1
            continue

        source_state = source_state_by_id.get(source_id)
        last_successful_scan_at = (
            source_state["last_successful_scan_at"] if source_state is not None else None
        )
        if last_successful_scan_at == scan["scanned_at"]:
            skipped += 1
            continue
        if scan["expected_last_successful_scan_at"] != last_successful_scan_at:
            conflict(
                conflicts,
                company_id,
                "last_successful_scan_at",
                last_successful_scan_at,
                scan["expected_last_successful_scan_at"],
                "A newer successful scan was applied after this handoff was prepared.",
            )
            skipped += 1
            continue
        if last_successful_scan_at is not None and datetime.fromisoformat(
            scan["scanned_at"].replace("Z", "+00:00")
        ) <= datetime.fromisoformat(last_successful_scan_at.replace("Z", "+00:00")):
            conflict(
                conflicts,
                company_id,
                "scanned_at",
                last_successful_scan_at,
                scan["scanned_at"],
                "A successful scan timestamp must advance the source watermark.",
            )
            skipped += 1
            continue

        identities = scan["active_job_identities"]
        observations = scan["matching_jobs"]
        if scan["current_job_count"] != len(identities):
            conflict(
                conflicts,
                company_id,
                "current_job_count",
                len(identities),
                scan["current_job_count"],
                "The current-job count must equal the complete identity list length.",
            )
            skipped += 1
            continue

        scan_error: str | None = None
        for index, identity in enumerate(identities):
            if any(job_identities_match(identity, prior) for prior in identities[:index]):
                scan_error = "The complete source identity list contains a duplicate."
                break
        if scan_error is None:
            for index, observation in enumerate(observations):
                if observation["company_id"] != company_id or observation["source_id"] != source_id:
                    scan_error = "A matching job references a different company or source."
                    break
                if any(job_identities_match(observation, prior) for prior in observations[:index]):
                    scan_error = "The matching-job list contains a duplicate identity."
                    break
                if not any(job_identities_match(observation, identity) for identity in identities):
                    scan_error = "A matching job is absent from the complete active identity list."
                    break
                mismatch = profile_mismatch(observation, profile)
                if mismatch is not None:
                    scan_error = f"Job {observation['canonical_url']} does not match profile: {mismatch}."
                    break
        if scan_error is not None:
            conflict(
                conflicts,
                company_id,
                "source_scan",
                None,
                source_id,
                scan_error,
            )
            skipped += 1
            continue

        for observation in observations:
            index = find_job_index(jobs, observation)
            incoming_facts = job_facts(observation)
            if index is None:
                jobs.append(
                    {
                        **incoming_facts,
                        "status": "active",
                        "first_seen_at": handoff["created_at"],
                        "updated_at": handoff["created_at"],
                        "inactive_at": None,
                    }
                )
                continue
            existing = jobs[index]
            if job_facts(existing) == incoming_facts and existing["status"] == "active":
                continue
            jobs[index] = {
                **incoming_facts,
                "status": "active",
                "first_seen_at": existing["first_seen_at"],
                "updated_at": handoff["created_at"],
                "inactive_at": None,
            }

        for job in jobs:
            if job["source_id"] != source_id or job["status"] != "active":
                continue
            if any(job_identities_match(job, identity) for identity in identities):
                continue
            job["status"] = "inactive"
            job["updated_at"] = handoff["created_at"]
            job["inactive_at"] = handoff["created_at"]

        if source_state is None:
            source_state = {
                "source_id": source_id,
                "last_successful_scan_at": scan["scanned_at"],
            }
            source_states.append(source_state)
            source_state_by_id[source_id] = source_state
        else:
            source_state["last_successful_scan_at"] = scan["scanned_at"]
        applied += 1

    return applied, skipped, conflicts


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path, help="JSON handoff to apply")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--career-sources",
        type=Path,
        default=DEFAULT_CAREER_SOURCES,
        help="Career-source registry path",
    )
    parser.add_argument(
        "--job-results-dir",
        type=Path,
        default=DEFAULT_JOB_RESULTS_DIR,
        help="Directory containing profile-specific job-result files",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    handoff_path = args.handoff.expanduser().resolve()
    catalog_path = args.catalog.expanduser().resolve()
    career_sources_path = args.career_sources.expanduser().resolve()
    job_results_dir = args.job_results_dir.expanduser().resolve()

    try:
        registry, schemas = schema_registry()
        handoff = read_json(handoff_path)
        catalog = read_json(catalog_path)
        validate_json(handoff, schemas["handoff.schema.json"], registry, "Handoff")
        validate_json(catalog, schemas["companies.schema.json"], registry, "Catalog")
        career_sources = None
        profile = None
        job_results = None
        job_results_path = None
        if handoff["operation"] in {"career-source-discover", "job-scan"}:
            career_sources = read_json(career_sources_path)
            validate_json(
                career_sources,
                schemas["career-sources.schema.json"],
                registry,
                "Career-source registry",
            )
        if handoff["operation"] == "job-scan":
            profile_id = handoff["profile_id"]
            profile_path = SEARCH_PROFILE_DIR / f"{profile_id}.json"
            profile = read_json(profile_path)
            validate_json(
                profile,
                schemas["search-profile.schema.json"],
                registry,
                "Search profile",
            )
            if profile["profile_id"] != profile_id:
                raise ApplyError(
                    f"Search profile ID {profile['profile_id']!r} does not match handoff {profile_id!r}"
                )
            job_results_path = job_results_dir / f"{profile_id}.json"
            if job_results_path.exists():
                job_results = read_json(job_results_path)
            else:
                job_results = {
                    "schema_version": 1,
                    "profile_id": profile_id,
                    "updated_at": None,
                    "source_states": [],
                    "jobs": [],
                }
            validate_json(
                job_results,
                schemas["job-results.schema.json"],
                registry,
                "Job results",
            )
            if job_results["profile_id"] != profile_id:
                raise ApplyError(
                    f"Job-result profile ID {job_results['profile_id']!r} does not match handoff {profile_id!r}"
                )
            assert career_sources is not None
            validate_job_results_consistency(job_results, career_sources)

        updated = copy.deepcopy(catalog)
        updated_career_sources = copy.deepcopy(career_sources) if career_sources is not None else None
        updated_job_results = copy.deepcopy(job_results) if job_results is not None else None
        if handoff["operation"] == "company-ingest":
            applied, skipped, conflicts = apply_ingest(updated, handoff)
        elif handoff["operation"] == "company-validate":
            applied, skipped, conflicts = apply_validation(updated, handoff)
        elif handoff["operation"] == "career-source-discover":
            assert updated_career_sources is not None
            applied, skipped, conflicts = apply_career_source_discovery(
                updated, updated_career_sources, handoff
            )
        else:
            assert updated_career_sources is not None
            assert updated_job_results is not None
            assert profile is not None
            applied, skipped, conflicts = apply_job_scan(
                updated,
                updated_career_sources,
                updated_job_results,
                profile,
                handoff,
            )

        would_change = updated != catalog
        if would_change:
            updated["updated_at"] = handoff["created_at"]
        validate_json(updated, schemas["companies.schema.json"], registry, "Updated catalog")

        career_sources_would_change = (
            updated_career_sources is not None and updated_career_sources != career_sources
        )
        if career_sources_would_change:
            assert updated_career_sources is not None
            updated_career_sources["updated_at"] = handoff["created_at"]
            validate_json(
                updated_career_sources,
                schemas["career-sources.schema.json"],
                registry,
                "Updated career-source registry",
            )

        job_results_would_change = (
            updated_job_results is not None and updated_job_results != job_results
        )
        if job_results_would_change:
            assert updated_job_results is not None
            updated_job_results["updated_at"] = handoff["created_at"]
            validate_json(
                updated_job_results,
                schemas["job-results.schema.json"],
                registry,
                "Updated job results",
            )
            assert updated_career_sources is not None
            validate_job_results_consistency(updated_job_results, updated_career_sources)

        if would_change and not args.dry_run:
            atomic_write(catalog_path, updated)
        if career_sources_would_change and not args.dry_run:
            assert updated_career_sources is not None
            atomic_write(career_sources_path, updated_career_sources)
        if job_results_would_change and not args.dry_run:
            assert updated_job_results is not None
            assert job_results_path is not None
            atomic_write(job_results_path, updated_job_results)

        output = {
            "schema_version": 1,
            "operation": handoff["operation"],
            "handoff": str(handoff_path),
            "catalog": str(catalog_path),
            "dry_run": args.dry_run,
            "catalog_changed": would_change and not args.dry_run,
            "would_change": would_change,
            "career_sources": str(career_sources_path) if career_sources is not None else None,
            "career_sources_changed": career_sources_would_change and not args.dry_run,
            "career_sources_would_change": career_sources_would_change,
            "job_results": str(job_results_path) if job_results_path is not None else None,
            "job_results_changed": job_results_would_change and not args.dry_run,
            "job_results_would_change": job_results_would_change,
            "applied": applied,
            "skipped": skipped,
            "conflicts": conflicts,
            "reported_issues": handoff["issues"],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0 if not conflicts else 2
    except ApplyError as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
