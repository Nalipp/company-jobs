"""Parser for Lever's public postings API, including paginated captures."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .base import (
    AdapterError,
    AdapterResult,
    FetchMetadata,
    NormalizedJob,
    deduplicate_jobs,
    html_to_text,
    normalize_employment,
    normalize_structured_compensation,
    normalize_timestamp,
    normalize_url,
    normalize_workplace,
    normalized_locations,
    optional_text,
    provider_url,
    require_text,
    stable_job_id,
)


PROVIDER = "lever"


def request_url(
    board_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
    eu: bool = False,
) -> str:
    """Return one official Lever pagination request URL."""

    if not isinstance(skip, int) or isinstance(skip, bool) or skip < 0:
        raise AdapterError("Lever skip must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise AdapterError("Lever limit must be a positive integer")
    board = quote(require_text(board_id, "Lever board ID"), safe="")
    host = "api.eu.lever.co" if eu else "api.lever.co"
    return f"https://{host}/v0/postings/{board}?mode=json&skip={skip}&limit={limit}"


def _page_jobs(page: Any) -> list[Mapping[str, Any]]:
    if isinstance(page, list):
        values = page
    elif isinstance(page, Mapping) and isinstance(page.get("postings"), list):
        values = page["postings"]
    else:
        raise AdapterError("each Lever page must be an array or contain postings")
    if not all(isinstance(item, Mapping) for item in values):
        raise AdapterError("Lever postings must be objects")
    return list(values)


def parse_pages(
    pages: Sequence[Any],
    *,
    company_id: str,
    source_id: str,
    board_id: str,
    enumeration_complete: bool,
    expected_pages: int | None = None,
    expected_total: int | None = None,
    eu: bool = False,
) -> AdapterResult:
    """Parse captured Lever pages.

    Lever uses ``skip``/``limit`` pagination and its raw list response does not
    prove that the caller fetched the final page.  Callers must therefore pass
    ``enumeration_complete=True`` only after observing the terminal page.
    """

    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or not pages:
        raise AdapterError("Lever parsing requires at least one captured page")
    raw_jobs: list[Mapping[str, Any]] = []
    for page in pages:
        raw_jobs.extend(_page_jobs(page))
    parsed: list[NormalizedJob] = []
    for raw in raw_jobs:
        provider_id = require_text(raw.get("id"), "Lever posting ID")
        categories = raw.get("categories") if isinstance(raw.get("categories"), Mapping) else {}
        locations: list[Any] = [categories.get("location")]
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations.extend(all_locations)
        workplace_raw = raw.get("workplaceType") or categories.get("workplaceType")
        employment_raw = categories.get("commitment") or raw.get("commitment")
        compensation_value = raw.get("salaryRange") or raw.get("compensation")
        compensation = normalize_structured_compensation(compensation_value)
        compensation_text = optional_text(
            raw.get("salaryDescriptionPlain") or raw.get("salaryDescription")
        )
        if isinstance(compensation_value, Mapping):
            compensation_text = compensation_text or optional_text(
                compensation_value.get("source_text")
                or compensation_value.get("salaryRange")
            )
        if compensation_text is None and compensation is not None:
            compensation_text = compensation["source_text"]
        description_parts = [raw.get("descriptionPlain") or raw.get("description")]
        for item in raw.get("lists", []) or []:
            if isinstance(item, Mapping):
                description_parts.extend([item.get("text"), item.get("content")])
        description_parts.append(raw.get("additionalPlain") or raw.get("additional"))
        description = " ".join(
            value for value in (html_to_text(part) for part in description_parts) if value
        )
        job_host = "jobs.eu.lever.co" if eu else "jobs.lever.co"
        url = raw.get("hostedUrl") or provider_url(job_host, board_id, provider_id)
        parsed.append(
            NormalizedJob(
                stable_id=stable_job_id(source_id, provider_id),
                company_id=company_id,
                source_id=source_id,
                provider=PROVIDER,
                source_job_id=provider_id,
                canonical_url=normalize_url(url),
                title=require_text(raw.get("text"), "Lever title"),
                description_text=html_to_text(description),
                locations=normalized_locations(locations),
                workplace_arrangement=normalize_workplace(workplace_raw),
                workplace_text=optional_text(workplace_raw),
                employment_type=normalize_employment(employment_raw),
                employment_text=optional_text(employment_raw),
                compensation=compensation,
                compensation_text=compensation_text,
                department=optional_text(categories.get("department")),
                team=optional_text(categories.get("team")),
                posted_at=normalize_timestamp(raw.get("createdAt")),
                updated_at=normalize_timestamp(raw.get("updatedAt")),
            )
        )
    jobs, duplicates = deduplicate_jobs(parsed)
    complete = bool(enumeration_complete)
    reason = "complete_paginated_enumeration" if complete else "caller_reported_incomplete"
    if expected_pages is not None and expected_pages != len(pages):
        complete = False
        reason = "page_count_mismatch"
    if expected_total is not None and expected_total != len(raw_jobs):
        complete = False
        reason = "reported_total_mismatch"
    if duplicates:
        # With offset pagination, overlap means the board changed while pages
        # were being fetched; an unseen posting may have shifted across an
        # offset. Keep the parsed facts, but never call that enumeration complete.
        complete = False
        reason = "pagination_overlap_detected"
    return AdapterResult(
        provider=PROVIDER,
        company_id=company_id,
        source_id=source_id,
        jobs=jobs,
        fetch=FetchMetadata(
            provider=PROVIDER,
            complete=complete,
            completeness_reason=reason,
            pages_received=len(pages),
            pages_expected=expected_pages,
            total_reported=expected_total,
            raw_jobs_seen=len(raw_jobs),
            unique_jobs=len(jobs),
            duplicates_removed=duplicates,
        ),
    )


def parse_payload(
    payload: Any,
    *,
    company_id: str,
    source_id: str,
    board_id: str,
    enumeration_complete: bool,
    expected_total: int | None = None,
    eu: bool = False,
) -> AdapterResult:
    return parse_pages(
        [payload],
        company_id=company_id,
        source_id=source_id,
        board_id=board_id,
        enumeration_complete=enumeration_complete,
        expected_pages=1,
        expected_total=expected_total,
        eu=eu,
    )


parse = parse_payload
