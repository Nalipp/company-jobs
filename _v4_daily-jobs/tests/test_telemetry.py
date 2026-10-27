from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

from v4_daily_jobs.cli import main as cli_main
from v4_daily_jobs.paths import RepoPaths
from v4_daily_jobs.telemetry import (
    append_event,
    event_from_report,
    read_events,
    telemetry_summary,
)


STAMP = "2026-08-31T12:00:00Z"


def _event(index: int) -> dict:
    return {
        "schema_version": 1,
        "timestamp": STAMP,
        "duration_ms": index,
        "operation": "find_jobs.finalize",
        "execution_status": "completed",
        "workflow": "find_jobs",
        "task_id": f"task-{index}",
        "attempt_id": f"attempt-{index}",
        "worker_id": f"worker-{index % 2}",
        "model": "luna",
        "lane": "manual",
        "provider": "custom",
        "company_ids": [f"company-{index}"],
        "source_ids": [f"source-{index}"],
        "outcome": "resolved",
        "queue_status": "completed",
        "reason_code": None,
        "failure_class": None,
        "retryable": None,
        "metrics": {
            "jobs_fetched": 4,
            "qualifying_jobs": 1,
            "unresolved_candidates": 0,
            "item_results": 1,
        },
        "changes_count": 1,
        "warnings_count": 0,
    }


def test_event_reduction_excludes_secrets_and_research_payloads() -> None:
    report = {
        "operation": "find_jobs.claim-next",
        "execution_status": "completed",
        "generated_at": STAMP,
        "task": {
            "task_id": "task-1",
            "expected_model": "luna",
            "target": {
                "company_ids": ["example"],
                "source_ids": ["example-primary"],
            },
            "claim": {
                "attempt_id": "attempt-1",
                "claimed_by": "worker-1",
                "claim_token": "TOP-SECRET-TOKEN",
            },
            "context": {
                "structured_adapter_available": False,
                "source_snapshot": {
                    "provider": "custom",
                    "instructions": "PRIVATE RESEARCH INSTRUCTIONS",
                },
                "evidence": ["PRIVATE EVIDENCE"],
            },
        },
        "item_results": [],
        "changes": [],
        "queue_delta": {},
        "warnings": [],
    }
    event = event_from_report(report, duration_ms=37)
    assert event is not None
    assert event["worker_id"] == "worker-1"
    assert event["lane"] == "manual"
    assert event["source_ids"] == ["example-primary"]
    encoded = json.dumps(event)
    assert "TOP-SECRET-TOKEN" not in encoded
    assert "PRIVATE RESEARCH INSTRUCTIONS" not in encoded
    assert "PRIVATE EVIDENCE" not in encoded


def test_parallel_appends_are_complete_and_summary_is_deterministic(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory())
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: append_event(paths, _event(index)), range(40)))

    events = read_events(
        paths,
        days=1,
        now=datetime(2026, 8, 31, 13, tzinfo=timezone.utc),
    )
    assert len(events) == 40
    assert {event["task_id"] for event in events} == {
        f"task-{index}" for index in range(40)
    }
    summary = telemetry_summary(
        paths,
        days=1,
        now=datetime(2026, 8, 31, 13, tzinfo=timezone.utc),
        tail=3,
    )
    assert summary["unique_workers"] == 2
    assert summary["finalized_count"] == 40
    assert summary["by_execution_status"] == {"completed": 40}
    assert summary["metrics"] == {
        "jobs_fetched": 160,
        "qualifying_jobs": 40,
        "unresolved_candidates": 0,
    }
    assert len(summary["recent"]) == 3
    assert summary["work_duration"]["measured_attempts"] == 0


def test_cli_records_workflow_operations_but_summary_is_read_only(
    repo_factory, capsys
) -> None:
    root = repo_factory()
    assert cli_main(
        ["--root", str(root), "company", "audit-and-enqueue", "--json"]
    ) == 0
    capsys.readouterr()
    paths = RepoPaths.discover(root)
    event_files = list(paths.telemetry_dir.glob("*.jsonl"))
    assert len(event_files) == 1
    before = event_files[0].read_bytes()

    assert cli_main(
        ["--root", str(root), "telemetry", "--days", "365", "--json"]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["by_operation"] == {"validate_companies.audit-and-enqueue": 1}
    assert event_files[0].read_bytes() == before
