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
from .json_store import JsonStore
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
    for model in ("luna", "terra", "sol"):
        counts = Counter(
            task["status"] for task in document["tasks"] if task["expected_model"] == model
        )
        by_model[model] = {status: counts.get(status, 0) for status in STATUSES}
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
        qualifying += sum(
            job.get("status", "active") == "active"
            and job.get("qualification_status") in {"qualifying", "deterministic_pass"}
            for job in snapshot["jobs"]
        )
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
