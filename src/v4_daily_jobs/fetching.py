"""Deterministic structured-source fetch and job-result draft creation."""

from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapters import ashby, greenhouse, lever
from .adapters.base import AdapterError, AdapterResult
from .audit import profile_digest
from .json_store import JsonStore
from .jobs import evaluate_jobs, rejection_counts_from_evaluation, semantic_candidates
from .paths import RepoPaths
from .queue import format_timestamp
from .schemas import validator


class FetchError(RuntimeError):
    """Structured access failure with deterministic retry classification."""

    def __init__(self, message: str, failure_class: str = "network") -> None:
        super().__init__(message)
        self.failure_class = failure_class


JsonFetcher = Callable[[str, float], Any]


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "v4-daily-jobs/0.1 (+local deterministic research)",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
    except HTTPError as exc:
        if exc.code == 429:
            failure_class = "rate_limit"
        elif exc.code in {401, 403}:
            failure_class = "authentication"
        else:
            failure_class = "source_access"
        raise FetchError(f"HTTP {exc.code} while fetching {url}", failure_class) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FetchError(f"Timed out while fetching {url}", "timeout") from exc
    except URLError as exc:
        # Provider API hosts are fixed by the adapters. A resolver failure for
        # one of those hosts normally means the local execution environment
        # has no DNS/network permission, not that this career source is bad.
        # Keep that distinct so it cannot consume the source's retry budget.
        failure_class = (
            "environment_network"
            if isinstance(exc.reason, socket.gaierror)
            else "network"
        )
        raise FetchError(
            f"Network error while fetching {url}: {exc.reason}", failure_class
        ) from exc
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"Source returned invalid JSON at {url}: {exc}", "source_access") from exc


def fetch_structured_source(
    source: Mapping[str, Any],
    *,
    company_id: str,
    fetcher: JsonFetcher = fetch_json,
    timeout: float = 30.0,
    lever_page_size: int = 100,
    lever_max_pages: int = 100,
) -> tuple[AdapterResult, list[str]]:
    provider = source.get("provider")
    source_id = source.get("source_id")
    board_id = source.get("board_id")
    if provider not in {"ashby", "greenhouse", "lever"}:
        raise FetchError(
            f"Source {source_id} provider {provider!r} has no deterministic V4 adapter",
            "source_access",
        )
    if not source.get("capabilities", {}).get("structured_data"):
        raise FetchError(
            f"Source {source_id} is not marked structured-data capable",
            "source_access",
        )
    if not isinstance(board_id, str) or not board_id:
        raise FetchError(f"Source {source_id} lacks a provider board ID", "source_access")

    urls: list[str] = []
    if provider == "ashby":
        url = ashby.request_url(board_id)
        urls.append(url)
        payload = fetcher(url, timeout)
        return (
            ashby.parse_payload(
                payload,
                company_id=company_id,
                source_id=source_id,
                board_id=board_id,
                enumeration_complete=True,
            ),
            urls,
        )
    if provider == "greenhouse":
        url = greenhouse.request_url(board_id)
        urls.append(url)
        payload = fetcher(url, timeout)
        return (
            greenhouse.parse_payload(
                payload,
                company_id=company_id,
                source_id=source_id,
                board_id=board_id,
                enumeration_complete=True,
            ),
            urls,
        )

    eu = "eu.lever.co" in str(source.get("listings_url") or "").lower()
    pages: list[Any] = []
    complete = False
    for page_index in range(lever_max_pages):
        skip = page_index * lever_page_size
        url = lever.request_url(board_id, skip=skip, limit=lever_page_size, eu=eu)
        urls.append(url)
        page = fetcher(url, timeout)
        pages.append(page)
        values = page.get("postings") if isinstance(page, Mapping) else page
        if not isinstance(values, list):
            raise FetchError(f"Lever page {page_index + 1} is not a postings array", "source_access")
        if len(values) < lever_page_size:
            complete = True
            break
    return (
        lever.parse_pages(
            pages,
            company_id=company_id,
            source_id=source_id,
            board_id=board_id,
            enumeration_complete=complete,
            expected_pages=len(pages),
            eu=eu,
        ),
        urls,
    )


def _result_path(paths: RepoPaths, attempt_id: str) -> Path:
    safe = "".join(character if character.isalnum() or character in "-_." else "_" for character in attempt_id)
    return paths.result_dir("find_jobs") / f"{safe}.json"


def _write_result(path: Path, document: dict[str, Any]) -> bool:
    store = JsonStore(path, validator=validator("job-result.schema.json", str(path)))
    if not path.exists():
        return store.initialize(document).changed
    existing = store.read()
    if existing != document:
        raise FetchError(
            f"Refusing to overwrite an existing result draft that may contain agent work: {path}",
            "other",
        )
    return False


def prepare_job_result(
    paths: RepoPaths,
    task: Mapping[str, Any],
    *,
    fetcher: JsonFetcher = fetch_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch a claimed structured source and write an agent-reviewable result draft."""

    if task.get("workflow") != "find_jobs" or task.get("status") != "running":
        raise FetchError("prepare_job_result requires a running find_jobs task", "other")
    claim = task.get("claim")
    if not isinstance(claim, Mapping):
        raise FetchError("Claim metadata is missing", "other")
    context = task.get("context", {})
    source = context.get("source_snapshot")
    company = context.get("company_snapshot")
    profile = context.get("profile")
    if not all(isinstance(item, Mapping) for item in (source, company, profile)):
        raise FetchError("Claim context lacks company, source, or profile snapshot", "other")
    timestamp = format_timestamp(now or datetime.now(timezone.utc))
    target = copy.deepcopy(task["target"])
    base: dict[str, Any] = {
        "schema_version": 1,
        "workflow": "find_jobs",
        "task_id": task["task_id"],
        "attempt_id": claim["attempt_id"],
        "claim_token": claim["claim_token"],
        "model": task["expected_model"],
        "created_at": timestamp,
        "target": target,
        "evidence": [],
        "item_results": [],
        "notes": None,
    }
    try:
        adapter_result, urls = fetch_structured_source(
            source,
            company_id=company["company_id"],
            fetcher=fetcher,
        )
    except FetchError as exc:
        result = {
            **base,
            "outcome": "blocked",
            "reason_code": "structured_source_access_failed",
            "snapshot": None,
            "failure_class": exc.failure_class,
            "item_results": [
                {
                    "item": f"source {source.get('source_id')}",
                    "status": "blocked",
                    "summary": str(exc),
                }
            ],
            "notes": "Finalize this draft to schedule a same-model retry or block after the retry limit.",
        }
        path = _result_path(paths, claim["attempt_id"])
        changed = _write_result(path, result)
        return {"result_path": str(path), "created": changed, "result": result}
    except (AdapterError, KeyError, TypeError, ValueError) as exc:
        result = {
            **base,
            "outcome": "failed",
            "reason_code": "structured_source_invalid_output",
            "snapshot": None,
            "failure_class": "invalid_output",
            "item_results": [
                {
                    "item": f"source {source.get('source_id')}",
                    "status": "failed",
                    "summary": f"Structured response failed deterministic validation: {exc}",
                }
            ],
            "notes": "Finalize this draft as failed; invalid provider output does not escalate models.",
        }
        path = _result_path(paths, claim["attempt_id"])
        changed = _write_result(path, result)
        return {"result_path": str(path), "created": changed, "result": result}

    if not adapter_result.complete:
        result = {
            **base,
            "outcome": "blocked",
            "reason_code": "structured_enumeration_incomplete",
            "evidence": [
                {
                    "url": url,
                    "retrieved_at": timestamp,
                    "evidence_type": "official_structured_job_feed",
                    "finding": "Fetched, but completeness could not be proven.",
                }
                for url in urls
            ],
            "snapshot": None,
            "failure_class": "source_access",
            "item_results": [
                {
                    "item": f"source {source.get('source_id')}",
                    "status": "blocked",
                    "summary": (
                        "Structured enumeration was incomplete: "
                        f"{adapter_result.fetch.completeness_reason}."
                    ),
                }
            ],
            "notes": "Finalize this draft to schedule a same-model retry; no prior jobs will be closed.",
        }
        path = _result_path(paths, claim["attempt_id"])
        changed = _write_result(path, result)
        return {"result_path": str(path), "created": changed, "result": result}

    evaluation = evaluate_jobs(adapter_result.jobs, profile)
    candidates = semantic_candidates(adapter_result.jobs, evaluation)
    deterministic_pass_ids = [
        decision["stable_id"]
        for decision in evaluation["decisions"]
        if decision["status"] == "deterministic_pass"
    ]
    unresolved_ids = {candidate["stable_id"] for candidate in candidates}
    outcome = "inconclusive" if unresolved_ids else "resolved"
    result = {
        **base,
        "outcome": outcome,
        "reason_code": (
            "semantic_review_required" if outcome == "inconclusive" else "structured_scan_complete"
        ),
        "evidence": [
            {
                "url": url,
                "retrieved_at": timestamp,
                "evidence_type": "official_structured_job_feed",
                "finding": "Captured as part of the complete structured enumeration.",
            }
            for url in urls
        ],
        "snapshot": {
            "source_id": source["source_id"],
            "company_id": company["company_id"],
            "expected_source_revision": task["target_revision"],
            "profile_id": profile["profile_id"],
            "profile_digest": profile_digest(profile),
            "scanned_at": timestamp,
            "adapter_result": adapter_result.to_dict(),
            "qualifying_jobs": deterministic_pass_ids,
            "unresolved_candidates": candidates,
            "rejection_counts": rejection_counts_from_evaluation(evaluation),
            "warnings": [],
        },
        "item_results": [
            {
                "item": f"source {source['source_id']}",
                "status": "inconclusive" if candidates else "resolved",
                "summary": (
                    f"Fetched {len(adapter_result.jobs)} current jobs; "
                    f"{evaluation['counts']['deterministically_rejected']} deterministically rejected; "
                    f"{len(candidates)} require semantic review."
                ),
            }
        ],
        "failure_class": None,
        "notes": (
            "Review every unresolved candidate against role fit and job-specific AI involvement. "
            "Move supported candidates to qualifying_jobs, remove reviewed candidates from "
            "unresolved_candidates, and set outcome/reason_code before finalizing."
            if candidates
            else "No semantic candidates remained after deterministic filtering."
        ),
    }
    path = _result_path(paths, claim["attempt_id"])
    changed = _write_result(path, result)
    return {
        "result_path": str(path),
        "created": changed,
        "counts": evaluation["counts"],
        "semantic_candidates": len(candidates),
        "result": result,
    }
