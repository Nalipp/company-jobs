"""Compact, secret-free operational telemetry for deterministic helpers."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .json_store import FileLock
from .paths import RepoPaths
from .queue import format_timestamp, parse_timestamp
from .schemas import validate


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def event_from_report(
    report: Mapping[str, Any],
    *,
    duration_ms: int = 0,
) -> dict[str, Any] | None:
    """Reduce an operation report to safe fields; never retain tokens or evidence."""

    operation = _text(report.get("operation"))
    execution_status = _text(report.get("execution_status"))
    timestamp = _text(report.get("generated_at"))
    if operation is None or execution_status is None or timestamp is None:
        return None
    task = report.get("task") if isinstance(report.get("task"), Mapping) else {}
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    claim = task.get("claim") if isinstance(task.get("claim"), Mapping) else {}
    target = task.get("target") if isinstance(task.get("target"), Mapping) else {}
    source = (
        context.get("source_snapshot")
        if isinstance(context.get("source_snapshot"), Mapping)
        else {}
    )
    workflow = operation.split(".", 1)[0]
    workflow = workflow if workflow in {"validate_companies", "find_jobs"} else None
    structured = context.get("structured_adapter_available")
    lane = task.get("lane")
    if lane not in {"structured", "manual"} and workflow == "find_jobs":
        if structured is True:
            lane = "structured"
        elif structured is False:
            lane = "manual"
        else:
            lane = None
    queue_delta = report.get("queue_delta") if isinstance(report.get("queue_delta"), Mapping) else {}
    metrics_value = task.get("metrics") if isinstance(task.get("metrics"), Mapping) else {}
    event = {
        "schema_version": 1,
        "timestamp": timestamp,
        "duration_ms": max(0, int(duration_ms)),
        "operation": operation,
        "execution_status": execution_status,
        "workflow": workflow,
        "task_id": _text(task.get("task_id")),
        "attempt_id": _text(task.get("attempt_id") or claim.get("attempt_id")),
        "worker_id": _text(task.get("worker_id") or claim.get("claimed_by")),
        "model": _text(task.get("model") or task.get("expected_model")),
        "lane": lane if lane in {"structured", "manual"} else None,
        "provider": _text(task.get("provider") or source.get("provider")),
        "company_ids": _list(target.get("company_ids")),
        "source_ids": _list(target.get("source_ids")),
        "outcome": _text(task.get("outcome")),
        "queue_status": _text(
            task.get("queue_status") or task.get("status") or queue_delta.get("final_status")
        ),
        "reason_code": _text(task.get("reason_code") or queue_delta.get("reason")),
        "failure_class": _text(task.get("failure_class")),
        "retryable": task.get("retryable") if isinstance(task.get("retryable"), bool) else None,
        "metrics": {
            "jobs_fetched": metrics_value.get("jobs_fetched")
            if isinstance(metrics_value.get("jobs_fetched"), int)
            else None,
            "qualifying_jobs": metrics_value.get("qualifying_jobs")
            if isinstance(metrics_value.get("qualifying_jobs"), int)
            else None,
            "unresolved_candidates": metrics_value.get("unresolved_candidates")
            if isinstance(metrics_value.get("unresolved_candidates"), int)
            else None,
            "item_results": len(report.get("item_results", []))
            if isinstance(report.get("item_results"), list)
            else 0,
        },
        "changes_count": len(report.get("changes", []))
        if isinstance(report.get("changes"), list)
        else 0,
        "warnings_count": len(report.get("warnings", []))
        if isinstance(report.get("warnings"), list)
        else 0,
    }
    validate(event, "telemetry-event.schema.json", "Telemetry event")
    return event


def append_event(paths: RepoPaths, event: Mapping[str, Any]) -> Path:
    validate(dict(event), "telemetry-event.schema.json", "Telemetry event")
    day = parse_timestamp(str(event["timestamp"])).date().isoformat()
    path = paths.telemetry_dir / f"{day}.jsonl"
    paths.telemetry_dir.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(event), sort_keys=True, ensure_ascii=False) + "\n"
    with FileLock(paths.telemetry_lock, exclusive=True, timeout=10):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return path


def record_report(
    paths: RepoPaths,
    report: Mapping[str, Any],
    *,
    duration_ms: int = 0,
) -> Path | None:
    event = event_from_report(report, duration_ms=duration_ms)
    return append_event(paths, event) if event is not None else None


def read_events(
    paths: RepoPaths,
    *,
    days: int = 1,
    now: datetime | None = None,
    workflow: str | None = None,
    worker_id: str | None = None,
) -> list[dict[str, Any]]:
    if not 1 <= days <= 365:
        raise ValueError("telemetry days must be between 1 and 365")
    instant = now or datetime.now(timezone.utc)
    cutoff = instant - timedelta(days=days)
    events: list[dict[str, Any]] = []
    if not paths.telemetry_dir.exists():
        return events
    with FileLock(paths.telemetry_lock, exclusive=False, timeout=10):
        for path in sorted(paths.telemetry_dir.glob("*.jsonl")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid telemetry JSON at {path}:{line_number}") from exc
                validate(event, "telemetry-event.schema.json", f"{path}:{line_number}")
                if parse_timestamp(event["timestamp"]) < cutoff:
                    continue
                if workflow is not None and event["workflow"] != workflow:
                    continue
                if worker_id is not None and event["worker_id"] != worker_id:
                    continue
                events.append(event)
    return sorted(events, key=lambda item: item["timestamp"])


def _counts(values: Iterable[str | None]) -> dict[str, int]:
    counts = Counter(value for value in values if value is not None)
    return {key: counts[key] for key in sorted(counts)}


def telemetry_summary(
    paths: RepoPaths,
    *,
    days: int = 1,
    now: datetime | None = None,
    workflow: str | None = None,
    worker_id: str | None = None,
    tail: int = 20,
) -> dict[str, Any]:
    if not 0 <= tail <= 200:
        raise ValueError("telemetry tail must be between 0 and 200")
    events = read_events(
        paths,
        days=days,
        now=now,
        workflow=workflow,
        worker_id=worker_id,
    )
    finalized = [
        event
        for event in events
        if event["operation"].endswith(".finalize")
        and event["execution_status"] == "completed"
        and event["task_id"] is not None
        and event["outcome"] is not None
    ]
    claims_by_attempt = {
        event["attempt_id"]: event
        for event in events
        if event["operation"].endswith(".claim-next") and event["attempt_id"]
    }
    work_durations = [
        max(
            0,
            round(
                (
                    parse_timestamp(event["timestamp"])
                    - parse_timestamp(claims_by_attempt[event["attempt_id"]]["timestamp"])
                ).total_seconds()
                * 1000
            ),
        )
        for event in finalized
        if event["attempt_id"] in claims_by_attempt
    ]
    return {
        "schema_version": 1,
        "generated_at": format_timestamp(now or datetime.now(timezone.utc)),
        "days": days,
        "filters": {"workflow": workflow, "worker_id": worker_id},
        "event_count": len(events),
        "unique_workers": len({event["worker_id"] for event in events if event["worker_id"]}),
        "unique_tasks": len({event["task_id"] for event in events if event["task_id"]}),
        "finalized_count": len(finalized),
        "by_operation": _counts(event["operation"] for event in events),
        "by_execution_status": _counts(event["execution_status"] for event in events),
        "by_outcome": _counts(event["outcome"] for event in finalized),
        "by_queue_status": _counts(event["queue_status"] for event in finalized),
        "by_reason_code": _counts(event["reason_code"] for event in events),
        "by_failure_class": _counts(event["failure_class"] for event in events),
        "by_provider": _counts(event["provider"] for event in events),
        "by_lane": _counts(event["lane"] for event in events),
        "metrics": {
            key: sum(
                int(event["metrics"][key] or 0)
                for event in finalized
            )
            for key in ("jobs_fetched", "qualifying_jobs", "unresolved_candidates")
        },
        "helper_runtime_ms": sum(event["duration_ms"] for event in events),
        "work_duration": {
            "measured_attempts": len(work_durations),
            "total_ms": sum(work_durations),
            "average_ms": (
                round(sum(work_durations) / len(work_durations))
                if work_durations
                else None
            ),
        },
        "recent": events[-tail:] if tail else [],
    }


def render_telemetry(summary: Mapping[str, Any]) -> str:
    lines = [
        f"V4 telemetry — last {summary['days']} day(s)",
        "",
        f"Events {summary['event_count']} | Workers {summary['unique_workers']} | "
        f"Tasks {summary['unique_tasks']} | Finalized {summary['finalized_count']}",
        f"Execution: {json.dumps(summary['by_execution_status'], sort_keys=True)}",
        f"Outcomes: {json.dumps(summary['by_outcome'], sort_keys=True)}",
        f"Queue states: {json.dumps(summary['by_queue_status'], sort_keys=True)}",
        f"Failure reasons: {json.dumps(summary['by_reason_code'], sort_keys=True)}",
        f"Failure classes: {json.dumps(summary['by_failure_class'], sort_keys=True)}",
        f"Providers: {json.dumps(summary['by_provider'], sort_keys=True)}",
        f"Lanes: {json.dumps(summary['by_lane'], sort_keys=True)}",
        f"Metrics: {json.dumps(summary['metrics'], sort_keys=True)}",
        f"Work duration: {json.dumps(summary['work_duration'], sort_keys=True)}",
    ]
    if summary["recent"]:
        lines.extend(["", "Recent events"])
        for event in summary["recent"]:
            target = (event["source_ids"] or event["company_ids"] or ["-"])[0]
            lines.append(
                f"  {event['timestamp']} {event['operation']} {target} "
                f"{event['outcome'] or event['execution_status']}"
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "append_event",
    "event_from_report",
    "read_events",
    "record_report",
    "render_telemetry",
    "telemetry_summary",
]
