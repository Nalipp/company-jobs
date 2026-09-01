"""Claim-authorized, journaled application of agent results."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .companies import apply_company_patch, source_index, validate_integrity
from .json_store import FileLock, JsonStore, canonical_json_bytes
from .jobs import (
    evaluate_jobs,
    reconcile_snapshot,
    semantic_candidates,
    validate_adapter_result,
)
from .paths import RepoPaths
from .queue import (
    DEFAULT_ACCESS_RETRY_LIMIT,
    ClaimRejected,
    QueueEngine,
    format_timestamp,
    parse_timestamp,
    utc_now,
)
from .reporting import operation_report, status_snapshot
from .schemas import validate, validator


ACCESS_FAILURE_CLASSES = {
    "network",
    "authentication",
    "rate_limit",
    "robots",
    "source_access",
    "timeout",
}
ENVIRONMENT_FAILURE_CLASSES = {"environment_network"}


class FinalizationError(RuntimeError):
    """Raised when a result cannot safely change canonical state."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalizationError(f"Cannot read result {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FinalizationError(f"Result must be a JSON object: {path}")
    return value


def _digest_result(result: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(result))).hexdigest()


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


def _write_journal(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = JsonStore(path)
    if path.exists():
        with store.locked(exclusive=True) as locked:
            locked.save(document)
    else:
        store.initialize(document)


def _remove_journal(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _append_audit(paths: RepoPaths, event: Mapping[str, Any]) -> None:
    paths.audit_events.parent.mkdir(parents=True, exist_ok=True)
    lock_path = paths.audit_events.with_suffix(".lock")
    with FileLock(lock_path, exclusive=True, timeout=10):
        with paths.audit_events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _ledger_contains(ledger: list[dict[str, Any]], digest: str, task_id: str) -> bool:
    return any(
        entry.get("result_digest") == digest and entry.get("task_id") == task_id
        for entry in ledger
    )


def _queue_resolution(
    engine: QueueEngine,
    locked: Any,
    result: Mapping[str, Any],
    *,
    now: str,
    require_unexpired: bool = True,
    result_file: str | None = None,
    follow_up_target_revision: int | None = None,
    follow_up_context: Mapping[str, Any] | None = None,
) -> Any:
    outcome = result["outcome"]
    common = {
        "result_digest": _digest_result(result),
        "reason_code": result["reason_code"],
        "evidence_count": len(result.get("evidence", [])),
        "item_results": copy.deepcopy(result.get("item_results", [])),
        "notes": result.get("notes"),
        "result_file": result_file,
    }
    if outcome == "blocked" and result.get("failure_class") in ENVIRONMENT_FAILURE_CLASSES:
        return engine.release_environment_failure_locked(
            locked,
            result["task_id"],
            result["claim_token"],
            resolution=common,
            attempt_id=result["attempt_id"],
            expected_model=result["model"],
            now=now,
            require_unexpired=require_unexpired,
        )
    if outcome == "blocked" and result.get("failure_class") in ACCESS_FAILURE_CLASSES:
        return engine.handle_access_failure_locked(
            locked,
            result["task_id"],
            result["claim_token"],
            result["failure_class"],
            resolution=common,
            attempt_id=result["attempt_id"],
            expected_model=result["model"],
            retry_limit=(
                0
                if result.get("retryable") is False
                else DEFAULT_ACCESS_RETRY_LIMIT
            ),
            now=now,
            require_unexpired=require_unexpired,
        )
    target_outcome = {
        "resolved": "completed",
        "inconclusive": "inconclusive",
        "blocked": "blocked",
        "failed": "failed",
    }[outcome]
    return engine.resolve_locked(
        locked,
        result["task_id"],
        result["claim_token"],
        target_outcome,
        resolution=common,
        attempt_id=result["attempt_id"],
        expected_model=result["model"],
        now=now,
        follow_up_target_revision=follow_up_target_revision,
        follow_up_context=follow_up_context,
        require_unexpired=require_unexpired,
    )


def _verify_result_scope(task: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    if result["workflow"] != task["workflow"]:
        raise FinalizationError("Result workflow does not match claimed task")
    if result["model"] != task["expected_model"]:
        raise FinalizationError("Result model does not match claimed task")
    if result["target"] != task["target"]:
        raise FinalizationError("Result target IDs do not exactly match claimed task")
    if result["workflow"] == "validate_companies":
        patch = result.get("patch")
        if not isinstance(patch, Mapping):
            raise FinalizationError("Company result lacks a keyed patch")
        if patch.get("company_id") != task["target"]["company_ids"][0]:
            raise FinalizationError("Company patch ID does not match claimed target")
        if patch.get("expected_record_revision") != task["target_revision"]:
            raise FinalizationError("Company patch revision does not match claimed target revision")
        if result["outcome"] in {"blocked", "failed"} and (
            patch.get("company_changes") or patch.get("source_upserts")
        ):
            raise FinalizationError("Blocked/failed company results cannot carry unapplied changes")
    else:
        snapshot = result.get("snapshot")
        if snapshot is not None:
            if snapshot.get("expected_source_revision") != task["target_revision"]:
                raise FinalizationError("Job snapshot revision does not match claimed source revision")
            if snapshot.get("source_id") != task["target"]["source_ids"][0]:
                raise FinalizationError("Job snapshot source does not match claimed target")
            if snapshot.get("company_id") != task["target"]["company_ids"][0]:
                raise FinalizationError("Job snapshot company does not match claimed target")
            context = task.get("context", {})
            expected_profile = context.get("profile", {})
            if snapshot.get("profile_id") != expected_profile.get("profile_id"):
                raise FinalizationError("Job result profile ID does not match claimed profile")
            if snapshot.get("profile_digest") != context.get("profile_digest"):
                raise FinalizationError("Job result profile digest does not match claimed profile")
            if context.get("structured_adapter_available") is False:
                adapter_result = snapshot.get("adapter_result")
                jobs = adapter_result.get("jobs") if isinstance(adapter_result, Mapping) else None
                if not isinstance(jobs, list):
                    raise FinalizationError("Manual job result lacks normalized jobs")
                evaluation = evaluate_jobs(jobs, expected_profile)
                candidates = semantic_candidates(jobs, evaluation)
                candidate_ids = {str(item["stable_id"]) for item in candidates}
                decisions = snapshot.get("semantic_decisions")
                if not isinstance(decisions, list):
                    raise FinalizationError(
                        "Manual job result requires semantic_decisions for every viable candidate"
                    )
                decision_ids = {
                    str(item.get("stable_id"))
                    for item in decisions
                    if isinstance(item, Mapping)
                }
                if decision_ids != candidate_ids or len(decisions) != len(decision_ids):
                    raise FinalizationError(
                        "Manual semantic_decisions must cover every viable candidate exactly once"
                    )
                qualifying_ids = {
                    str(item if isinstance(item, str) else item.get("stable_id"))
                    for item in snapshot.get("qualifying_jobs", [])
                    if isinstance(item, (str, Mapping))
                }
                unresolved_ids = {
                    str(item if isinstance(item, str) else item.get("stable_id"))
                    for item in snapshot.get("unresolved_candidates", [])
                    if isinstance(item, (str, Mapping))
                }
                for decision in decisions:
                    stable_id = str(decision["stable_id"])
                    choice = decision["decision"]
                    if choice == "qualifying" and stable_id not in qualifying_ids:
                        raise FinalizationError(
                            f"Manual qualifying decision {stable_id} is not selected"
                        )
                    if choice == "inconclusive" and stable_id not in unresolved_ids:
                        raise FinalizationError(
                            f"Manual inconclusive decision {stable_id} is not retained"
                        )
                    if choice == "rejected" and (
                        stable_id in qualifying_ids or stable_id in unresolved_ids
                    ):
                        raise FinalizationError(
                            f"Manual rejected decision {stable_id} remains selected"
                        )
            if result["outcome"] == "resolved" and snapshot.get("unresolved_candidates"):
                raise FinalizationError(
                    "Resolved job result cannot retain unresolved semantic candidates"
                )
            if result["outcome"] == "inconclusive":
                unresolved = snapshot.get("unresolved_candidates")
                if not isinstance(unresolved, list) or not unresolved:
                    raise FinalizationError(
                        "Inconclusive job result must retain at least one genuinely "
                        "reviewed unresolved candidate"
                    )
                if result.get("reason_code") != "semantic_evidence_inconclusive":
                    raise FinalizationError(
                        "Only evidence-based semantic uncertainty can escalate models; "
                        "unfinished, access-limited, or budget-limited work must remain "
                        "at the same model"
                    )
                for candidate in unresolved:
                    notes = candidate.get("review_notes") if isinstance(candidate, Mapping) else None
                    if not isinstance(notes, str) or not notes.strip():
                        raise FinalizationError(
                            "Every inconclusive job candidate requires non-empty "
                            "review_notes explaining the evidence gap"
                        )


def _attempt_has_result(task: Mapping[str, Any], attempt_id: str, digest: str) -> bool:
    for attempt in task.get("attempts", []):
        if attempt.get("attempt_id") != attempt_id:
            continue
        resolution = attempt.get("resolution")
        return isinstance(resolution, Mapping) and resolution.get("result_digest") == digest
    return False


def _telemetry_task_fields(
    task: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    source = (
        context.get("source_snapshot")
        if isinstance(context.get("source_snapshot"), Mapping)
        else {}
    )
    claim = task.get("claim") if isinstance(task.get("claim"), Mapping) else {}
    worker_id = claim.get("claimed_by")
    if worker_id is None:
        worker_id = next(
            (
                attempt.get("claimed_by")
                for attempt in task.get("attempts", [])
                if isinstance(attempt, Mapping)
                and attempt.get("attempt_id") == result.get("attempt_id")
            ),
            None,
        )
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), Mapping) else {}
    adapter = (
        snapshot.get("adapter_result")
        if isinstance(snapshot.get("adapter_result"), Mapping)
        else {}
    )
    jobs = adapter.get("jobs") if isinstance(adapter.get("jobs"), list) else None
    qualifying = snapshot.get("qualifying_jobs")
    unresolved = snapshot.get("unresolved_candidates")
    return {
        "worker_id": worker_id,
        "lane": (
            "structured"
            if context.get("structured_adapter_available") is True
            else "manual"
            if context.get("structured_adapter_available") is False
            else None
        ),
        "provider": source.get("provider") or adapter.get("provider"),
        "reason_code": result.get("reason_code"),
        "failure_class": result.get("failure_class"),
        "retryable": result.get("retryable"),
        "metrics": {
            "jobs_fetched": len(jobs) if jobs is not None else None,
            "qualifying_jobs": len(qualifying) if isinstance(qualifying, list) else None,
            "unresolved_candidates": len(unresolved) if isinstance(unresolved, list) else None,
        },
    }


def preflight_result(paths: RepoPaths, result_path: Path) -> dict[str, Any]:
    """Validate a claimed result without writing queue or canonical state."""

    result = _read_json(result_path)
    workflow = result.get("workflow")
    if workflow not in {"find_jobs", "validate_companies"}:
        raise FinalizationError("Result preflight requires a supported workflow")
    validate(result, "job-result.schema.json" if workflow == "find_jobs" else "company-result.schema.json", str(result_path))
    engine = _engine(paths, workflow)
    with engine.transaction() as locked:
        task = locked.require_claim(
            result["task_id"],
            result["claim_token"],
            attempt_id=result["attempt_id"],
            expected_model=result["model"],
        )
        snapshot = result.get("snapshot")
        if workflow == "find_jobs" and isinstance(snapshot, Mapping):
            adapter_result = snapshot.get("adapter_result")
            if isinstance(adapter_result, Mapping):
                validate_adapter_result(adapter_result)
        _verify_result_scope(task, result)
    if workflow == "validate_companies":
        return operation_report(
            "validate_companies.preflight-result",
            task={
                "task_id": result["task_id"],
                "attempt_id": result["attempt_id"],
                "model": result["model"],
                "target": result["target"],
                "outcome": result["outcome"],
                "worker_id": task["claim"].get("claimed_by"),
            },
            item_results=result["item_results"],
            message=(
                f"Result {result_path} passed schema, claim-scope, and company patch preflight."
            ),
        )
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), Mapping) else {}
    adapter = (
        snapshot.get("adapter_result")
        if isinstance(snapshot.get("adapter_result"), Mapping)
        else {}
    )
    jobs = adapter.get("jobs") if isinstance(adapter.get("jobs"), list) else None
    return operation_report(
        "find_jobs.preflight-result",
        task={
            "task_id": result["task_id"],
            "attempt_id": result["attempt_id"],
            "model": result["model"],
            "target": result["target"],
            "outcome": result["outcome"],
            "worker_id": task["claim"].get("claimed_by"),
            "lane": (
                "structured"
                if task.get("context", {}).get("structured_adapter_available") is True
                else "manual"
            ),
            "provider": adapter.get("provider"),
            "metrics": {
                "jobs_fetched": len(jobs) if jobs is not None else None,
                "qualifying_jobs": len(snapshot.get("qualifying_jobs", []))
                if isinstance(snapshot.get("qualifying_jobs"), list)
                else None,
                "unresolved_candidates": len(snapshot.get("unresolved_candidates", []))
                if isinstance(snapshot.get("unresolved_candidates"), list)
                else None,
            },
        },
        message=(
            f"Result {result_path} passed schema, claim-scope, adapter identity, "
            "and manual candidate-accounting preflight."
        ),
    )


def preflight_job_result(paths: RepoPaths, result_path: Path) -> dict[str, Any]:
    """Backward-compatible job-only preflight entry point."""
    return preflight_result(paths, result_path)


def _apply_company_result(
    paths: RepoPaths,
    result: Mapping[str, Any],
    result_digest: str,
    transaction_id: str,
    applied_at: str,
) -> tuple[bool, list[str], int, dict[str, Any]]:
    store = JsonStore(paths.companies, validator=validator("companies.schema.json"))
    with store.locked(exclusive=True) as canonical:
        document = canonical.read()
        validate_integrity(document)
        if _ledger_contains(document["application_ledger"], result_digest, result["task_id"]):
            company = next(
                item
                for item in document["companies"]
                if item["company_id"] == result["target"]["company_ids"][0]
            )
            return False, [], company["record_revision"], copy.deepcopy(company)
        changed = False
        descriptions: list[str] = []
        if result["outcome"] in {"resolved", "inconclusive"}:
            changed, descriptions = apply_company_patch(
                document,
                result["patch"],
                applied_at=applied_at,
            )
        document["application_ledger"].append(
            {
                "transaction_id": transaction_id,
                "task_id": result["task_id"],
                "attempt_id": result["attempt_id"],
                "result_digest": result_digest,
                "applied_at": applied_at,
            }
        )
        document["revision"] += 1
        document["updated_at"] = applied_at
        validate_integrity(document)
        canonical.save(document)
        company = next(
            item
            for item in document["companies"]
            if item["company_id"] == result["target"]["company_ids"][0]
        )
        return changed, descriptions, company["record_revision"], copy.deepcopy(company)


def _job_snapshot_from_result(
    result: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    transaction_id: str,
    result_digest: str,
    applied_at: str,
) -> dict[str, Any]:
    incoming = result.get("snapshot")
    if not isinstance(incoming, Mapping):
        raise FinalizationError("Resolved job result requires a snapshot")
    adapter_result = incoming.get("adapter_result")
    if not isinstance(adapter_result, Mapping):
        raise FinalizationError("Resolved job result requires adapter_result")
    fetch = adapter_result.get("fetch")
    if not isinstance(fetch, Mapping) or not fetch.get("complete"):
        raise FinalizationError("A resolved job result must be a complete source enumeration")
    if adapter_result.get("source_id") != incoming["source_id"]:
        raise FinalizationError("Adapter enumeration source does not match result target")
    if adapter_result.get("company_id") != incoming["company_id"]:
        raise FinalizationError("Adapter enumeration company does not match result target")
    document = reconcile_snapshot(
        prior,
        adapter_result,
        scanned_at=incoming["scanned_at"],
        source_revision=incoming["expected_source_revision"],
        profile_id=incoming["profile_id"],
        profile_digest=incoming["profile_digest"],
        qualifying_jobs=incoming["qualifying_jobs"],
        unresolved_candidates=incoming["unresolved_candidates"],
        rejection_counts=incoming["rejection_counts"],
        warnings=incoming["warnings"],
    )
    document["application_ledger"].append(
        {
            "transaction_id": transaction_id,
            "task_id": result["task_id"],
            "attempt_id": result["attempt_id"],
            "result_digest": result_digest,
            "applied_at": applied_at,
        }
    )
    validate(document, "job-snapshot.schema.json", f"Snapshot {document['source_id']}")
    return document


def _apply_job_result(
    paths: RepoPaths,
    result: Mapping[str, Any],
    result_digest: str,
    transaction_id: str,
    applied_at: str,
) -> tuple[bool, list[str]]:
    source_id = result["target"]["source_ids"][0]
    canonical_store = JsonStore(
        paths.companies,
        validator=validator("companies.schema.json"),
    )
    incoming = result.get("snapshot")
    # The job finalizer reads company/source ownership under its canonical
    # shared lock before acquiring the global jobs lock. This preserves the
    # repository lock order: queue -> companies -> jobs.
    with canonical_store.locked(exclusive=False) as canonical_locked:
        canonical = canonical_locked.read()
        sources = source_index(canonical)
        ownership = sources.get(source_id)
        if ownership is None:
            raise FinalizationError(f"Unknown source ID {source_id}")
        company, source = ownership
        if company["company_id"] != result["target"]["company_ids"][0]:
            raise FinalizationError("Job result source is owned by another company")
        if result["outcome"] == "resolved":
            if not isinstance(incoming, Mapping):
                raise FinalizationError("Resolved job result requires snapshot")
            if incoming["expected_source_revision"] != source["record_revision"]:
                raise FinalizationError(
                    f"Source {source_id} revision conflict: expected "
                    f"{incoming['expected_source_revision']}, found {source['record_revision']}"
                )
            if incoming["source_id"] != source_id or incoming["company_id"] != company["company_id"]:
                raise FinalizationError("Job snapshot ownership does not match its claimed target")
        snapshot_path = paths.job_sources / f"{source_id}.json"
        store = JsonStore(
            snapshot_path,
            lock_path=paths.jobs_lock,
            validator=validator("job-snapshot.schema.json"),
        )
        with store.locked(exclusive=True) as locked:
            prior = locked.read() if snapshot_path.exists() else None
            if prior and _ledger_contains(prior["application_ledger"], result_digest, result["task_id"]):
                return False, []
            if result["outcome"] != "resolved":
                return False, []
            document = _job_snapshot_from_result(
                result,
                prior,
                transaction_id,
                result_digest,
                applied_at,
            )
            if prior is None:
                locked.initialize(document)
            else:
                locked.save(document)
            return True, [f"source {source_id}: canonical job snapshot updated"]


def _canonical_has_application(paths: RepoPaths, journal: Mapping[str, Any]) -> bool:
    if journal["workflow"] == "validate_companies":
        document = json.loads(paths.companies.read_text(encoding="utf-8"))
        return _ledger_contains(
            document["application_ledger"], journal["result_digest"], journal["task_id"]
        )
    source_id = journal["target"]["source_ids"][0]
    path = paths.job_sources / f"{source_id}.json"
    if not path.exists():
        return False
    document = json.loads(path.read_text(encoding="utf-8"))
    return _ledger_contains(
        document["application_ledger"], journal["result_digest"], journal["task_id"]
    )


def recover_journal(paths: RepoPaths, workflow: str) -> dict[str, Any] | None:
    """Finish or discard one interrupted coordinated transaction."""

    journal_path = paths.journal_path(workflow)
    if not journal_path.exists():
        return None
    journal = _read_json(journal_path)
    engine = _engine(paths, workflow)
    with engine.transaction() as locked:
        task = locked.find_task(journal["task_id"])
        applied = _canonical_has_application(paths, journal) or (
            journal.get("phase") == "canonical_applied"
            and journal.get("result", {}).get("outcome") != "resolved"
        )
        if task["status"] != "running":
            if applied and task["status"] not in {
                "completed",
                "inconclusive",
                "blocked",
                "failed",
                "open",
            }:
                raise FinalizationError("Journal canonical commit has inconsistent queue state")
            _remove_journal(journal_path)
            return {"recovered": False, "removed_completed_journal": True}
        claim = task.get("claim") or {}
        if claim.get("claim_token") != journal["claim_token"]:
            raise FinalizationError("Interrupted transaction claim token no longer owns its task")
        result = journal["result"]
        _verify_result_scope(task, result)
        if not applied:
            if workflow == "validate_companies":
                (
                    _changed,
                    _changes,
                    follow_up_target_revision,
                    updated_company,
                ) = _apply_company_result(
                    paths,
                    result,
                    journal["result_digest"],
                    journal["transaction_id"],
                    journal["prepared_at"],
                )
                follow_up_context = copy.deepcopy(task["context"])
                follow_up_context["company_snapshot"] = updated_company
                follow_up_context["source_snapshots"] = copy.deepcopy(
                    updated_company["career_sources"]
                )
                journal["follow_up_target_revision"] = follow_up_target_revision
                journal["follow_up_context"] = follow_up_context
            else:
                _apply_job_result(
                    paths,
                    result,
                    journal["result_digest"],
                    journal["transaction_id"],
                    journal["prepared_at"],
                )
            journal["phase"] = "canonical_applied"
            _write_journal(journal_path, journal)
            applied = True
        elif (
            workflow == "validate_companies"
            and result["outcome"] == "inconclusive"
            and journal.get("follow_up_target_revision") is None
        ):
            # Recover the narrow crash window after the canonical replace but
            # before the journal phase/child metadata update.
            canonical = json.loads(paths.companies.read_text(encoding="utf-8"))
            updated_company = next(
                company
                for company in canonical["companies"]
                if company["company_id"] == task["target"]["company_ids"][0]
            )
            follow_up_context = copy.deepcopy(task["context"])
            follow_up_context["company_snapshot"] = copy.deepcopy(updated_company)
            follow_up_context["source_snapshots"] = copy.deepcopy(
                updated_company["career_sources"]
            )
            journal["follow_up_target_revision"] = updated_company["record_revision"]
            journal["follow_up_context"] = follow_up_context
            journal["phase"] = "canonical_applied"
            _write_journal(journal_path, journal)
        # Use the original prepared timestamp, which was verified inside the
        # lease, so recovery does not convert a committed canonical write into
        # an expired-token dead end.
        transition = _queue_resolution(
            engine,
            locked,
            result,
            now=journal["prepared_at"],
            require_unexpired=False,
            result_file=journal.get("result_path"),
            follow_up_target_revision=journal.get("follow_up_target_revision"),
            follow_up_context=journal.get("follow_up_context"),
        )
        _remove_journal(journal_path)
        return {
            "recovered": True,
            "task_id": journal["task_id"],
            "queue_revision": transition.revision,
        }


def finalize_result(paths: RepoPaths, result_path: Path) -> dict[str, Any]:
    result = _read_json(result_path)
    workflow = result.get("workflow")
    if workflow not in {"validate_companies", "find_jobs"}:
        raise FinalizationError(f"Unknown result workflow {workflow!r}")
    schema_name = "company-result.schema.json" if workflow == "validate_companies" else "job-result.schema.json"
    validate(result, schema_name, str(result_path))
    recover_journal(paths, workflow)
    engine = _engine(paths, workflow)
    digest = _digest_result(result)
    transaction_id = f"txn:{digest[:24]}"
    journal_path = paths.journal_path(workflow)
    prepared_at = format_timestamp(utc_now())
    changes: list[str] = []
    canonical_changed = False
    follow_up_target_revision: int | None = None
    follow_up_context: dict[str, Any] | None = None

    with engine.transaction() as locked:
        existing_task = locked.find_task(result["task_id"])
        if _attempt_has_result(existing_task, result["attempt_id"], digest):
            return operation_report(
                f"{workflow}.finalize",
                task={
                    "task_id": result["task_id"],
                    "attempt_id": result["attempt_id"],
                    "model": result["model"],
                    "target": result["target"],
                    "outcome": result["outcome"],
                    "queue_status": existing_task["status"],
                    **_telemetry_task_fields(existing_task, result),
                },
                queue_delta={
                    "final_status": existing_task["status"],
                    "follow_up_task_id": None,
                    "follow_up_model": None,
                    "queue_revision": locked.revision,
                },
                message=f"Task {result['task_id']} result was already finalized; no files changed.",
                generated_at=prepared_at,
            )
        task = locked.require_claim(
            result["task_id"],
            result["claim_token"],
            attempt_id=result["attempt_id"],
            expected_model=result["model"],
            now=prepared_at,
        )
        _verify_result_scope(task, result)
        journal = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "workflow": workflow,
            "task_id": result["task_id"],
            "attempt_id": result["attempt_id"],
            "claim_token": result["claim_token"],
            "target": copy.deepcopy(result["target"]),
            "result_digest": digest,
            "prepared_at": prepared_at,
            "phase": "prepared",
            "result_path": str(result_path.resolve()),
            "follow_up_target_revision": None,
            "follow_up_context": None,
            "result": copy.deepcopy(result),
        }
        _write_journal(journal_path, journal)
        try:
            if workflow == "validate_companies":
                (
                    canonical_changed,
                    changes,
                    follow_up_target_revision,
                    updated_company,
                ) = _apply_company_result(
                    paths, result, digest, transaction_id, prepared_at
                )
                follow_up_context = copy.deepcopy(task["context"])
                follow_up_context["company_snapshot"] = updated_company
                follow_up_context["source_snapshots"] = copy.deepcopy(
                    updated_company["career_sources"]
                )
            else:
                canonical_changed, changes = _apply_job_result(
                    paths, result, digest, transaction_id, prepared_at
                )
            journal["phase"] = "canonical_applied"
            journal["follow_up_target_revision"] = follow_up_target_revision
            journal["follow_up_context"] = follow_up_context
            _write_journal(journal_path, journal)
            transition = _queue_resolution(
                engine,
                locked,
                result,
                now=prepared_at,
                result_file=str(result_path.resolve()),
                follow_up_target_revision=follow_up_target_revision,
                follow_up_context=follow_up_context,
            )
        except BaseException:
            # Keep a journal only when a canonical commit may need its queue
            # half recovered. A validation/revision error before canonical save
            # is safely retryable by the owning task and must not poison every
            # later mutating command with an unrecoverable prepared journal.
            try:
                canonical_applied = _canonical_has_application(paths, journal)
            except Exception:
                # Ambiguous storage failure: preserve the journal for explicit
                # recovery rather than guessing that no commit occurred.
                pass
            else:
                if not canonical_applied:
                    _remove_journal(journal_path)
            raise
        _remove_journal(journal_path)

    task_after = transition.task
    follow_up = getattr(transition, "follow_up_task", None)
    event = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "workflow": workflow,
        "task_id": result["task_id"],
        "attempt_id": result["attempt_id"],
        "model": result["model"],
        "outcome": result["outcome"],
        "queue_status": task_after["status"],
        "canonical_changed": canonical_changed,
        "result_digest": digest,
        "created_at": prepared_at,
    }
    _append_audit(paths, event)
    source_routing: dict[str, Any] | None = None
    post_commit_warnings: list[str] = []
    if workflow == "find_jobs" and result.get("reason_code") in {
        "structured_source_not_found",
        "structured_source_configuration_invalid",
        "manual_source_enumeration_unsupported",
    }:
        try:
            from .maintenance import enqueue_job_source_revalidations

            source_routing = enqueue_job_source_revalidations(paths)
        except Exception as exc:
            post_commit_warnings.append(
                f"Job result committed, but source revalidation routing failed: {exc}"
            )
    try:
        full_progress = status_snapshot(paths)
        progress = {
            "queue": full_progress["queues"][workflow],
            "coverage": full_progress[
                "companies" if workflow == "validate_companies" else "job_coverage"
            ],
        }
    except Exception as exc:  # Reporting must not invalidate a committed result.
        progress = {}
        reporting_warnings = [
            *post_commit_warnings,
            f"Result committed, but progress footer was unavailable: {exc}",
        ]
    else:
        reporting_warnings = post_commit_warnings
    queue_delta = {
        "final_status": task_after["status"],
        "follow_up_task_id": follow_up["task_id"] if follow_up else None,
        "follow_up_model": follow_up["expected_model"] if follow_up else None,
        "queue_revision": transition.revision,
    }
    if result.get("failure_class") in ENVIRONMENT_FAILURE_CLASSES:
        queue_delta.update({"stop_worker": True, "retry_budget_consumed": False})
    if source_routing is not None:
        queue_delta["source_revalidation"] = {
            "created": source_routing["created_count"],
            "augmented": source_routing["augmented_count"],
        }
    return operation_report(
        f"{workflow}.finalize",
        task={
            "task_id": result["task_id"],
            "attempt_id": result["attempt_id"],
            "model": result["model"],
            "target": result["target"],
            "outcome": result["outcome"],
            "queue_status": task_after["status"],
            **_telemetry_task_fields(task_after, result),
        },
        item_results=copy.deepcopy(result.get("item_results", [])),
        changes=changes,
        queue_delta=queue_delta,
        progress=progress,
        warnings=reporting_warnings,
        message=(
            f"Task {result['task_id']} finalized as {task_after['status']}; "
            f"canonical data {'changed' if canonical_changed else 'was unchanged'}."
        ),
        generated_at=prepared_at,
    )
