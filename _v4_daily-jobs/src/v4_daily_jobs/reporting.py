"""Deterministic operation reports and read-only full-system status."""

from __future__ import annotations

import copy
import json
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .companies import integrity_report
from .json_store import FileLock, JsonStore
from .paths import RepoPaths
from .queue import QueueEngine, STATUSES, format_timestamp, parse_timestamp
from .schemas import validate, validator


def utc_now_string() -> str:
    return format_timestamp(datetime.now(timezone.utc))


def operation_report(
    operation: str,
    *,
    execution_status: str = "completed",
    task: Mapping[str, Any] | None = None,
    item_results: list[dict[str, Any]] | None = None,
    changes: list[str] | None = None,
    queue_delta: Mapping[str, Any] | None = None,
    progress: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
    message: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "execution_status": execution_status,
        "operation": operation,
        "generated_at": generated_at or utc_now_string(),
        "task": copy.deepcopy(dict(task)) if task is not None else None,
        "item_results": copy.deepcopy(item_results or []),
        "changes": list(changes or []),
        "queue_delta": copy.deepcopy(dict(queue_delta or {})),
        "progress": copy.deepcopy(dict(progress or {})),
        "warnings": list(warnings or []),
        "message": message,
    }
    validate(report, "operation-report.schema.json", "Operation report")
    return report


def queue_counts(document: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    instant = now or datetime.now(timezone.utc)
    raw_by_status = Counter(task["status"] for task in document["tasks"])
    by_status = {status: raw_by_status.get(status, 0) for status in STATUSES}
    by_model: dict[str, dict[str, int]] = {}
    availability_by_model: dict[str, dict[str, int]] = {}
    for model in ("luna", "terra", "sol"):
        counts = Counter(
            task["status"] for task in document["tasks"] if task["expected_model"] == model
        )
        by_model[model] = {status: counts.get(status, 0) for status in STATUSES}
        model_open = [
            task
            for task in document["tasks"]
            if task["expected_model"] == model and task["status"] == "open"
        ]
        availability_by_model[model] = {
            "due": sum(
                parse_timestamp(task["available_at"]) <= instant for task in model_open
            ),
            "delayed": sum(
                parse_timestamp(task["available_at"]) > instant for task in model_open
            ),
        }
    active_running = 0
    expired_running = 0
    due_open = 0
    delayed_open = 0
    for task in document["tasks"]:
        if task["status"] == "running":
            expires = parse_timestamp(task["claim"]["lease_expires_at"])
            if expires <= instant:
                expired_running += 1
            else:
                active_running += 1
        elif task["status"] == "open":
            if parse_timestamp(task["available_at"]) <= instant:
                due_open += 1
            else:
                delayed_open += 1
    return {
        "total": len(document["tasks"]),
        "by_status": by_status,
        "by_model": by_model,
        "availability_by_model": availability_by_model,
        "running": {"active": active_running, "expired": expired_running},
        "open": {"due": due_open, "delayed": delayed_open},
        "revision": document["revision"],
    }


def _read_job_snapshots(paths: RepoPaths) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    if not paths.job_sources.exists():
        return snapshots
    for path in sorted(paths.job_sources.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        validate(document, "job-snapshot.schema.json", str(path))
        snapshots[document["source_id"]] = document
    return snapshots


def status_snapshot(paths: RepoPaths, *, now: datetime | None = None) -> dict[str, Any]:
    """Read a coherent local snapshot without changing any data file."""

    instant = now or datetime.now(timezone.utc)
    company_engine = QueueEngine(
        paths.company_queue,
        "validate_companies",
        validator=validator("company-queue.schema.json"),
    )
    job_engine = QueueEngine(
        paths.job_queue,
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )
    canonical_store = JsonStore(paths.companies, validator=validator("companies.schema.json"))
    jobs_guard = JsonStore(
        paths.root / "data" / "jobs" / ".snapshot-guard.json",
        lock_path=paths.jobs_lock,
    )
    # Acquire shared locks in the same global order used everywhere:
    # company queue -> job queue -> company DB -> jobs DB.
    with ExitStack() as stack:
        company_locked = stack.enter_context(company_engine.store.locked(exclusive=False))
        job_locked = stack.enter_context(job_engine.store.locked(exclusive=False))
        canonical_locked = stack.enter_context(canonical_store.locked(exclusive=False))
        stack.enter_context(jobs_guard.locked(exclusive=False))
        company_queue = company_locked.read()
        job_queue = job_locked.read()
        canonical = canonical_locked.read()
        snapshots = _read_job_snapshots(paths)

    company_statuses = Counter(company["address_status"] for company in canonical["companies"])
    sources = [source for company in canonical["companies"] for source in company["career_sources"]]
    source_statuses = Counter(source["status"] for source in sources)
    ready_providers = Counter(source["provider"] for source in sources if source["status"] == "ready")
    deterministic_adapters = Counter(
        source["provider"]
        for source in sources
        if source["status"] == "ready"
        and source["provider"] in {"ashby", "greenhouse", "lever"}
        and source["capabilities"].get("structured_data")
    )
    profile = json.loads(paths.profile.read_text(encoding="utf-8"))
    validate(profile, "search-profile.schema.json", str(paths.profile))
    from .audit import profile_digest

    current_digest = profile_digest(profile)
    ready_source_ids = {source["source_id"] for source in sources if source["status"] == "ready"}
    coverage = Counter()
    qualifying = 0
    unresolved = 0
    for source_id in ready_source_ids:
        snapshot = snapshots.get(source_id)
        if snapshot is None:
            coverage["missing"] += 1
            continue
        if not snapshot["fetch_complete"]:
            coverage["incomplete"] += 1
        elif snapshot["profile_digest"] != current_digest:
            coverage["stale"] += 1
        elif snapshot["last_successful_scan_at"] and parse_timestamp(
            snapshot["last_successful_scan_at"]
        ) + timedelta(hours=24) <= instant:
            coverage["stale"] += 1
        else:
            coverage["current"] += 1
        # The canonical jobs array is the qualifying set; schema validation
        # guarantees every record also carries its qualification provenance.
        qualifying += sum(job["status"] == "active" for job in snapshot["jobs"])
        unresolved += len(snapshot["unresolved_candidates"])

    recent = {"24h": Counter(), "7d": Counter()}
    for document in (company_queue, job_queue):
        for task in document["tasks"]:
            for attempt in task["attempts"]:
                ended = attempt.get("ended_at")
                if not ended:
                    continue
                ended_at = parse_timestamp(ended)
                label = attempt.get("outcome") or attempt.get("status") or "unknown"
                if ended_at >= instant - timedelta(hours=24):
                    recent["24h"][label] += 1
                if ended_at >= instant - timedelta(days=7):
                    recent["7d"][label] += 1

    company_queue_counts = queue_counts(company_queue, now=instant)
    job_queue_counts = queue_counts(job_queue, now=instant)
    human_review = sum(
        bool(task.get("resolution", {}).get("human_review_required"))
        for document in (company_queue, job_queue)
        for task in document["tasks"]
        if isinstance(task.get("resolution"), dict)
    )
    canonical_integrity = integrity_report(canonical)
    data_warning_count = (
        len(canonical_integrity["duplicate_company_ids"])
        + len(canonical_integrity["duplicate_source_ids"])
        + len(canonical_integrity["orphan_source_ids"])
        + len(canonical_integrity["duplicate_website_domains"])
    )
    recent_core = ("completed", "inconclusive", "blocked", "failed")
    normalized_recent = {
        window: {
            **{label: counts.get(label, 0) for label in recent_core},
            **{
                label: count
                for label, count in sorted(counts.items())
                if label not in recent_core
            },
        }
        for window, counts in recent.items()
    }
    report = {
        "schema_version": 1,
        "generated_at": format_timestamp(instant),
        "revisions": {
            "companies": canonical["revision"],
            "company_queue": company_queue["revision"],
            "job_queue": job_queue["revision"],
            "profile_digest": current_digest,
        },
        "queues": {
            "validate_companies": company_queue_counts,
            "find_jobs": job_queue_counts,
        },
        "companies": {
            "total": len(canonical["companies"]),
            "by_address_status": dict(sorted(company_statuses.items())),
        },
        "career_sources": {
            "total": len(sources),
            "by_status": dict(sorted(source_statuses.items())),
            "ready_by_provider": dict(sorted(ready_providers.items())),
            "deterministic_adapter_ready": dict(sorted(deterministic_adapters.items())),
            "agent_enumeration_ready": source_statuses.get("ready", 0)
            - sum(deterministic_adapters.values()),
        },
        "job_coverage": {
            "current": coverage.get("current", 0),
            "stale": coverage.get("stale", 0),
            "incomplete": coverage.get("incomplete", 0),
            "missing": coverage.get("missing", 0),
            "snapshot_files": len(snapshots),
            "active_qualifying_jobs": qualifying,
            "unresolved_candidates": unresolved,
        },
        "attention": {
            "expired_leases": company_queue_counts["running"]["expired"]
            + job_queue_counts["running"]["expired"],
            "blocked_tasks": company_queue_counts["by_status"].get("blocked", 0)
            + job_queue_counts["by_status"].get("blocked", 0),
            "failed_tasks": company_queue_counts["by_status"].get("failed", 0)
            + job_queue_counts["by_status"].get("failed", 0),
            "human_review": human_review,
            "data_warnings": data_warning_count,
            "integrity": canonical_integrity,
        },
        "recent_activity": normalized_recent,
    }
    return report


def next_commands_snapshot(
    paths: RepoPaths,
    *,
    company_workers: int = 10,
    job_workers: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan operator launches from one coherent, read-only status snapshot."""

    if not 1 <= company_workers <= 50:
        raise ValueError("company workers must be between 1 and 50")
    if not 1 <= job_workers <= 50:
        raise ValueError("job workers must be between 1 and 50")
    instant = now or datetime.now(timezone.utc)
    status = status_snapshot(paths, now=instant)
    from .audit import build_company_tasks, build_job_tasks

    company_engine = QueueEngine(
        paths.company_queue,
        "validate_companies",
        validator=validator("company-queue.schema.json"),
    )
    job_engine = QueueEngine(
        paths.job_queue,
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )
    canonical = JsonStore(
        paths.companies,
        validator=validator("companies.schema.json"),
    ).read()
    company_queue = company_engine.read()
    job_queue = job_engine.read()
    profile = json.loads(paths.profile.read_text(encoding="utf-8"))
    validate(profile, "search-profile.schema.json", str(paths.profile))
    archive = (
        json.loads(paths.research_archive.read_text(encoding="utf-8"))
        if paths.research_archive.exists()
        else None
    )
    with FileLock(paths.jobs_lock, exclusive=False, timeout=10):
        snapshots = _read_job_snapshots(paths)
    prospective = {
        "validate_companies": build_company_tasks(
            canonical,
            company_engine,
            research_archive=archive,
            now=instant,
        ),
        "find_jobs": build_job_tasks(
            canonical,
            job_engine,
            profile,
            snapshots,
            now=instant,
        ),
    }
    queue_documents = {
        "validate_companies": company_queue,
        "find_jobs": job_queue,
    }
    workflow_config = (
        (
            "validate_companies",
            "Company validation",
            "$validate-companies",
            "company",
            company_workers,
        ),
        ("find_jobs", "Job finding", "$find-jobs", "jobs", job_workers),
    )
    workflows: list[dict[str, Any]] = []
    total_launches = 0
    for (
        workflow,
        label,
        command,
        cli_workflow,
        worker_target,
    ) in workflow_config:
        queue = status["queues"][workflow]
        existing_keys = {
            task["dedupe_key"] for task in queue_documents[workflow]["tasks"]
        }
        missing_audit_tasks = [
            task
            for task in prospective[workflow]
            if task["dedupe_key"] not in existing_keys
        ]
        pending_audit_by_model = Counter(task["expected_model"] for task in missing_audit_tasks)
        pending_audit_by_lane = Counter(
            (
                task["expected_model"],
                "structured"
                if task.get("context", {}).get("structured_adapter_available") is True
                else "manual",
            )
            for task in missing_audit_tasks
        )
        launches: list[dict[str, Any]] = []
        for model in ("luna", "terra", "sol"):
            active_for_model = sum(
                task["status"] == "running"
                and task["expected_model"] == model
                and parse_timestamp(task["claim"]["lease_expires_at"]) > instant
                for task in queue_documents[workflow]["tasks"]
            )
            remaining_capacity = max(0, worker_target - active_for_model)
            candidate_lanes: tuple[str | None, ...] = (
                ("manual", "structured") if workflow == "find_jobs" else (None,)
            )
            for lane in candidate_lanes:
                if workflow == "find_jobs":
                    assert lane is not None
                    queued_due = sum(
                        task["status"] == "open"
                        and task["expected_model"] == model
                        and parse_timestamp(task["available_at"]) <= instant
                        and (
                            "structured"
                            if task.get("context", {}).get(
                                "structured_adapter_available"
                            )
                            is True
                            else "manual"
                        )
                        == lane
                        for task in queue_documents[workflow]["tasks"]
                    )
                    pending_audit = pending_audit_by_lane.get((model, lane), 0)
                else:
                    queued_due = queue["availability_by_model"][model]["due"]
                    pending_audit = pending_audit_by_model.get(model, 0)
                due = queued_due + pending_audit
                if not due or not remaining_capacity:
                    continue
                count = min(due, remaining_capacity)
                remaining_capacity -= count
                total_launches += count
                launches.append(
                    {
                        "model": model,
                        "due_tasks": due,
                        "queued_due_tasks": queued_due,
                        "pending_audit_tasks": pending_audit,
                        "worker_target": worker_target,
                        "active_workers": active_for_model,
                        "launch_count": count,
                        "lane": lane,
                        "command": (
                            f"{command} --run {model} --lane {lane}"
                            if lane is not None
                            else f"{command} --run {model}"
                        ),
                    }
                )
        expired = queue["running"]["expired"]
        blocked = queue["by_status"]["blocked"]
        failed = queue["by_status"]["failed"]
        running = queue["running"]["active"]
        delayed = queue["open"]["delayed"]
        audit_recommended = bool(missing_audit_tasks)
        workflows.append(
            {
                "workflow": workflow,
                "label": label,
                "launches": launches,
                "active_running": running,
                "delayed_open": delayed,
                "expired_running": expired,
                "recovery_command": (
                    f".venv/bin/v4-queue {cli_workflow} recover-stale"
                    if expired
                    else None
                ),
                "blocked": blocked,
                "failed": failed,
                "inspect_commands": [
                    *(
                        [
                            f".venv/bin/v4-queue {'company' if workflow == 'validate_companies' else 'jobs'} inspect --status blocked --json"
                        ]
                        if blocked
                        else []
                    ),
                    *(
                        [
                            f".venv/bin/v4-queue {'company' if workflow == 'validate_companies' else 'jobs'} inspect --status failed --json"
                        ]
                        if failed
                        else []
                    ),
                ],
                "audit_recommended": audit_recommended,
                "audit_task_count": len(missing_audit_tasks),
                "audit_command": (
                    f".venv/bin/v4-queue {cli_workflow} audit-and-enqueue"
                    if audit_recommended
                    else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": status["generated_at"],
        "company_workers": company_workers,
        "job_workers": job_workers,
        "total_planned_launches": total_launches,
        "workflows": workflows,
        "notes": [
            "Each launched worker keeps claiming exact tasks until its model lane has no due work.",
            "Concurrent workers cannot overlap because every task is claimed atomically.",
            "Re-run this command after a queue drains; retries and model escalation may create later work.",
        ],
    }


def render_next_commands(report: Mapping[str, Any]) -> str:
    lines = [f"V4 next commands — {report['generated_at']}"]
    for workflow in report["workflows"]:
        lines.extend(["", workflow["label"]])
        if workflow["recovery_command"]:
            lines.append(
                f"  Run once: {workflow['recovery_command']} "
                f"({workflow['expired_running']} expired claim(s))"
            )
        if workflow["audit_recommended"]:
            lines.append(
                f"  Run once first: {workflow['audit_command']} "
                f"({workflow['audit_task_count']} task(s) not yet queued)"
            )
        for launch in workflow["launches"]:
            lines.append(
                f"  Open {launch['launch_count']} new agent(s), once each: "
                f"{launch['command']}"
            )
            lines.append(
                f"    {launch['due_tasks']} due {launch['model'].title()} task(s); "
                f"target concurrency {launch['worker_target']}, "
                f"{launch['active_workers']} already active."
            )
            if launch["pending_audit_tasks"]:
                lines.append(
                    f"    Includes {launch['pending_audit_tasks']} task(s) added by the audit above."
                )
        if not workflow["launches"] and not workflow["recovery_command"]:
            if workflow["active_running"]:
                lines.append(
                    f"  No new launch now; {workflow['active_running']} task(s) are actively running."
                )
            elif workflow["delayed_open"]:
                lines.append(
                    f"  No due launch now; {workflow['delayed_open']} retry task(s) are delayed."
                )
            else:
                lines.append("  No command required; no due or newly recommended work.")
        if workflow["blocked"] or workflow["failed"]:
            lines.append(
                f"  Attention: {workflow['blocked']} blocked | {workflow['failed']} failed."
            )
            lines.extend(f"    Inspect: {command}" for command in workflow["inspect_commands"])
    lines.extend(["", f"Planned agent launches: {report['total_planned_launches']}"])
    lines.extend(f"Note: {note}" for note in report["notes"])
    return "\n".join(lines) + "\n"


def render_status(report: Mapping[str, Any]) -> str:
    queues = report["queues"]
    companies = report["companies"]
    sources = report["career_sources"]
    coverage = report["job_coverage"]
    attention = report["attention"]
    recent = report["recent_activity"]
    ready_providers = sources["deterministic_adapter_ready"]
    primary_provider_counts = {
        provider: ready_providers.get(provider, 0)
        for provider in ("ashby", "greenhouse", "lever")
    }
    other_ready = sources["agent_enumeration_ready"]
    lines = [f"V4 status — {report['generated_at']}", "", "Queues"]
    for workflow, label in (
        ("validate_companies", "Company validation"),
        ("find_jobs", "Job finding"),
    ):
        counts = queues[workflow]
        status = counts["by_status"]
        lines.append(
            f"  {label}: open {status.get('open', 0)} | running {status.get('running', 0)} "
            f"| completed {status.get('completed', 0)} | blocked {status.get('blocked', 0)} "
            f"| failed {status.get('failed', 0)}"
        )
        lines.append(
            "    "
            + " | ".join(
                f"{model.title()} {counts['by_model'][model].get('open', 0)} open"
                for model in ("luna", "terra", "sol")
            )
        )
    status = companies["by_address_status"]
    lines.extend(
        [
            "",
            "Companies",
            f"  Total {companies['total']}",
            f"  Verified {status.get('verified', 0)} | Outside SF {status.get('outside_sf', 0)} "
            f"| Not found {status.get('not_found', 0)} | Pending {status.get('pending', 0)}",
            "",
            "Career sources",
            f"  Total {sources['total']} | Ready {sources['by_status'].get('ready', 0)} "
            f"| No careers {sources['by_status'].get('no_careers_found', 0)} "
            f"| Unsupported {sources['by_status'].get('unsupported', 0)} "
            f"| Blocked {sources['by_status'].get('blocked', 0)} "
            f"| Stale {sources['by_status'].get('stale', 0)}",
            "  Deterministic adapters: "
            f"Ashby {primary_provider_counts['ashby']} | "
            f"Greenhouse {primary_provider_counts['greenhouse']} | "
            f"Lever {primary_provider_counts['lever']} | Agent enumeration {other_ready}",
            "",
            "Job coverage",
            f"  Current {coverage.get('current', 0)} | Stale {coverage.get('stale', 0)} "
            f"| Incomplete {coverage.get('incomplete', 0)} | Missing {coverage.get('missing', 0)}",
            f"  Active qualifying jobs {coverage['active_qualifying_jobs']} | "
            f"Unresolved candidates {coverage['unresolved_candidates']}",
            "",
            "Needs attention",
            f"  Expired leases {attention['expired_leases']} | Blocked tasks {attention['blocked_tasks']} "
            f"| Failed tasks {attention['failed_tasks']} | Human review {attention['human_review']} "
            f"| Data warnings {attention['data_warnings']}",
            "",
            "Recent activity",
            "  Last 24h: "
            + " | ".join(
                f"{label.replace('_', ' ').title()} {recent['24h'].get(label, 0)}"
                for label in ("completed", "inconclusive", "blocked", "failed")
            ),
            "  Last 7d:  "
            + " | ".join(
                f"{label.replace('_', ' ').title()} {recent['7d'].get(label, 0)}"
                for label in ("completed", "inconclusive", "blocked", "failed")
            ),
        ]
    )
    return "\n".join(lines) + "\n"
