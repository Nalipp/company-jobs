"""Read-only deterministic audits that recommend and enqueue exact work."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .companies import normalize_domain
from .queue import QueueEngine, format_timestamp, parse_timestamp


COMPANY_POLICY_VERSION = "company_validation_v1"
JOB_POLICY_VERSION = "job_search_v1"
DEFAULT_STALE_AFTER_HOURS = 24


def profile_digest(profile: Mapping[str, Any]) -> str:
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def job_cycle_policy_version(
    snapshot: Mapping[str, Any] | None,
    expected_profile_digest: str,
) -> str:
    """Return stable work identity for one completed-snapshot generation.

    The initial missing-snapshot audit retains the original policy string so
    the already-seeded V4 queue stays idempotent. Later recurring scans include
    the current snapshot revision and profile digest; after a successful scan
    increments that revision, the next stale scan is new work rather than a
    duplicate of an older completed task.
    """

    if snapshot is None:
        return JOB_POLICY_VERSION
    revision = snapshot.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("job snapshot revision must be a positive integer")
    return (
        f"{JOB_POLICY_VERSION}:profile_{expected_profile_digest[:16]}:"
        f"snapshot_{revision}"
    )


def _research_by_company(archive: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not archive:
        return result
    for record in archive.get("records", []):
        company_id = record.get("issue", {}).get("company_id")
        if isinstance(company_id, str):
            result[company_id].append(record)
    return result


def build_company_tasks(
    canonical: Mapping[str, Any],
    engine: QueueEngine,
    *,
    research_archive: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    instant = now or datetime.now(timezone.utc)
    research = _research_by_company(research_archive)
    domain_groups: dict[str, list[str]] = defaultdict(list)
    for company in canonical["companies"]:
        domain = normalize_domain(company.get("website_url"))
        if domain:
            domain_groups[domain].append(company["company_id"])

    tasks: list[dict[str, Any]] = []
    for company in sorted(canonical["companies"], key=lambda item: item["company_id"]):
        reasons: list[str] = []
        priority = 50
        if company["address_status"] == "pending":
            reasons.append("pending_validation")
            priority = max(priority, 100)
        if company["address_status"] == "verified" and not company.get("website_url"):
            reasons.append("verified_missing_website")
            priority = max(priority, 95)
        sources = company["career_sources"]
        if company["address_status"] == "verified" and not sources:
            reasons.append("missing_career_source")
            priority = max(priority, 85)
        for status in sorted({source["status"] for source in sources if source["status"] != "ready"}):
            reasons.append(f"career_source_{status}")
            priority = max(priority, 80 if status in {"blocked", "stale"} else 70)
        domain = normalize_domain(company.get("website_url"))
        duplicate_ids = domain_groups.get(domain, []) if domain else []
        if len(duplicate_ids) > 1:
            reasons.append("duplicate_website_domain")
            priority = max(priority, 90)
        if not reasons:
            continue
        relevant = research.get(company["company_id"], [])
        context = {
            "company_snapshot": copy.deepcopy(company),
            "source_snapshots": copy.deepcopy(sources),
            "relevant_evidence": copy.deepcopy(relevant[-20:]),
            "relevant_evidence_total": len(relevant),
            "duplicate_domain_company_ids": duplicate_ids,
        }
        tasks.append(
            engine.build_task(
                company_id=company["company_id"],
                source_ids=[source["source_id"] for source in sources],
                reason_codes=sorted(set(reasons)),
                policy_version=COMPANY_POLICY_VERSION,
                target_revision=company["record_revision"],
                context=context,
                expected_model="luna",
                priority=priority,
                created_at=instant,
            )
        )
    return tasks


def _snapshot_reason_codes(
    snapshot: Mapping[str, Any] | None,
    *,
    expected_profile_digest: str,
    now: datetime,
    stale_after: timedelta,
) -> list[str]:
    if snapshot is None:
        return ["missing_snapshot"]
    reasons: list[str] = []
    if snapshot.get("profile_digest") != expected_profile_digest:
        reasons.append("profile_changed")
    if not snapshot.get("fetch_complete", False):
        reasons.append("incomplete_snapshot")
    if snapshot.get("unresolved_candidates"):
        reasons.append("semantic_candidates_pending")
    last_success = snapshot.get("last_successful_scan_at")
    if not last_success:
        reasons.append("missing_successful_scan")
    elif parse_timestamp(last_success) + stale_after <= now:
        reasons.append("stale_snapshot")
    return sorted(set(reasons))


def build_job_tasks(
    canonical: Mapping[str, Any],
    engine: QueueEngine,
    profile: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
    stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS,
) -> list[dict[str, Any]]:
    instant = now or datetime.now(timezone.utc)
    digest = profile_digest(profile)
    tasks: list[dict[str, Any]] = []
    for company in sorted(canonical["companies"], key=lambda item: item["company_id"]):
        for source in sorted(company["career_sources"], key=lambda item: item["source_id"]):
            if source["status"] != "ready":
                continue
            snapshot = snapshots.get(source["source_id"])
            reasons = _snapshot_reason_codes(
                snapshot,
                expected_profile_digest=digest,
                now=instant,
                stale_after=timedelta(hours=stale_after_hours),
            )
            if not reasons:
                continue
            structured = source["provider"] in {"ashby", "greenhouse", "lever"} and bool(
                source["capabilities"].get("structured_data")
            )
            priority = 90 if structured else 60
            context = {
                "company_snapshot": {
                    key: copy.deepcopy(company[key])
                    for key in ("company_id", "company_name", "website_url", "address_status")
                },
                "source_snapshot": copy.deepcopy(source),
                "previous_job_snapshot": copy.deepcopy(snapshot),
                "profile": copy.deepcopy(profile),
                "profile_digest": digest,
                "structured_adapter_available": structured,
            }
            tasks.append(
                engine.build_task(
                    company_id=company["company_id"],
                    source_id=source["source_id"],
                    reason_codes=reasons,
                    policy_version=job_cycle_policy_version(snapshot, digest),
                    target_revision=source["record_revision"],
                    context=context,
                    expected_model="luna",
                    priority=priority,
                    created_at=instant,
                )
            )
    return tasks


def audit_and_enqueue_companies(
    canonical: Mapping[str, Any],
    engine: QueueEngine,
    *,
    research_archive: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    tasks = build_company_tasks(
        canonical,
        engine,
        research_archive=research_archive,
        now=now,
    )
    result = engine.enqueue_many(tasks, now=now)
    return {
        "recommended": len(tasks),
        "created": len(result.created),
        "deduplicated": len(result.existing),
        "created_task_ids": [task["task_id"] for task in result.created],
        "queue_revision": result.revision,
        "changed": result.changed,
    }


def audit_and_enqueue_jobs(
    canonical: Mapping[str, Any],
    engine: QueueEngine,
    profile: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
    stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS,
) -> dict[str, Any]:
    tasks = build_job_tasks(
        canonical,
        engine,
        profile,
        snapshots,
        now=now,
        stale_after_hours=stale_after_hours,
    )
    result = engine.enqueue_many(tasks, now=now)
    return {
        "recommended": len(tasks),
        "created": len(result.created),
        "deduplicated": len(result.existing),
        "created_task_ids": [task["task_id"] for task in result.created],
        "queue_revision": result.revision,
        "changed": result.changed,
    }
