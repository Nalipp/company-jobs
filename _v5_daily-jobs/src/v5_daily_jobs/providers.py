"""Unfiltered captures from the three official structured providers V4 supported."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
import re
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class CaptureError(ValueError):
    """A provider response cannot be represented as a trustworthy job capture."""


class FetchFailure(RuntimeError):
    def __init__(self, message: str, category: str, *, retryable: bool = True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class Capture:
    jobs: list[dict[str, Any]]
    complete: bool
    summary: dict[str, Any]


JsonFetcher = Callable[[str, float], Any]


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "v5-daily-jobs/0.1 (+local job collection)",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(50_000_001)
    except HTTPError as exc:
        if exc.code in {404, 410}:
            raise FetchFailure(f"HTTP {exc.code}: {url}", "source_missing", retryable=False) from exc
        category = "rate_limit" if exc.code == 429 else "source_access"
        raise FetchFailure(f"HTTP {exc.code}: {url}", category) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchFailure(f"Timeout: {url}", "timeout") from exc
    except URLError as exc:
        category = "environment_network" if isinstance(exc.reason, socket.gaierror) else "network"
        raise FetchFailure(f"Network error: {url}: {exc.reason}", category) from exc
    if len(data) > 50_000_000:
        raise FetchFailure(f"Response exceeds 50 MB: {url}", "source_access")
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchFailure(f"Invalid JSON: {url}", "source_access") from exc


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data)


def plain_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    parser = _TextParser()
    parser.feed(text)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _required(value: Any, field: str) -> str:
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise CaptureError(f"Missing {field}")
    return str(value).strip()


def normalize_url(value: Any) -> str:
    raw = _required(value, "canonical job URL")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise CaptureError(f"Invalid canonical job URL: {raw!r}")
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "gh_src", "lever-source", "lever-origin"}
    )
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urlencode(query), "")
    )


def _locations(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("location")
        text = str(value).strip() if value is not None else ""
        if text and text.casefold() not in seen:
            result.append(text)
            seen.add(text.casefold())
    return result


def _job(
    raw: dict[str, Any], *, source: dict[str, Any], source_job_id: Any,
    title: Any, url: Any, description: Any, locations: list[Any],
    workplace: Any = None, employment: Any = None, compensation: Any = None,
    compensation_text: Any = None, published_at: Any = None,
    updated_at: Any = None, department: Any = None, team: Any = None,
) -> dict[str, Any]:
    provider_id = _required(source_job_id, "source job ID")
    job_url = normalize_url(url)
    return {
        "source_job_id": provider_id,
        "canonical_url": job_url,
        "title": _required(title, "job title"),
        "description_text": plain_text(description),
        "locations": _locations(locations),
        "workplace_text": str(workplace).strip() if workplace is not None else None,
        "employment_text": str(employment).strip() if employment is not None else None,
        "compensation_text": str(compensation_text).strip() if compensation_text is not None else None,
        "published_at": str(published_at) if published_at is not None else None,
        "details": {
            "provider": source["provider"],
            "workplace_arrangement": workplace,
            "employment_type": employment,
            "compensation": compensation,
            "department": department,
            "team": team,
            "updated_at": updated_at,
        },
        "raw_posting": raw,
    }


def _dedupe(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_id: dict[str, dict[str, Any]] = {}
    by_url: dict[str, str] = {}
    duplicates = 0
    for job in jobs:
        key = job["source_job_id"]
        prior = by_id.get(key)
        if prior is not None:
            if prior != job:
                raise CaptureError(f"Conflicting duplicate source job ID: {key}")
            duplicates += 1
            continue
        if job["canonical_url"] in by_url:
            raise CaptureError(f"Different jobs share URL: {job['canonical_url']}")
        by_id[key] = job
        by_url[job["canonical_url"]] = key
    return list(by_id.values()), duplicates


def _parse_ashby(payload: Any, source: dict[str, Any]) -> Capture:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CaptureError("Ashby response lacks jobs array")
    if payload.get("apiVersion") is not None and str(payload["apiVersion"]) != "1":
        raise CaptureError("Unsupported Ashby API version")
    parsed: list[dict[str, Any]] = []
    for raw in payload["jobs"]:
        if not isinstance(raw, dict):
            raise CaptureError("Ashby job is not an object")
        if raw.get("isListed") is False:
            continue
        provider_id = _required(raw.get("id"), "Ashby job ID")
        secondary = raw.get("secondaryLocations") or []
        if not isinstance(secondary, list):
            raise CaptureError("Ashby secondary locations are invalid")
        description = raw.get("descriptionPlain") or raw.get("descriptionHtml")
        compensation = raw.get("compensation")
        parsed.append(_job(
            raw, source=source, source_job_id=provider_id, title=raw.get("title"),
            url=raw.get("jobUrl") or f"https://jobs.ashbyhq.com/{quote(source['board_id'], safe='')}/{quote(provider_id, safe='')}",
            description=description, locations=[raw.get("location"), *secondary],
            workplace=raw.get("workplaceType") or ("remote" if raw.get("isRemote") is True else None),
            employment=raw.get("employmentType"), compensation=compensation,
            compensation_text=(compensation or {}).get("scrapeableCompensationSalarySummary") if isinstance(compensation, dict) else None,
            published_at=raw.get("publishedAt"), updated_at=raw.get("updatedAt"),
            department=raw.get("department"), team=raw.get("team"),
        ))
    jobs, duplicates = _dedupe(parsed)
    total = payload.get("meta", {}).get("total") if isinstance(payload.get("meta"), dict) else payload.get("total")
    complete = not (isinstance(total, int) and total != len(payload["jobs"]))
    return Capture(jobs, complete, {
        "provider": "ashby", "pages": 1, "raw_jobs": len(payload["jobs"]),
        "unique_jobs": len(jobs), "duplicates": duplicates,
        "completeness_reason": "single_page" if complete else "reported_total_mismatch",
    })


def _parse_greenhouse(payload: Any, source: dict[str, Any]) -> Capture:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CaptureError("Greenhouse response lacks jobs array")
    parsed: list[dict[str, Any]] = []
    for raw in payload["jobs"]:
        if not isinstance(raw, dict):
            raise CaptureError("Greenhouse job is not an object")
        provider_id = _required(raw.get("id"), "Greenhouse job ID")
        offices = raw.get("offices") or []
        if not isinstance(offices, list):
            raise CaptureError("Greenhouse offices are invalid")
        metadata = {
            str(item.get("name", "")).casefold(): item.get("value")
            for item in (raw.get("metadata") or []) if isinstance(item, dict)
        }
        parsed.append(_job(
            raw, source=source, source_job_id=provider_id, title=raw.get("title"),
            url=raw.get("absolute_url") or f"https://job-boards.greenhouse.io/{quote(source['board_id'], safe='')}/jobs/{quote(provider_id, safe='')}",
            description=raw.get("content"), locations=[raw.get("location"), *offices],
            workplace=raw.get("workplace_type") or metadata.get("workplace type"),
            employment=raw.get("employment_type") or metadata.get("employment type"),
            compensation=raw.get("compensation") or raw.get("pay_input_ranges"),
            compensation_text=metadata.get("salary range") or metadata.get("compensation"),
            published_at=raw.get("first_published"), updated_at=raw.get("updated_at"),
            department=raw.get("departments"), team=metadata.get("team"),
        ))
    jobs, duplicates = _dedupe(parsed)
    total = payload.get("meta", {}).get("total") if isinstance(payload.get("meta"), dict) else None
    complete = not (isinstance(total, int) and total != len(payload["jobs"]))
    return Capture(jobs, complete, {
        "provider": "greenhouse", "pages": 1, "raw_jobs": len(payload["jobs"]),
        "unique_jobs": len(jobs), "duplicates": duplicates,
        "completeness_reason": "single_page" if complete else "reported_total_mismatch",
    })


def _parse_lever(pages: list[Any], source: dict[str, Any], *, terminal: bool) -> Capture:
    raw_jobs: list[dict[str, Any]] = []
    for page in pages:
        values = page.get("postings") if isinstance(page, dict) else page
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise CaptureError("Lever page is not a postings array")
        raw_jobs.extend(values)
    parsed: list[dict[str, Any]] = []
    eu = "eu.lever.co" in str(source.get("listings_url") or "").lower()
    host = "jobs.eu.lever.co" if eu else "jobs.lever.co"
    for raw in raw_jobs:
        provider_id = _required(raw.get("id"), "Lever posting ID")
        categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        description = " ".join(str(value) for value in [
            raw.get("descriptionPlain") or raw.get("description"),
            *[item.get("text") or item.get("content") for item in (raw.get("lists") or []) if isinstance(item, dict)],
            raw.get("additionalPlain") or raw.get("additional"),
        ] if value)
        locations = [categories.get("location"), *(categories.get("allLocations") or [])]
        compensation = raw.get("salaryRange") or raw.get("compensation")
        parsed.append(_job(
            raw, source=source, source_job_id=provider_id, title=raw.get("text"),
            url=raw.get("hostedUrl") or f"https://{host}/{quote(source['board_id'], safe='')}/{quote(provider_id, safe='')}",
            description=description, locations=locations,
            workplace=raw.get("workplaceType") or categories.get("workplaceType"),
            employment=categories.get("commitment") or raw.get("commitment"),
            compensation=compensation,
            compensation_text=raw.get("salaryDescriptionPlain") or raw.get("salaryDescription"),
            published_at=raw.get("createdAt"), updated_at=raw.get("updatedAt"),
            department=categories.get("department"), team=categories.get("team"),
        ))
    jobs, duplicates = _dedupe(parsed)
    complete = terminal and duplicates == 0
    return Capture(jobs, complete, {
        "provider": "lever", "pages": len(pages), "raw_jobs": len(raw_jobs),
        "unique_jobs": len(jobs), "duplicates": duplicates,
        "completeness_reason": "terminal_page" if complete else "page_cap_or_overlap",
    })


def fetch_structured(
    source: dict[str, Any], *, fetcher: JsonFetcher = fetch_json,
    timeout: float = 30.0, lever_page_size: int = 100, lever_max_pages: int = 100,
) -> Capture:
    provider = source["provider"]
    board = quote(_required(source.get("board_id"), "board ID"), safe="")
    if provider == "ashby":
        return _parse_ashby(fetcher(
            f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true", timeout
        ), source)
    if provider == "greenhouse":
        return _parse_greenhouse(fetcher(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", timeout
        ), source)
    if provider != "lever":
        raise CaptureError(f"No structured adapter for {provider}")
    eu = "eu.lever.co" in str(source.get("listings_url") or "").lower()
    host = "api.eu.lever.co" if eu else "api.lever.co"
    pages: list[Any] = []
    terminal = False
    for page_number in range(lever_max_pages):
        skip = page_number * lever_page_size
        page = fetcher(
            f"https://{host}/v0/postings/{board}?mode=json&skip={skip}&limit={lever_page_size}",
            timeout,
        )
        values = page.get("postings") if isinstance(page, dict) else page
        if not isinstance(values, list):
            raise CaptureError("Lever page is not a postings array")
        pages.append(page)
        if len(values) < lever_page_size:
            terminal = True
            break
    return _parse_lever(pages, source, terminal=terminal)
