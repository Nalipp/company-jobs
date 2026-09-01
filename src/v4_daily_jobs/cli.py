"""Command-line boundary for every V4 queue and canonical mutation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from .audit import audit_and_enqueue_companies, audit_and_enqueue_jobs
from .companies import validate_integrity
from .finalize import finalize_result, preflight_result, recover_journal
from .fetching import prepare_job_result
from .json_store import FileLock, JsonStore
from .migration import DEFAULT_V3_ROOT, migrate
from .maintenance import (
    enqueue_job_source_revalidations,
    repair_invalid_job_escalations,
    repair_job_qualification_statuses,
    repair_unsupported_provider_retries,
)
from .paths import RepoPaths
from .queue import QueueEngine
from .reporting import (
    next_commands_snapshot,
    operation_report,
    queue_counts,
    render_next_commands,
    render_status,
    status_snapshot,
)
from .schemas import validator
from .telemetry import record_report, render_telemetry, telemetry_summary


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _engine(paths: RepoPaths, workflow: str) -> QueueEngine:
    if workflow == "validate_companies":
        return QueueEngine(
            paths.company_queue,
            workflow,
            validator=validator("company-queue.schema.json"),
        )
    return QueueEngine(
        paths.job_queue,
        workflow,
        validator=validator("job-queue.schema.json"),
    )


def _workflow(value: str) -> str:
    return "validate_companies" if value == "company" else "find_jobs"


def _snapshots(paths: RepoPaths) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    paths.job_sources.mkdir(parents=True, exist_ok=True)
    with FileLock(paths.jobs_lock, exclusive=False, timeout=10):
        for path in sorted(paths.job_sources.glob("*.json")):
            document = _json(path)
            result[document["source_id"]] = document
    return result


def _progress_for_queue(engine: QueueEngine) -> dict[str, Any]:
    return queue_counts(engine.read())


def _handle_workflow(paths: RepoPaths, args: argparse.Namespace) -> dict[str, Any]:
    workflow = _workflow(args.workflow)
    engine = _engine(paths, workflow)
    command = args.workflow_command
    if command != "inspect":
        recover_journal(paths, workflow)

    if command == "repair-snapshots":
        repaired = repair_job_qualification_statuses(paths)
        return operation_report(
            "find_jobs.repair-snapshots",
            task={"repair_version": repaired["repair_version"]},
            changes=[
                f"{item['source_id']}:{item['stable_id']}: qualification_status=qualifying"
                for item in repaired["changed_jobs"]
            ],
            queue_delta={"queue_revision": engine.read()["revision"]},
            progress={"repair": repaired},
            message=(
                f"Repaired {repaired['job_count']} job(s) in "
                f"{repaired['source_count']} source snapshot(s)."
            ),
        )

    if command == "repair-escalations":
        repaired = repair_invalid_job_escalations(paths)
        return operation_report(
            "find_jobs.repair-escalations",
            task={"repair_version": repaired["repair_version"]},
            changes=[
                f"reopened {task_id} and superseded its unused Terra follow-up"
                for task_id in repaired["reopened_task_ids"]
            ],
            queue_delta={
                "reopened": repaired["reopened_count"],
                "superseded": repaired["superseded_count"],
                "skipped": repaired["skipped_count"],
                "queue_revision": repaired["queue_revision"],
            },
            progress={"repair": repaired},
            warnings=repaired["skipped_reasons"],
            message=(
                f"Reopened {repaired['reopened_count']} prematurely escalated Luna task(s) "
                f"and superseded {repaired['superseded_count']} unused Terra follow-up(s)."
            ),
        )

    if command == "repair-lanes":
        repaired = repair_unsupported_provider_retries(paths)
        return operation_report(
            "find_jobs.repair-lanes",
            task={"repair_version": repaired["repair_version"]},
            changes=[
                f"removed invalid structured-helper retry charges from {task_id}"
                for task_id in repaired["repaired_task_ids"]
            ],
            queue_delta={
                "repaired_tasks": repaired["task_count"],
                "repaired_attempts": repaired["attempt_count"],
                "reopened": repaired["reopened_count"],
                "queue_revision": repaired["queue_revision"],
            },
            progress=_progress_for_queue(engine),
            message=(
                f"Repaired {repaired['attempt_count']} invalid lane attempt(s) across "
                f"{repaired['task_count']} task(s); reopened {repaired['reopened_count']}."
            ),
        )

    if command == "route-source-repairs":
        routed = enqueue_job_source_revalidations(paths)
        return operation_report(
            "find_jobs.route-source-repairs",
            changes=[
                *[f"created company source-revalidation task {task_id}" for task_id in routed["created_task_ids"]],
                *[f"augmented company task {task_id} with source revalidation" for task_id in routed["augmented_task_ids"]],
            ],
            queue_delta=routed,
            progress={"company_queue": _progress_for_queue(_engine(paths, "validate_companies"))},
            message=(
                f"Routed {routed['request_count']} permanent source failure(s): "
                f"created {routed['created_count']} company task(s), augmented "
                f"{routed['augmented_count']}."
            ),
        )

    if command == "claim-next":
        result = engine.claim_next(
            args.model,
            claimed_by=args.claimed_by,
            lane=getattr(args, "lane", None),
        )
        if not result.claimed:
            label = "company-validation" if workflow == "validate_companies" else "job-finding"
            lane_label = f" {args.lane}" if getattr(args, "lane", None) else ""
            return operation_report(
                f"{workflow}.claim-next",
                execution_status="no_task",
                queue_delta={
                    "reason": result.reason,
                    "queue_revision": result.revision,
                },
                progress=_progress_for_queue(engine),
                message=(
                    f"No open {args.model.title()}{lane_label} {label} task was available. "
                    "No files changed."
                ),
            )
        assert result.task is not None
        return operation_report(
            f"{workflow}.claim-next",
            task=result.task,
            queue_delta={
                "claimed": 1,
                "queue_revision": result.revision,
            },
            progress=_progress_for_queue(engine),
            message=(
                f"Claimed {result.task['task_id']} for {args.model}. "
                f"Only the returned company/source IDs are in scope."
            ),
        )

    if command == "renew":
        result = engine.renew(
            args.task_id,
            args.claim_token,
            attempt_id=args.attempt_id,
            expected_model=args.model,
        )
        return operation_report(
            f"{workflow}.renew",
            task=result.task,
            queue_delta={"queue_revision": result.revision},
            progress=_progress_for_queue(engine),
            message=f"Renewed lease for {args.task_id}.",
        )

    if command == "prepare-result":
        with engine.transaction() as locked:
            task = locked.require_claim(
                args.task_id,
                args.claim_token,
                attempt_id=args.attempt_id,
                expected_model=args.model,
            )
            task_snapshot = json.loads(json.dumps(task))
        prepared = prepare_job_result(paths, task_snapshot)
        result = prepared["result"]
        snapshot = result.get("snapshot") or {}
        adapter = snapshot.get("adapter_result") or {}
        return operation_report(
            f"{workflow}.prepare-result",
            task={
                "task_id": task["task_id"],
                "attempt_id": args.attempt_id,
                "model": args.model,
                "target": task["target"],
                "draft_outcome": result["outcome"],
                "failure_class": result.get("failure_class"),
                "result_path": prepared["result_path"],
            },
            item_results=result["item_results"],
            changes=[
                f"{'created' if prepared['created'] else 'reused'} result draft {prepared['result_path']}"
            ],
            progress={
                "fetched_jobs": len(adapter.get("jobs", [])),
                "semantic_candidates": prepared.get("semantic_candidates", 0),
                "draft_result": prepared["result_path"],
                "stop_worker": result.get("failure_class") == "environment_network",
            },
            message=(
                (
                    "Local DNS/network access is unavailable. Finalize this draft to "
                    "release the claim without consuming a source retry, then stop the worker."
                )
                if result.get("failure_class") == "environment_network"
                else (
                    f"Prepared {prepared['result_path']}. Review its semantic candidates, "
                    "then finalize the result through the queue helper."
                )
            ),
        )
    if command == "finalize":
        return finalize_result(paths, args.result.resolve())

    if command == "preflight-result":
        return preflight_result(paths, args.result.resolve())

    if command == "recover-stale":
        result = engine.recover_stale(task_id=args.task_id)
        return operation_report(
            f"{workflow}.recover-stale",
            changes=[f"reopened {task_id}" for task_id in result.recovered_task_ids],
            queue_delta={
                "recovered": len(result.recovered_task_ids),
                "queue_revision": result.revision,
            },
            progress=_progress_for_queue(engine),
            message=f"Recovered {len(result.recovered_task_ids)} expired running task(s).",
        )

    if command == "abandon":
        result = engine.abandon(args.task_id, reason=args.reason)
        return operation_report(
            f"{workflow}.abandon",
            changes=[f"abandoned active attempt and reopened {args.task_id}"],
            queue_delta={
                "reopened": 1,
                "queue_revision": result.revision,
            },
            progress=_progress_for_queue(engine),
            message=f"Safely abandoned the stopped agent claim for {args.task_id} and reopened it.",
        )

    if command == "retry":
        result = engine.retry(args.task_id, reason=args.reason)
        return operation_report(
            f"{workflow}.retry",
            task=result.task,
            queue_delta={"reopened": 1, "queue_revision": result.revision},
            progress=_progress_for_queue(engine),
            message=f"Reopened {args.task_id} at the same model.",
        )

    if command == "inspect":
        tasks = engine.inspect(
            status=args.status,
            model=args.model,
            lane=getattr(args, "lane", None),
        )
        return {
            "schema_version": 1,
            "workflow": workflow,
            "filters": {
                "status": args.status,
                "model": args.model,
                "lane": getattr(args, "lane", None),
            },
            "count": len(tasks),
            "tasks": list(tasks),
        }

    canonical = _json(paths.companies)
    validate_integrity(canonical)
    if workflow == "validate_companies":
        archive = _json(paths.research_archive) if paths.research_archive.exists() else None
        outcome = audit_and_enqueue_companies(
            canonical,
            engine,
            research_archive=archive,
        )
    else:
        profile = _json(paths.profile)
        outcome = audit_and_enqueue_jobs(
            canonical,
            engine,
            profile,
            _snapshots(paths),
            stale_after_hours=args.stale_after_hours,
        )
    return operation_report(
        f"{workflow}.audit-and-enqueue",
        changes=(
            [f"created {outcome['created']} deterministic task(s)"]
            if outcome["created"]
            else []
        ),
        queue_delta={
            key: value
            for key, value in outcome.items()
            if key != "created_task_ids"
        },
        progress=_progress_for_queue(engine),
        message=(
            f"Audit recommended {outcome['recommended']} task(s), created "
            f"{outcome['created']}, and deduplicated {outcome['deduplicated']}."
        ),
    )


def _render_operation(report: dict[str, Any]) -> str:
    lines = [report.get("message", "Operation completed.")]
    task = report.get("task")
    if isinstance(task, dict):
        task_id = task.get("task_id")
        if task_id:
            lines.extend(["", "Task result", f"  Task: {task_id}"])
        if task.get("expected_model") or task.get("model"):
            lines.append(f"  Model: {task.get('expected_model') or task.get('model')}")
        if task.get("status") or task.get("queue_status"):
            lines.append(f"  Status: {task.get('status') or task.get('queue_status')}")
        if task.get("target"):
            lines.append(f"  Target: {json.dumps(task['target'], ensure_ascii=False)}")
    if report.get("item_results"):
        lines.extend(["", "Item results"])
        for item in report["item_results"]:
            lines.append(
                f"  [{item.get('status', 'unknown')}] "
                f"{item.get('item', 'item')} — {item.get('summary', '')}"
            )
    if report.get("changes"):
        lines.extend(["", "Changes"])
        lines.extend(f"  {change}" for change in report["changes"][:20])
        if len(report["changes"]) > 20:
            lines.append(f"  ... and {len(report['changes']) - 20} more")
    if report.get("queue_delta"):
        lines.extend(["", "Run summary", f"  {json.dumps(report['queue_delta'], ensure_ascii=False)}"])
    if report.get("progress"):
        lines.extend(["", "Current progress", f"  {json.dumps(report['progress'], ensure_ascii=False)}"])
    if report.get("warnings"):
        lines.extend(["", "Warnings"])
        lines.extend(f"  {warning}" for warning in report["warnings"])
    return "\n".join(lines) + "\n"


def _print(value: dict[str, Any], *, as_json: bool, status: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    elif status:
        print(render_status(value), end="")
    elif "execution_status" in value:
        print(_render_operation(value), end="")
    else:
        print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _add_workflow_parsers(subparsers: Any, name: str) -> None:
    parser = subparsers.add_parser(name)
    commands = parser.add_subparsers(dest="workflow_command", required=True)
    claim = commands.add_parser("claim-next")
    claim.add_argument("--model", choices=("luna", "terra", "sol"), required=True)
    claim.add_argument("--claimed-by")
    if name == "jobs":
        claim.add_argument("--lane", choices=("structured", "manual"))
    renew = commands.add_parser("renew")
    renew.add_argument("--task-id", required=True)
    renew.add_argument("--claim-token", required=True)
    renew.add_argument("--attempt-id")
    renew.add_argument("--model", choices=("luna", "terra", "sol"))
    if name == "jobs":
        commands.add_parser("repair-snapshots")
        commands.add_parser("repair-escalations")
        commands.add_parser("repair-lanes")
        commands.add_parser("route-source-repairs")
        prepare = commands.add_parser("prepare-result")
        prepare.add_argument("--task-id", required=True)
        prepare.add_argument("--claim-token", required=True)
        prepare.add_argument("--attempt-id", required=True)
        prepare.add_argument("--model", choices=("luna", "terra", "sol"), required=True)
        preflight = commands.add_parser("preflight-result")
        preflight.add_argument("--result", type=Path, required=True)
    else:
        preflight = commands.add_parser("preflight-result")
        preflight.add_argument("--result", type=Path, required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--result", type=Path, required=True)
    audit = commands.add_parser("audit-and-enqueue")
    audit.add_argument("--stale-after-hours", type=int, default=24)
    recover = commands.add_parser("recover-stale")
    recover.add_argument("--task-id")
    abandon = commands.add_parser("abandon")
    abandon.add_argument("--task-id", required=True)
    abandon.add_argument("--reason", default="operator_stopped_agent")
    retry = commands.add_parser("retry")
    retry.add_argument("--task-id", required=True)
    retry.add_argument("--reason", default="operator_retry")
    inspect = commands.add_parser("inspect")
    inspect.add_argument(
        "--status",
        choices=(
            "open",
            "running",
            "completed",
            "inconclusive",
            "blocked",
            "failed",
            "superseded",
        ),
    )
    inspect.add_argument("--model", choices=("luna", "terra", "sol"))
    if name == "jobs":
        inspect.add_argument("--lane", choices=("structured", "manual"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    result.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    commands = result.add_subparsers(dest="command", required=True)
    migrate_parser = commands.add_parser("migrate-v3")
    migrate_parser.add_argument("--v3-root", type=Path, default=DEFAULT_V3_ROOT)
    migrate_parser.add_argument("--force", action="store_true")
    commands.add_parser("status")
    next_parser = commands.add_parser("next-commands")
    next_parser.add_argument("--company-workers", type=int, default=10)
    next_parser.add_argument("--job-workers", type=int, default=10)
    telemetry_parser = commands.add_parser("telemetry")
    telemetry_parser.add_argument("--days", type=int, default=1)
    telemetry_parser.add_argument(
        "--workflow", choices=("validate_companies", "find_jobs")
    )
    telemetry_parser.add_argument("--worker")
    telemetry_parser.add_argument("--tail", type=int, default=20)
    _add_workflow_parsers(commands, "company")
    _add_workflow_parsers(commands, "jobs")
    return result


def main(argv: list[str] | None = None) -> int:
    started = perf_counter()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_anywhere = "--json" in raw_argv
    if json_anywhere:
        raw_argv.remove("--json")
    args = parser().parse_args(raw_argv)
    args.json = args.json or json_anywhere
    paths = RepoPaths.discover(args.root)
    try:
        if args.command == "migrate-v3":
            value = migrate(paths.root, args.v3_root, force=args.force)
            _print(value, as_json=args.json)
        elif args.command == "status":
            value = status_snapshot(paths)
            _print(value, as_json=args.json, status=True)
        elif args.command == "next-commands":
            value = next_commands_snapshot(
                paths,
                company_workers=args.company_workers,
                job_workers=args.job_workers,
            )
            if args.json:
                _print(value, as_json=True)
            else:
                print(render_next_commands(value), end="")
        elif args.command == "telemetry":
            value = telemetry_summary(
                paths,
                days=args.days,
                workflow=args.workflow,
                worker_id=args.worker,
                tail=args.tail,
            )
            if args.json:
                _print(value, as_json=True)
            else:
                print(render_telemetry(value), end="")
        else:
            args.workflow = args.command
            value = _handle_workflow(paths, args)
            try:
                record_report(
                    paths,
                    value,
                    duration_ms=round((perf_counter() - started) * 1000),
                )
            except Exception as telemetry_error:
                print(
                    f"Telemetry warning: {telemetry_error}",
                    file=sys.stderr,
                )
            _print(value, as_json=args.json)
        return 0
    except Exception as exc:
        operation = str(getattr(args, "command", "unknown"))
        if operation in {"company", "jobs"}:
            workflow = _workflow(operation)
            operation = f"{workflow}.{getattr(args, 'workflow_command', 'unknown')}"
        else:
            workflow = None
        failure_report = {
            "operation": operation,
            "execution_status": "failed",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "task": {
                "task_id": getattr(args, "task_id", None),
                "worker_id": getattr(args, "claimed_by", None),
                "model": getattr(args, "model", None),
                "lane": getattr(args, "lane", None),
                "failure_class": type(exc).__name__,
            },
            "item_results": [],
            "changes": [],
            "queue_delta": {},
            "warnings": [],
        }
        try:
            record_report(
                paths,
                failure_report,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        except Exception:
            pass
        payload = {
            "schema_version": 1,
            "execution_status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
