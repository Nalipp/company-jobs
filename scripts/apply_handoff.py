#!/usr/bin/env python3
"""Validate and idempotently apply one Daily Jobs v2 handoff."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
except ImportError:
    print(
        "Missing dependency. Run: python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "data" / "companies.json"
COMPANIES_SCHEMA = ROOT / "schemas" / "companies.schema.json"
HANDOFF_SCHEMA = ROOT / "schemas" / "handoff.schema.json"
TRACKING_PARAMETERS = {"gh_src", "lever-source", "source"}


class InputError(Exception):
    """Raised when an input file or its JSON content is invalid."""


def read_json(path: Path | None) -> Any:
    try:
        if path is None:
            return json.load(sys.stdin)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        label = "stdin" if path is None else str(path)
        raise InputError(f"Could not read valid JSON from {label}: {exc}") from exc


def format_validation_errors(validator: Draft202012Validator, value: Any) -> str:
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    lines = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        lines.append(f"{location}: {error.message}")
    return "\n".join(lines)


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_PARAMETERS
    ]
    path = parts.path.rstrip("/")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def latest_timestamp(left: str, right: str) -> str:
    return right if parse_timestamp(right) > parse_timestamp(left) else left


def company_matches(candidate: dict[str, Any], saved: dict[str, Any]) -> set[str]:
    matches: set[str] = set()
    candidate_ats = candidate["ats"]
    saved_ats = saved["ats"]
    if (
        candidate_ats["board_id"] is not None
        and saved_ats["board_id"] is not None
        and candidate_ats["provider"] == saved_ats["provider"]
        and candidate_ats["board_id"] == saved_ats["board_id"]
    ):
        matches.add("ats")
    if (
        candidate["primary_domain"] is not None
        and saved["primary_domain"] is not None
        and candidate["primary_domain"].lower() == saved["primary_domain"].lower()
    ):
        matches.add("domain")
    if normalize_url(candidate["jobs_url"]) == normalize_url(saved["jobs_url"]):
        matches.add("jobs_url")
    return matches


def find_company(catalog: dict[str, Any], company_id: str) -> dict[str, Any] | None:
    matches = [item for item in catalog["companies"] if item["company_id"] == company_id]
    return matches[0] if len(matches) == 1 else None


def job_is_known(candidate: dict[str, Any], saved: dict[str, Any]) -> bool:
    candidate_id = candidate["job_id"]
    saved_id = saved["job_id"]
    if candidate_id is not None and saved_id is not None:
        return candidate_id == saved_id
    return normalize_url(candidate["url"]) == normalize_url(saved["url"])


def apply_discovery(
    catalog: dict[str, Any], changes: list[dict[str, Any]]
) -> tuple[int, int, list[dict[str, Any]]]:
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    for index, candidate in enumerate(changes):
        matched_indexes = {
            saved_index
            for saved_index, saved in enumerate(catalog["companies"])
            if company_matches(candidate, saved)
        }
        id_matches = [
            saved
            for saved in catalog["companies"]
            if saved["company_id"] == candidate["company_id"]
        ]
        if len(matched_indexes) > 1:
            conflicts.append(
                {
                    "change_index": index,
                    "company_id": candidate["company_id"],
                    "reason": "identity keys match more than one saved company",
                }
            )
        elif len(matched_indexes) == 1:
            skipped += 1
        elif id_matches:
            conflicts.append(
                {
                    "change_index": index,
                    "company_id": candidate["company_id"],
                    "reason": "company_id is already used by a different company identity",
                }
            )
        else:
            catalog["companies"].append(copy.deepcopy(candidate))
            applied += 1
    return applied, skipped, conflicts


def apply_addresses(
    catalog: dict[str, Any], changes: list[dict[str, Any]]
) -> tuple[int, int, list[dict[str, Any]]]:
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    for index, change in enumerate(changes):
        company = find_company(catalog, change["company_id"])
        if company is None:
            conflicts.append(
                {
                    "change_index": index,
                    "company_id": change["company_id"],
                    "reason": "company_id does not identify exactly one saved company",
                }
            )
            continue
        if company["map_location"] == change["map_location"]:
            skipped += 1
            continue
        current_status = company["map_location"]["status"]
        if current_status != change["expected_status"]:
            conflicts.append(
                {
                    "change_index": index,
                    "company_id": change["company_id"],
                    "reason": (
                        f"expected map status {change['expected_status']!r}, "
                        f"found {current_status!r}"
                    ),
                }
            )
            continue
        company["map_location"] = copy.deepcopy(change["map_location"])
        applied += 1
    return applied, skipped, conflicts


def apply_jobs(
    catalog: dict[str, Any], changes: list[dict[str, Any]], created_at: str
) -> tuple[int, int, list[dict[str, Any]]]:
    applied = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    for index, change in enumerate(changes):
        company = find_company(catalog, change["company_id"])
        if company is None:
            conflicts.append(
                {
                    "change_index": index,
                    "company_id": change["company_id"],
                    "reason": "company_id does not identify exactly one saved company",
                }
            )
            continue
        company_changed = False
        for job in change["new_jobs"]:
            if any(job_is_known(job, saved) for saved in company["jobs"]):
                skipped += 1
                continue
            company["jobs"].append(copy.deepcopy(job))
            applied += 1
            company_changed = True
        if company_changed:
            company["last_verified_at"] = latest_timestamp(
                company["last_verified_at"], created_at
            )
    return applied, skipped, conflicts


def canonicalize(catalog: dict[str, Any]) -> None:
    for company in catalog["companies"]:
        company["jobs"].sort(
            key=lambda job: (
                job["verified_at"],
                job["job_id"] or "",
                normalize_url(job["url"]),
                job["title"],
            )
        )
    catalog["companies"].sort(
        key=lambda company: (company["discovered_at"], company["company_id"])
    )


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def build_validators() -> tuple[Draft202012Validator, Draft202012Validator]:
    companies_schema = read_json(COMPANIES_SCHEMA)
    handoff_schema = read_json(HANDOFF_SCHEMA)
    registry = Registry().with_resource(
        companies_schema["$id"], Resource.from_contents(companies_schema)
    )
    checker = FormatChecker()
    return (
        Draft202012Validator(handoff_schema, registry=registry, format_checker=checker),
        Draft202012Validator(companies_schema, format_checker=checker),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and idempotently apply a Daily Jobs v2 handoff."
    )
    parser.add_argument("handoff", help="Handoff JSON path, or - for stdin")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Catalog path; defaults to data/companies.json",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and plan without writing"
    )
    args = parser.parse_args()

    handoff_path = None if args.handoff == "-" else Path(args.handoff).resolve()
    catalog_path = args.catalog.resolve()
    try:
        handoff = read_json(handoff_path)
        catalog = read_json(catalog_path)
        handoff_validator, catalog_validator = build_validators()

        handoff_errors = format_validation_errors(handoff_validator, handoff)
        if handoff_errors:
            raise InputError(f"Handoff schema validation failed:\n{handoff_errors}")
        catalog_errors = format_validation_errors(catalog_validator, catalog)
        if catalog_errors:
            raise InputError(f"Catalog schema validation failed:\n{catalog_errors}")

        proposed = copy.deepcopy(catalog)
        operation = handoff["operation"]
        if operation == "company-discover":
            applied, skipped, conflicts = apply_discovery(proposed, handoff["changes"])
        elif operation == "validate-company-address":
            applied, skipped, conflicts = apply_addresses(proposed, handoff["changes"])
        else:
            applied, skipped, conflicts = apply_jobs(
                proposed, handoff["changes"], handoff["created_at"]
            )

        changed = applied > 0
        if changed:
            proposed["updated_at"] = latest_timestamp(
                proposed["updated_at"], handoff["created_at"]
            )
            canonicalize(proposed)
            proposed_errors = format_validation_errors(catalog_validator, proposed)
            if proposed_errors:
                raise InputError(
                    f"Post-merge catalog validation failed:\n{proposed_errors}"
                )
            if not args.dry_run:
                atomic_write(catalog_path, proposed)

        summary = {
            "schema_version": 1,
            "operation": operation,
            "handoff": "stdin" if handoff_path is None else str(handoff_path),
            "catalog": str(catalog_path),
            "dry_run": args.dry_run,
            "catalog_changed": changed and not args.dry_run,
            "would_change": changed,
            "applied": applied,
            "skipped": skipped,
            "conflicts": conflicts,
            "reported_issues": handoff["issues"],
        }
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if not conflicts else 1
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
