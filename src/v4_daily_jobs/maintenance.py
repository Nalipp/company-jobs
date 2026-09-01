"""Small, explicit repairs for canonical data created by older V4 code."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .json_store import JsonStore, canonical_json_bytes, sha256_bytes
from .paths import RepoPaths
from .queue import QueueEngine, format_timestamp
from .schemas import validate, validator


QUALIFICATION_REPAIR_VERSION = "job_qualification_status_v1"
ESCALATION_REPAIR_VERSION = "unreviewed_job_escalation_v2"
LANE_REPAIR_VERSION = "unsupported_provider_retry_v1"
SOURCE_REVALIDATION_POLICY = "job_source_revalidation_v1"
def _invalid_job_escalation(result: dict[str, Any]) -> str | None:
    """Return why an inconclusive result was not actually fully reviewed."""

    if result.get("outcome") != "inconclusive":
        return None
    reason = result.get("reason_code")
    if reason != "semantic_evidence_inconclusive":
        return str(reason) if isinstance(reason, str) else "non_evidence_inconclusive"
    snapshot = result.get("snapshot")
    unresolved = snapshot.get("unresolved_candidates") if isinstance(snapshot, dict) else None
    if not isinstance(unresolved, list) or not unresolved:
        return "missing_unresolved_candidates"
    if any(
        not isinstance(candidate, dict)
        or not isinstance(candidate.get("review_notes"), str)
        or not candidate["review_notes"].strip()
        for candidate in unresolved
    ):
        return "unreviewed_candidates"
    return None


def repair_invalid_job_escalations(
    paths: RepoPaths,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reopen prematurely escalated Luna work and retire unused Terra children.

    The repair is intentionally conservative: it changes a pair only when the
    parent result proves the semantic review was incomplete and the generated
    Terra child is still open and has never been claimed. Repeated runs are
    exact no-ops.
    """

    timestamp = format_timestamp(now or datetime.now(timezone.utc))
    engine = QueueEngine(
        paths.job_queue,
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )
    reopened: list[str] = []
    superseded: list[str] = []
    skipped: list[str] = []

    # This is a versioned maintenance migration, not a normal state-machine
    # transition. The ordinary queue lock, full validator, revision check, and
    # atomic replacement still protect the mutation.
    with engine.store.locked(exclusive=True) as locked:
        document = locked.read()
        original_revision = document["revision"]
        tasks = {task["task_id"]: task for task in document["tasks"]}
        for parent in document["tasks"]:
            if parent["status"] != "inconclusive" or parent["expected_model"] != "luna":
                continue
            resolution = parent.get("resolution")
            if not isinstance(resolution, dict):
                continue
            result_file = resolution.get("result_file")
            child_id = resolution.get("follow_up_task_id")
            if not isinstance(result_file, str) or not isinstance(child_id, str):
                continue
            result_path = Path(result_file)
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                skipped.append(f"{parent['task_id']}: unreadable result ({exc})")
                continue
            if not isinstance(result, dict):
                skipped.append(f"{parent['task_id']}: result is not an object")
                continue
            invalid_reason = _invalid_job_escalation(result)
            if invalid_reason is None:
                continue
            child = tasks.get(child_id)
            if not isinstance(child, dict):
                skipped.append(f"{parent['task_id']}: missing follow-up {child_id}")
                continue
            if (
                child.get("parent_task_id") != parent["task_id"]
                or child.get("expected_model") != "terra"
                or child.get("status") != "open"
                or child.get("attempts") != []
            ):
                skipped.append(
                    f"{parent['task_id']}: follow-up {child_id} is no longer unused/open"
                )
                continue

            parent.setdefault("context", {}).setdefault("maintenance_history", []).append(
                {
                    "repair_version": ESCALATION_REPAIR_VERSION,
                    "repaired_at": timestamp,
                    "invalid_reason": invalid_reason,
                    "prior_resolution": copy.deepcopy(resolution),
                }
            )
            parent["status"] = "open"
            parent["available_at"] = timestamp
            parent["updated_at"] = timestamp
            parent["claim"] = None
            parent["resolution"] = None

            child["status"] = "superseded"
            child["updated_at"] = timestamp
            child["claim"] = None
            child["resolution"] = {
                "outcome": "superseded",
                "reason_code": "invalid_unreviewed_escalation",
                "superseded_at": timestamp,
                "superseded_by_task_id": parent["task_id"],
                "maintenance_version": ESCALATION_REPAIR_VERSION,
            }
            reopened.append(parent["task_id"])
            superseded.append(child_id)

        if reopened:
            document["revision"] = original_revision + 1
            document["updated_at"] = timestamp
            locked.save(document, expected_revision=original_revision)

    final_revision = original_revision + (1 if reopened else 0)
    return {
        "repair_version": ESCALATION_REPAIR_VERSION,
        "reopened_task_ids": reopened,
        "superseded_task_ids": superseded,
        "skipped_reasons": skipped,
        "reopened_count": len(reopened),
        "superseded_count": len(superseded),
        "skipped_count": len(skipped),
        "queue_revision": final_revision,
    }


def repair_job_qualification_statuses(
    paths: RepoPaths,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark legacy canonical job records as qualifying under the jobs lock.

    A canonical snapshot's ``jobs`` array contains only jobs selected through
    ``qualifying_jobs``.  Older finalizers allowed the redundant
    ``qualification_status`` annotation to be absent.  This repair makes that
    implicit invariant explicit, validates the complete proposed snapshot,
    and saves atomically.  Repeated runs are exact no-ops.
    """

    timestamp = format_timestamp(now or datetime.now(timezone.utc))
    changed_sources: list[str] = []
    changed_jobs: list[dict[str, str]] = []
    paths.job_sources.mkdir(parents=True, exist_ok=True)

    for path in sorted(paths.job_sources.glob("*.json")):
        # Read legacy snapshots without the new schema first. The normalized
        # proposal must pass the current schema before it can be written.
        store = JsonStore(path, lock_path=paths.jobs_lock)
        with store.locked(exclusive=True) as locked:
            document = locked.read()
            proposed = copy.deepcopy(document)
            source_changes: list[dict[str, str]] = []
            jobs = proposed.get("jobs")
            if not isinstance(jobs, list):
                validate(proposed, "job-snapshot.schema.json", str(path))
                continue
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                if job.get("qualification_status") is None:
                    job["qualification_status"] = "qualifying"
                    source_changes.append(
                        {
                            "source_id": str(proposed.get("source_id", "")),
                            "stable_id": str(job.get("stable_id", "")),
                            "title": str(job.get("title", "")),
                        }
                    )

            if not source_changes:
                validate(proposed, "job-snapshot.schema.json", str(path))
                continue

            revision = proposed.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool):
                validate(proposed, "job-snapshot.schema.json", str(path))
                raise AssertionError("unreachable")
            proposed["revision"] = revision + 1
            repair_identity = {
                "repair_version": QUALIFICATION_REPAIR_VERSION,
                "source_id": proposed.get("source_id"),
                "stable_ids": sorted(item["stable_id"] for item in source_changes),
            }
            proposed.setdefault("application_ledger", []).append(
                {
                    "transaction_id": (
                        f"maintenance:{QUALIFICATION_REPAIR_VERSION}:"
                        f"{proposed.get('source_id')}"
                    ),
                    "task_id": f"maintenance:{QUALIFICATION_REPAIR_VERSION}",
                    "attempt_id": f"maintenance:{QUALIFICATION_REPAIR_VERSION}",
                    "result_digest": sha256_bytes(canonical_json_bytes(repair_identity)),
                    "applied_at": timestamp,
                }
            )
            validate(proposed, "job-snapshot.schema.json", str(path))
            locked.save(proposed, expected_revision=revision)
            changed_sources.append(str(proposed["source_id"]))
            changed_jobs.extend(source_changes)

    return {
        "repair_version": QUALIFICATION_REPAIR_VERSION,
        "changed_sources": changed_sources,
        "changed_jobs": changed_jobs,
        "source_count": len(changed_sources),
        "job_count": len(changed_jobs),
    }


def repair_unsupported_provider_retries(
    paths: RepoPaths,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove invalid access-failure charges caused by the structured helper.

    Older workers could send manual-lane tasks through ``prepare-result``.
    Those attempts are retained for audit but no longer count against the
    source's genuine access retry allowance. Blocked tasks are reopened.
    """

    timestamp = format_timestamp(now or datetime.now(timezone.utc))
    engine = QueueEngine(
        paths.job_queue,
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )
    repaired_tasks: list[str] = []
    repaired_attempts = 0
    reopened: list[str] = []
    with engine.store.locked(exclusive=True) as locked:
        document = locked.read()
        original_revision = document["revision"]
        for task in document["tasks"]:
            if task.get("context", {}).get("structured_adapter_available") is not False:
                continue
            changed_attempts = []
            for attempt in task["attempts"]:
                if attempt.get("outcome") != "access_failure":
                    continue
                resolution = attempt.get("resolution")
                items = resolution.get("item_results", []) if isinstance(resolution, dict) else []
                if not any(
                    "no deterministic V4 adapter" in str(item.get("summary", ""))
                    for item in items
                    if isinstance(item, dict)
                ):
                    continue
                prior = copy.deepcopy(resolution)
                attempt["status"] = "failed"
                attempt["outcome"] = "invalid_lane_attempt"
                attempt["error_class"] = "other"
                attempt["resolution"] = {
                    "outcome": "invalid_lane_attempt",
                    "reason_code": "manual_lane_required",
                    "repaired_at": timestamp,
                    "repair_version": LANE_REPAIR_VERSION,
                    "prior_resolution": prior,
                }
                changed_attempts.append(attempt["attempt_id"])
            if not changed_attempts:
                continue
            task.setdefault("context", {}).setdefault("maintenance_history", []).append(
                {
                    "repair_version": LANE_REPAIR_VERSION,
                    "repaired_at": timestamp,
                    "attempt_ids": changed_attempts,
                }
            )
            if task["status"] == "blocked":
                task["status"] = "open"
                task["claim"] = None
                task["resolution"] = None
                reopened.append(task["task_id"])
            if task["status"] == "open":
                task["available_at"] = timestamp
            task["updated_at"] = timestamp
            repaired_tasks.append(task["task_id"])
            repaired_attempts += len(changed_attempts)

        if repaired_tasks:
            document["revision"] = original_revision + 1
            document["updated_at"] = timestamp
            engine.validate_document(document)
            locked.save(document, expected_revision=original_revision)

    return {
        "repair_version": LANE_REPAIR_VERSION,
        "repaired_task_ids": repaired_tasks,
        "reopened_task_ids": reopened,
        "task_count": len(repaired_tasks),
        "attempt_count": repaired_attempts,
        "reopened_count": len(reopened),
        "queue_revision": original_revision + (1 if repaired_tasks else 0),
    }


def enqueue_job_source_revalidations(
    paths: RepoPaths,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Route permanent structured-feed failures into company/source validation."""

    timestamp = format_timestamp(now or datetime.now(timezone.utc))
    canonical = JsonStore(
        paths.companies,
        validator=validator("companies.schema.json"),
    ).read()
    companies = {item["company_id"]: item for item in canonical["companies"]}
    job_document = JsonStore(
        paths.job_queue,
        validator=validator("job-queue.schema.json"),
    ).read()
    requests_by_company: dict[str, list[dict[str, Any]]] = {}
    permanent_reasons = {
        "manual_source_enumeration_unsupported",
        "structured_source_not_found",
        "structured_source_configuration_invalid",
    }
    for task in job_document["tasks"]:
        resolution = task.get("resolution")
        if task.get("status") != "blocked" or not isinstance(resolution, dict):
            continue
        item_results = resolution.get("item_results", [])
        legacy_not_found = any(
            ("HTTP 404" in str(item.get("summary", "")) or "HTTP 410" in str(item.get("summary", "")))
            for item in item_results
            if isinstance(item, dict)
        )
        if resolution.get("reason_code") not in permanent_reasons and not legacy_not_found:
            continue
        company_id = task["target"]["company_ids"][0]
        requests_by_company.setdefault(company_id, []).append(
            {
                "job_task_id": task["task_id"],
                "source_id": task["target"]["source_ids"][0],
                "reason_code": (
                    "structured_source_not_found"
                    if legacy_not_found
                    else resolution["reason_code"]
                ),
                "item_results": copy.deepcopy(item_results),
            }
        )

    engine = QueueEngine(
        paths.company_queue,
        "validate_companies",
        validator=validator("company-queue.schema.json"),
    )
    created: list[str] = []
    augmented: list[str] = []
    with engine.store.locked(exclusive=True) as locked:
        document = locked.read()
        original_revision = document["revision"]
        for company_id, requests in sorted(requests_by_company.items()):
            company = companies.get(company_id)
            if company is None:
                continue
            already_routed = {
                request.get("job_task_id")
                for task in document["tasks"]
                for request in task.get("context", {}).get(
                    "job_source_revalidation_requests", []
                )
                if task["target"]["company_ids"] == [company_id]
                and isinstance(request, dict)
            }
            pending = [
                request
                for request in requests
                if request["job_task_id"] not in already_routed
            ]
            if not pending:
                continue
            source_ids = sorted({request["source_id"] for request in pending})
            existing = next(
                (
                    task
                    for task in document["tasks"]
                    if task["target"]["company_ids"] == [company_id]
                    and task["status"] == "open"
                    and set(source_ids).issubset(task["target"]["source_ids"])
                ),
                None,
            )
            if existing is not None:
                existing["context"].setdefault(
                    "job_source_revalidation_requests", []
                ).extend(copy.deepcopy(pending))
                existing["priority"] = max(existing["priority"], 110)
                existing["updated_at"] = timestamp
                augmented.append(existing["task_id"])
                continue
            task = engine.build_task(
                company_id=company_id,
                source_ids=source_ids,
                reason_codes=["job_source_revalidation"],
                policy_version=SOURCE_REVALIDATION_POLICY,
                target_revision=company["record_revision"],
                context={
                    "company_snapshot": copy.deepcopy(company),
                    "source_snapshots": copy.deepcopy(company["career_sources"]),
                    "relevant_evidence": [],
                    "relevant_evidence_total": 0,
                    "duplicate_domain_company_ids": [],
                    "job_source_revalidation_requests": copy.deepcopy(pending),
                },
                expected_model="luna",
                priority=110,
                created_at=timestamp,
            )
            document["tasks"].append(task)
            created.append(task["task_id"])

        if created or augmented:
            document["tasks"].sort(
                key=lambda task: (task["created_at"], task["task_id"])
            )
            document["revision"] = original_revision + 1
            document["updated_at"] = timestamp
            engine.validate_document(document)
            locked.save(document, expected_revision=original_revision)

    return {
        "policy_version": SOURCE_REVALIDATION_POLICY,
        "created_task_ids": created,
        "augmented_task_ids": augmented,
        "created_count": len(created),
        "augmented_count": len(augmented),
        "request_count": sum(len(value) for value in requests_by_company.values()),
        "queue_revision": original_revision + (1 if created or augmented else 0),
    }


__all__ = [
    "ESCALATION_REPAIR_VERSION",
    "INVALID_ESCALATION_REASONS",
    "LANE_REPAIR_VERSION",
    "QUALIFICATION_REPAIR_VERSION",
    "SOURCE_REVALIDATION_POLICY",
    "enqueue_job_source_revalidations",
    "repair_invalid_job_escalations",
    "repair_unsupported_provider_retries",
    "repair_job_qualification_statuses",
]
