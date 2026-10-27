"""Parser for Ashby's public job-board API payload."""

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
    provider_url,
    require_text,
    stable_job_id,
)


PROVIDER = "ashby"


def request_url(board_id: str, *, include_compensation: bool = True) -> str:
    """Return the official public single-page board endpoint."""

    board = quote(require_text(board_id, "Ashby board ID"), safe="")
    include = "true" if include_compensation else "false"
    return (
        f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        f"?includeCompensation={include}"
    )


def _compensation(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    value = raw.get("compensation")
    if not isinstance(value, Mapping):
        return None, None
    text = optional_text(
        value.get("scrapeableCompensationSalarySummary")
        or value.get("compensationTierSummary")
    )
    candidates: list[Mapping[str, Any]] = []
    if all(
        key in value
        for key in ("maxValue", "currencyCode", "interval")
    ):
        candidates.append(value)
    for tier in value.get("compensationTiers", []) or []:
        if not isinstance(tier, Mapping):
            continue
        components = tier.get("components", [])
        if isinstance(components, list):
            candidates.extend(item for item in components if isinstance(item, Mapping))
    summary_components = value.get("summaryComponents", [])
    if isinstance(summary_components, list):
        candidates.extend(
            item for item in summary_components if isinstance(item, Mapping)
        )
    # Ignore equity/bonus/total ranges. If geographic tiers contain different
    # salaries, choosing a tier without knowing which applies to this location is
    # unsafe; preserve the summary for semantic review instead.
    normalized_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        normalized = normalize_structured_compensation(candidate, source_text=text)
        if normalized is not None and normalized["basis"] == "base":
            normalized_candidates.append(normalized)
    distinct = {
        (
            item["minimum"],
            item["maximum"],
            item["currency"],
            item["basis"],
            item["period"],
        ): item
        for item in normalized_candidates
    }
    if len(distinct) == 1:
        normalized = next(iter(distinct.values()))
        return normalized, text or normalized["source_text"]
    return None, text


def parse_payload(
    payload: Mapping[str, Any],
    *,
    company_id: str,
    source_id: str,
    board_id: str,
    enumeration_complete: bool = True,
) -> AdapterResult:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("jobs"), list):
        raise AdapterError("Ashby payload must contain a jobs array")
    api_version = payload.get("apiVersion")
    if api_version is not None and str(api_version) != "1":
        raise AdapterError(f"unsupported Ashby public API version {api_version!r}")
    raw_jobs = payload["jobs"]
    parsed: list[NormalizedJob] = []
    for raw in raw_jobs:
        if not isinstance(raw, Mapping):
            raise AdapterError("Ashby jobs must be objects")
        # The board endpoint can expose unlisted jobs. They are not current public
        # postings and therefore do not belong in the current enumeration.
        if raw.get("isListed") is False:
            continue
        provider_id = require_text(raw.get("id"), "Ashby job ID")
        title = require_text(raw.get("title"), "Ashby title")
        url = raw.get("jobUrl") or provider_url(
            "jobs.ashbyhq.com", board_id, provider_id
        )
        primary_location = raw.get("location")
        secondary = raw.get("secondaryLocations") or []
        location_values: list[Any] = [primary_location]
        address = raw.get("address")
        postal = address.get("postalAddress") if isinstance(address, Mapping) else None
        if isinstance(postal, Mapping):
            location_values.append(
                ", ".join(
                    str(value)
                    for value in (
                        postal.get("addressLocality"),
                        postal.get("addressRegion"),
                        postal.get("addressCountry"),
                    )
                    if value
                )
            )
        if isinstance(secondary, list):
            for item in secondary:
                if isinstance(item, Mapping):
                    location_values.append(item.get("location") or item.get("name"))
                    secondary_address = item.get("address")
                    if isinstance(secondary_address, Mapping):
                        location_values.append(
                            ", ".join(
                                str(value)
                                for value in (
                                    secondary_address.get("addressLocality"),
                                    secondary_address.get("addressRegion"),
                                    secondary_address.get("addressCountry"),
                                )
                                if value
                            )
                        )
                else:
                    location_values.append(item)
        workplace_raw = raw.get("workplaceType") or raw.get("workplace")
        if workplace_raw is None and raw.get("isRemote") is True:
            workplace_raw = "remote"
        compensation, compensation_text = _compensation(raw)
        description = raw.get("descriptionPlain")
        if description is None:
            description = raw.get("descriptionHtml")
        parsed.append(
            NormalizedJob(
                stable_id=stable_job_id(source_id, provider_id),
                company_id=company_id,
                source_id=source_id,
                provider=PROVIDER,
                source_job_id=provider_id,
                canonical_url=normalize_url(url),
                title=title,
                description_text=html_to_text(description),
                locations=normalized_locations(location_values),
                workplace_arrangement=normalize_workplace(workplace_raw),
                workplace_text=optional_text(workplace_raw),
                employment_type=normalize_employment(raw.get("employmentType")),
                employment_text=optional_text(raw.get("employmentType")),
                compensation=compensation,
                compensation_text=compensation_text,
                department=optional_text(raw.get("department")),
                team=optional_text(raw.get("team")),
                posted_at=normalize_timestamp(raw.get("publishedAt")),
                updated_at=normalize_timestamp(raw.get("updatedAt")),
            )
        )
    jobs, duplicates = deduplicate_jobs(parsed)
    reported = (
        payload.get("meta", {}).get("total")
        if isinstance(payload.get("meta"), Mapping)
        else payload.get("total")
    )
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
