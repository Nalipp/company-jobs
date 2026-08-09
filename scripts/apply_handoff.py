#!/usr/bin/env python3
"""Validate and idempotently apply a V3 company handoff."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "data" / "companies.json"
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
    for name in ("companies.schema.json", "handoff.schema.json"):
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

        result = change["address_result"]
        if address_result_matches(company, result, handoff["created_at"]):
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

        for field in ADDRESS_FIELDS:
            company[field] = copy.deepcopy(result[field])
        company["updated_date"] = handoff["created_at"]
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
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    handoff_path = args.handoff.expanduser().resolve()
    catalog_path = args.catalog.expanduser().resolve()

    try:
        registry, schemas = schema_registry()
        handoff = read_json(handoff_path)
        catalog = read_json(catalog_path)
        validate_json(handoff, schemas["handoff.schema.json"], registry, "Handoff")
        validate_json(catalog, schemas["companies.schema.json"], registry, "Catalog")

        updated = copy.deepcopy(catalog)
        if handoff["operation"] == "company-ingest":
            applied, skipped, conflicts = apply_ingest(updated, handoff)
        else:
            applied, skipped, conflicts = apply_validation(updated, handoff)

        would_change = updated != catalog
        if would_change:
            updated["updated_at"] = handoff["created_at"]
        validate_json(updated, schemas["companies.schema.json"], registry, "Updated catalog")

        if would_change and not args.dry_run:
            atomic_write(catalog_path, updated)

        output = {
            "schema_version": 1,
            "operation": handoff["operation"],
            "handoff": str(handoff_path),
            "catalog": str(catalog_path),
            "dry_run": args.dry_run,
            "catalog_changed": would_change and not args.dry_run,
            "would_change": would_change,
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

