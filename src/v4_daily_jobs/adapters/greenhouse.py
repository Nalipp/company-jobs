"""Parser for Greenhouse's public Job Board API payload."""

from __future__ import annotations

from typing import Any, Mapping
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
    require_text,
    stable_job_id,
)


PROVIDER = "greenhouse"


def request_url(board_id: str, *, include_content: bool = True) -> str:
    """Return Greenhouse's public, unpaginated Job Board endpoint."""

    board = quote(require_text(board_id, "Greenhouse board ID"), safe="")
    suffix = "?content=true" if include_content else ""
    return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs{suffix}"


def _metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in raw.get("metadata", []) or []:
        if not isinstance(item, Mapping):
            continue
        name = optional_text(item.get("name"))
        if name:
            result[name.casefold()] = item.get("value")
    return result


def _first_name(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    names = [optional_text(item.get("name")) for item in items if isinstance(item, Mapping)]
    return ", ".join(name for name in names if name) or None


def parse_payload(
    payload: Mapping[str, Any],
    *,
    company_id: str,
    source_id: str,
    board_id: str,
    enumeration_complete: bool = True,
) -> AdapterResult:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("jobs"), list):
        raise AdapterError("Greenhouse payload must contain a jobs array")
    raw_jobs = payload["jobs"]
    parsed: list[NormalizedJob] = []
    for raw in raw_jobs:
        if not isinstance(raw, Mapping):
            raise AdapterError("Greenhouse jobs must be objects")
        provider_id = require_text(raw.get("id"), "Greenhouse job ID")
        metadata = _metadata(raw)
        location_value = raw.get("location")
        location_text = (
            location_value.get("name")
            if isinstance(location_value, Mapping)
            else location_value
        )
        office_locations = [
            item.get("location") or item.get("name")
            for item in (raw.get("offices") or [])
            if isinstance(item, Mapping)
        ]
        workplace_raw = (
            raw.get("workplace_type")
            or metadata.get("workplace type")
            or metadata.get("workplace")
        )
        employment_raw = (
            raw.get("employment_type")
            or metadata.get("employment type")
            or metadata.get("commitment")
        )
        compensation_value = raw.get("compensation")
        compensation = normalize_structured_compensation(compensation_value)
        compensation_text = optional_text(
            metadata.get("salary range")
            or metadata.get("compensation")
            or (
                compensation_value.get("source_text")
                if isinstance(compensation_value, Mapping)
                else None
            )
        )
        pay_ranges = raw.get("pay_input_ranges")
        if compensation_text is None and isinstance(pay_ranges, list):
            rendered_ranges: list[str] = []
            for pay_range in pay_ranges:
                if not isinstance(pay_range, Mapping):
                    continue
                minimum = pay_range.get("min_cents")
                maximum = pay_range.get("max_cents")
                currency = optional_text(pay_range.get("currency_type"))
                if (
                    isinstance(minimum, (int, float))
                    and isinstance(maximum, (int, float))
                    and currency
                ):
                    rendered_ranges.append(
                        f"{currency.upper()} {minimum / 100:g}-{maximum / 100:g}; "
                        f"{optional_text(pay_range.get('title')) or 'pay range'}"
                    )
            compensation_text = "; ".join(rendered_ranges) or None
        url = raw.get("absolute_url") or (
            "https://job-boards.greenhouse.io/"
            f"{quote(board_id, safe='')}/jobs/{quote(provider_id, safe='')}"
        )
        parsed.append(
            NormalizedJob(
                stable_id=stable_job_id(source_id, provider_id),
                company_id=company_id,
                source_id=source_id,
                provider=PROVIDER,
                source_job_id=provider_id,
                canonical_url=normalize_url(url),
                title=require_text(raw.get("title"), "Greenhouse title"),
                description_text=html_to_text(raw.get("content")),
                locations=normalized_locations([location_text, *office_locations]),
                workplace_arrangement=normalize_workplace(workplace_raw),
                workplace_text=optional_text(workplace_raw),
                employment_type=normalize_employment(employment_raw),
                employment_text=optional_text(employment_raw),
                compensation=compensation,
                compensation_text=compensation_text,
                department=_first_name(raw.get("departments")),
                team=optional_text(metadata.get("team")),
                posted_at=normalize_timestamp(raw.get("first_published")),
                updated_at=normalize_timestamp(raw.get("updated_at")),
            )
        )
    jobs, duplicates = deduplicate_jobs(parsed)
    meta = payload.get("meta")
    reported = meta.get("total") if isinstance(meta, Mapping) else None
    total_reported = (
        int(reported)
        if isinstance(reported, int) and not isinstance(reported, bool)
        else None
    )
    complete = enumeration_complete
    reason = "complete_single_page" if complete else "caller_reported_incomplete"
    if total_reported is not None and total_reported != len(raw_jobs):
        complete = False
        reason = "reported_total_mismatch"
    return AdapterResult(
        provider=PROVIDER,
        company_id=company_id,
        source_id=source_id,
        jobs=jobs,
        fetch=FetchMetadata(
            provider=PROVIDER,
            complete=complete,
            completeness_reason=reason,
            pages_received=1,
            pages_expected=1,
            total_reported=total_reported,
            raw_jobs_seen=len(raw_jobs),
            unique_jobs=len(jobs),
            duplicates_removed=duplicates,
        ),
    )


parse = parse_payload
