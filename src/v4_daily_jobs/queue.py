"""Deterministic queue state machine for company and job work.

The engine operates on plain, inspectable JSON documents while centralizing
every mutation.  Selection and lineage are deterministic; only ownership
tokens and attempt IDs are intentionally random.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import secrets
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote
import uuid

from .json_store import JsonObject, JsonStore, LockedJsonStore, SaveResult


MODELS = ("luna", "terra", "sol")
WORKFLOWS = ("validate_companies", "find_jobs")
STATUSES = ("open", "running", "completed", "inconclusive", "blocked", "failed")
TERMINAL_STATUSES = frozenset(("completed", "inconclusive", "blocked", "failed"))
DEFAULT_LEASE_SECONDS = {
    "validate_companies": 120 * 60,
    "find_jobs": 60 * 60,
}
DEFAULT_ACCESS_RETRY_LIMIT = 2
DEFAULT_ACCESS_BACKOFF_SECONDS = (5 * 60, 30 * 60)
DEFAULT_ENVIRONMENT_BACKOFF_SECONDS = 5 * 60
BATCH_LIMITS = {"validate_companies": 75, "find_jobs": 20}
BATCH_STATUSES = ("active", "limit_reached", "closed")
BATCH_CLOSE_REASONS = (
    "completed",
    "queue_empty",
    "candidate_budget",
    "stopped",
    "system_error",
)
REASON_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class QueueError(RuntimeError):
    """Base class for deterministic queue failures."""


class QueueValidationError(QueueError):
    """Raised when a queue or task violates an invariant."""


class TaskNotFound(QueueError):
    """Raised when an operation names a task not present in the queue."""


class ClaimRejected(QueueError):
    """Raised for stale, wrong, expired, or otherwise invalid ownership."""


class InvalidTransition(QueueError):
    """Raised when a requested state transition is not allowed."""


class DedupeConflict(QueueError):
    """Raised when a task ID or dedupe key is reused inconsistently."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str, *, label: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise QueueValidationError(f"{label} must be a UTC ISO-8601 string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise QueueValidationError(f"invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise QueueValidationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)
    return parse_timestamp(value)


def normalize_model(model: str) -> str:
    normalized = model.strip().lower() if isinstance(model, str) else ""
    if normalized not in MODELS:
        raise QueueValidationError(f"model must be one of {', '.join(MODELS)}")
    return normalized


def normalize_workflow(workflow: str) -> str:
    aliases = {
        "company": "validate_companies",
        "companies": "validate_companies",
        "validate-companies": "validate_companies",
        "jobs": "find_jobs",
        "job": "find_jobs",
        "find-jobs": "find_jobs",
    }
    normalized = aliases.get(workflow, workflow)
    if normalized not in WORKFLOWS:
        raise QueueValidationError(f"workflow must be one of {', '.join(WORKFLOWS)}")
    return normalized


def _component(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueueValidationError("stable ID components must be non-empty strings")
    return quote(value.strip(), safe="-._~")


def make_dedupe_key(
    *,
    workflow: str,
    company_id: str,
    source_id: str | None = None,
    source_ids: Sequence[str] | None = None,
    reason_codes: Sequence[str],
    policy_version: str,
    expected_model: str,
    target_revision: int,
) -> str:
    """Construct a stable, readable work identity from normalized components."""

    workflow = normalize_workflow(workflow)
    model = normalize_model(expected_model)
    reasons = sorted(set(reason_codes))
    normalized_source_ids = _normalize_source_ids(source_id=source_id, source_ids=source_ids)
    if not reasons:
        raise QueueValidationError("at least one reason code is required")
    if not isinstance(target_revision, int) or isinstance(target_revision, bool) or target_revision < 1:
        raise QueueValidationError("target_revision must be a positive integer")
    parts = [
        f"workflow={_component(workflow)}",
        f"company={_component(company_id)}",
        "sources="
        + (",".join(_component(item) for item in normalized_source_ids) or "-"),
        f"reasons={','.join(_component(reason) for reason in reasons)}",
        f"policy={_component(policy_version)}",
        f"model={model}",
        f"revision={target_revision}",
    ]
    return "|".join(parts)


def make_task_id(dedupe_key: str, *, workflow: str, target_id: str, model: str) -> str:
    workflow = normalize_workflow(workflow)
    model = normalize_model(model)
    prefix = "company" if workflow == "validate_companies" else "source"
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{_component(target_id)}:{model}:{digest}"


def _normalize_source_ids(
    *,
    source_id: str | None,
    source_ids: Sequence[str] | None,
) -> list[str]:
    if source_id is not None and source_ids is not None:
        raise QueueValidationError("pass source_id or source_ids, not both")
    if isinstance(source_ids, (str, bytes)):
        raise QueueValidationError("source_ids must be an array, not a string")
    values = list(source_ids) if source_ids is not None else ([] if source_id is None else [source_id])
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise QueueValidationError("source IDs must be non-empty strings")
    normalized = sorted(item.strip() for item in values)
    if len(normalized) != len(set(normalized)):
        raise QueueValidationError("source IDs must be unique")
    return normalized


def empty_queue_document() -> JsonObject:
    return {
        "schema_version": 1,
        "revision": 0,
        "updated_at": None,
        "batches": [],
        "tasks": [],
    }


@dataclass(frozen=True)
class EnqueueResult:
    changed: bool
    created: tuple[JsonObject, ...]
    existing: tuple[JsonObject, ...]
    revision: int

    @property
    def task(self) -> JsonObject | None:
        items = self.created or self.existing
        return copy.deepcopy(items[0]) if items else None


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    task: JsonObject | None
    revision: int
    reason: str | None = None
    batch: JsonObject | None = None

    @property
    def claim_token(self) -> str | None:
        if self.task is None or self.task.get("claim") is None:
            return None
        return self.task["claim"].get("claim_token")

    @property
    def attempt_id(self) -> str | None:
        if self.task is None or self.task.get("claim") is None:
            return None
        return self.task["claim"].get("attempt_id")


@dataclass(frozen=True)
class RenewResult:
    task: JsonObject
    revision: int


@dataclass(frozen=True)
class RecoveryResult:
    changed: bool
    recovered_task_ids: tuple[str, ...]
    revision: int


@dataclass(frozen=True)
class TransitionResult:
    task: JsonObject
    follow_up_task: JsonObject | None
    follow_up_created: bool
    revision: int


@dataclass(frozen=True)
class AccessFailureResult:
    task: JsonObject
    retry_scheduled: bool
    blocked: bool
    revision: int


@dataclass(frozen=True)
class RetryResult:
    task: JsonObject
    revision: int


@dataclass(frozen=True)
class BatchResult:
    batch: JsonObject
    revision: int
    changed: bool = True


class LockedQueue:
    """Queue document held under one exclusive lock.

    This is the cross-file finalizer interface.  A caller may verify a claim,
    acquire the canonical database lock, apply its patch, then re-verify and
    save a queue transition without releasing the queue lock.
    """

    def __init__(self, engine: "QueueEngine", store: LockedJsonStore) -> None:
        self.engine = engine
        self.store = store
        self.document = self.store.reload()
        self._base_document = copy.deepcopy(self.document)

    def reload(self) -> JsonObject:
        self.document = self.store.reload()
        self._base_document = copy.deepcopy(self.document)
        return self.document

    @property
    def revision(self) -> int:
        return self.document["revision"]

    def find_task(self, task_id: str) -> JsonObject:
        return self.engine._find_task(self.document, task_id)

    def require_claim(
        self,
        task_id: str,
        claim_token: str,
        *,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        now: datetime | str | None = None,
        require_unexpired: bool = True,
    ) -> JsonObject:
        instant = (
            _as_datetime(now)
            if now is not None
            else _as_datetime(self.engine.now_factory())
        )
        return self.engine._require_claim(
            self.document,
            task_id,
            claim_token,
            attempt_id=attempt_id,
            expected_model=expected_model,
            now=instant,
            require_unexpired=require_unexpired,
        )

    def save(self, *, now: datetime | str | None = None) -> SaveResult:
        """Validate and save one logical queue transaction.

        Revision and root update time are engine-owned.  A semantic no-op does
        not write or increment the revision.
        """

        proposed = copy.deepcopy(self.document)
        proposed["revision"] = self._base_document["revision"]
        proposed["updated_at"] = self._base_document["updated_at"]
        if proposed == self._base_document:
            return SaveResult(False, copy.deepcopy(self._base_document), self.store.digest())
        proposed["revision"] = self._base_document["revision"] + 1
        instant = (
            _as_datetime(now)
            if now is not None
            else _as_datetime(self.engine.now_factory())
        )
        proposed["updated_at"] = format_timestamp(instant)
        self.engine.validate_transition(self._base_document, proposed)
        result = self.store.save(
            proposed,
            expected_revision=self._base_document["revision"],
        )
        self.document = copy.deepcopy(result.document)
        self._base_document = copy.deepcopy(result.document)
        return result


class _QueueContext:
    def __init__(self, engine: "QueueEngine", timeout: float | None) -> None:
        self.engine = engine
        self.timeout = timeout
        self._context: Any | None = None

    def __enter__(self) -> LockedQueue:
        self._context = self.engine.store.locked(exclusive=True, timeout=self.timeout)
        locked_store = self._context.__enter__()
        return LockedQueue(self.engine, locked_store)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self._context is not None
        self._context.__exit__(exc_type, exc, traceback)


class QueueEngine:
    """Safe deterministic operations for one workflow queue."""

    def __init__(
        self,
        path: str | Path,
        workflow: str,
        *,
        lease_seconds: int | None = None,
        validator: Callable[[JsonObject], None] | None = None,
        lock_path: str | Path | None = None,
        lock_timeout: float = 10.0,
        now_factory: Callable[[], datetime] = utc_now,
        token_factory: Callable[[], str] | None = None,
        attempt_id_factory: Callable[[], str] | None = None,
        batch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.workflow = normalize_workflow(workflow)
        self.lease_seconds = (
            DEFAULT_LEASE_SECONDS[self.workflow] if lease_seconds is None else lease_seconds
        )
        if not isinstance(self.lease_seconds, int) or self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        self.external_validator = validator
        self.now_factory = now_factory
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.attempt_id_factory = attempt_id_factory or (lambda: f"attempt:{uuid.uuid4()}")
        self.batch_id_factory = batch_id_factory or (
            lambda: f"batch:{self.workflow}:{uuid.uuid4()}"
        )
        self.store = JsonStore(
            path,
            lock_path=lock_path,
            validator=self.validate_document,
            lock_timeout=lock_timeout,
        )

    def initialize(self) -> SaveResult:
        return self.store.initialize(empty_queue_document())

    def locked(self, *, timeout: float | None = None) -> _QueueContext:
        return _QueueContext(self, timeout)

    # A readable alias at coordinated-finalization call sites.
    transaction = locked

    def read(self) -> JsonObject:
        return self.store.read()

    def build_task(
        self,
        *,
        company_id: str,
        source_id: str | None = None,
        source_ids: Sequence[str] | None = None,
        reason_codes: Sequence[str],
        policy_version: str,
        target_revision: int,
        context: Mapping[str, Any],
        expected_model: str = "luna",
        priority: int = 50,
        available_at: datetime | str | None = None,
        created_at: datetime | str | None = None,
        parent_task_id: str | None = None,
        root_task_id: str | None = None,
        task_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> JsonObject:
        model = normalize_model(expected_model)
        if not isinstance(company_id, str) or not company_id.strip():
            raise QueueValidationError("company_id must be a non-empty string")
        company_id = company_id.strip()
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise QueueValidationError("policy_version must be a non-empty string")
        policy_version = policy_version.strip()
        if isinstance(reason_codes, (str, bytes)):
            raise QueueValidationError("reason_codes must be an array, not a string")
        normalized_source_ids = _normalize_source_ids(
            source_id=source_id,
            source_ids=source_ids,
        )
        if self.workflow == "find_jobs" and len(normalized_source_ids) != 1:
            raise QueueValidationError("job tasks require exactly one source_id")
        reasons = sorted(set(reason_codes))
        key = dedupe_key or make_dedupe_key(
            workflow=self.workflow,
            company_id=company_id,
            source_ids=normalized_source_ids,
            reason_codes=reasons,
            policy_version=policy_version,
            expected_model=model,
            target_revision=target_revision,
        )
        target_id = (
            company_id
            if self.workflow == "validate_companies"
            else normalized_source_ids[0]
        )
        stable_task_id = task_id or make_task_id(
            key,
            workflow=self.workflow,
            target_id=target_id,
            model=model,
        )
        created = (
            _as_datetime(created_at)
            if created_at is not None
            else _as_datetime(self.now_factory())
        )
        available = created if available_at is None else _as_datetime(available_at)
        task: JsonObject = {
            "task_id": stable_task_id,
            "dedupe_key": key,
            "workflow": self.workflow,
            "status": "open",
            "expected_model": model,
            "priority": priority,
            "available_at": format_timestamp(available),
            "created_at": format_timestamp(created),
            "updated_at": format_timestamp(created),
            "target": {
                "company_ids": [company_id],
                "source_ids": normalized_source_ids,
            },
            "reason_codes": reasons,
            "policy_version": policy_version,
            "target_revision": target_revision,
            "context": copy.deepcopy(dict(context)),
            "attempts": [],
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id or stable_task_id,
            "claim": None,
            "resolution": None,
        }
        self.validate_task(task)
        return task

    def enqueue(self, task: Mapping[str, Any], *, now: datetime | str | None = None) -> EnqueueResult:
        return self.enqueue_many((task,), now=now)

    def enqueue_many(
        self,
        tasks: Iterable[Mapping[str, Any]],
        *,
        now: datetime | str | None = None,
    ) -> EnqueueResult:
        candidates = [copy.deepcopy(dict(task)) for task in tasks]
        for candidate in candidates:
            self.validate_task(candidate)
        candidates.sort(key=lambda task: task["task_id"])
        with self.locked() as locked:
            by_id = {task["task_id"]: task for task in locked.document["tasks"]}
            by_key = {task["dedupe_key"]: task for task in locked.document["tasks"]}
            created: list[JsonObject] = []
            existing: list[JsonObject] = []
            for candidate in candidates:
                id_match = by_id.get(candidate["task_id"])
                key_match = by_key.get(candidate["dedupe_key"])
                if id_match is not None or key_match is not None:
                    match = id_match or key_match
                    assert match is not None
                    if (
                        id_match is not None
                        and key_match is not None
                        and id_match is not key_match
                    ):
                        raise DedupeConflict(
                            f"task_id {candidate['task_id']} and dedupe_key "
                            f"{candidate['dedupe_key']} refer to different tasks"
                        )
                    if (
                        match["task_id"] != candidate["task_id"]
                        or match["dedupe_key"] != candidate["dedupe_key"]
                    ):
                        raise DedupeConflict(
                            "a task ID or dedupe key was reused for different work: "
                            f"{candidate['task_id']}"
                        )
                    existing.append(copy.deepcopy(match))
                    continue
                locked.document["tasks"].append(candidate)
                by_id[candidate["task_id"]] = candidate
                by_key[candidate["dedupe_key"]] = candidate
                created.append(copy.deepcopy(candidate))
            if created:
                locked.document["tasks"].sort(
                    key=lambda task: (task["created_at"], task["task_id"])
                )
            save = locked.save(now=now)
            return EnqueueResult(
                save.changed,
                tuple(created),
                tuple(existing),
                save.document["revision"],
            )

    def start_batch(
        self,
        model: str,
        limit: int,
        *,
        now: datetime | str | None = None,
    ) -> BatchResult:
        """Create a workflow-bound claim budget in the same atomic queue."""
        model = normalize_model(model)
        maximum = BATCH_LIMITS[self.workflow]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise QueueValidationError(
                f"{self.workflow} batch limit must be an integer from 1 through {maximum}"
            )
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        batch_id = self.batch_id_factory()
        self._require_batch_workflow(batch_id)
        batch: JsonObject = {
            "batch_id": batch_id,
            "workflow": self.workflow,
            "model": model,
            "limit": limit,
            "claimed_count": 0,
            "task_ids": [],
            "status": "active",
            "close_reason": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self.validate_batch(batch)
        with self.locked() as locked:
            batches = locked.document.setdefault("batches", [])
            if any(item["batch_id"] == batch_id for item in batches):
                raise DedupeConflict(f"duplicate batch_id: {batch_id}")
            batches.append(batch)
            batches.sort(key=lambda item: (item["created_at"], item["batch_id"]))
            save = locked.save(now=instant)
            return BatchResult(copy.deepcopy(batch), save.document["revision"])

    def close_batch(
        self,
        batch_id: str,
        reason: str,
        *,
        now: datetime | str | None = None,
    ) -> BatchResult:
        """Close unused capacity without changing task ownership."""
        self._require_batch_workflow(batch_id)
        if reason not in BATCH_CLOSE_REASONS:
            raise QueueValidationError(
                f"batch close reason must be one of {', '.join(BATCH_CLOSE_REASONS)}"
            )
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        with self.locked() as locked:
            batch = self._find_batch(locked.document, batch_id)
            if batch["status"] == "limit_reached":
                return BatchResult(copy.deepcopy(batch), locked.revision, False)
            if batch["status"] == "closed":
                if batch["close_reason"] != reason:
                    raise InvalidTransition(
                        f"batch {batch_id} is already closed as {batch['close_reason']}"
                    )
                return BatchResult(copy.deepcopy(batch), locked.revision, False)
            batch["status"] = "closed"
            batch["close_reason"] = reason
            batch["updated_at"] = format_timestamp(instant)
            save = locked.save(now=instant)
            return BatchResult(
                copy.deepcopy(self._find_batch(save.document, batch_id)),
                save.document["revision"],
            )

    def claim_next(
        self,
        model: str,
        *,
        claimed_by: str | None = None,
        batch_id: str | None = None,
        lease_seconds: int | None = None,
        now: datetime | str | None = None,
    ) -> ClaimResult:
        model = normalize_model(model)
        if batch_id is not None:
            self._require_batch_workflow(batch_id)
            if claimed_by is not None and claimed_by != batch_id:
                raise QueueValidationError(
                    "claimed_by must be omitted or equal batch_id for a batch claim"
                )
            claimed_by = batch_id
        if claimed_by is not None and (not isinstance(claimed_by, str) or not claimed_by):
            raise QueueValidationError("claimed_by must be null or a non-empty string")
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        with self.locked() as locked:
            batch = None
            if batch_id is not None:
                batch = self._find_batch(locked.document, batch_id)
                if batch["model"] != model:
                    raise ClaimRejected(
                        f"batch {batch_id} is for {batch['model']}, not {model}"
                    )
                if batch["status"] == "closed":
                    raise ClaimRejected(f"batch {batch_id} is closed")
                if batch["claimed_count"] >= batch["limit"]:
                    return ClaimResult(
                        False, None, locked.revision,
                        "batch_limit_reached", copy.deepcopy(batch),
                    )
            due = [
                task
                for task in locked.document["tasks"]
                if task["status"] == "open"
                and task["expected_model"] == model
                and parse_timestamp(task["available_at"], label="available_at") <= instant
            ]
            due.sort(key=lambda task: (-task["priority"], task["created_at"], task["task_id"]))
            if not due:
                # Crucially, do not call save: bytes, revision, and timestamps stay exact.
                return ClaimResult(
                    False, None, locked.revision, "queue_empty", copy.deepcopy(batch)
                )

            task = due[0]
            token = self.token_factory()
            attempt_id = self.attempt_id_factory()
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(attempt_id, str)
                or not attempt_id
            ):
                raise QueueError("claim token and attempt ID factories must return non-empty strings")
            claimed_at = format_timestamp(instant)
            expires_at = format_timestamp(instant + timedelta(seconds=duration))
            claim: JsonObject = {
                "claim_token": token,
                "attempt_id": attempt_id,
                "claimed_at": claimed_at,
                "lease_expires_at": expires_at,
                "requested_model": model,
                "claimed_by": claimed_by,
            }
            attempt: JsonObject = {
                "attempt_id": attempt_id,
                "claim_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "model": model,
                "status": "running",
                "started_at": claimed_at,
                "ended_at": None,
                "lease_expires_at": expires_at,
                "claimed_by": claimed_by,
            }
            task["status"] = "running"
            task["claim"] = claim
            task["resolution"] = None
            task["attempts"].append(attempt)
            task["updated_at"] = claimed_at
            if batch is not None:
                batch["task_ids"].append(task["task_id"])
                batch["claimed_count"] += 1
                batch["updated_at"] = claimed_at
                if batch["claimed_count"] == batch["limit"]:
                    batch["status"] = "limit_reached"
            save = locked.save(now=instant)
            return ClaimResult(
                True, copy.deepcopy(task), save.document["revision"],
                None, copy.deepcopy(batch),
            )

    def renew(
        self,
        task_id: str,
        claim_token: str,
        *,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        lease_seconds: int | None = None,
        now: datetime | str | None = None,
    ) -> RenewResult:
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        with self.locked() as locked:
            task = locked.require_claim(
                task_id,
                claim_token,
                attempt_id=attempt_id,
                expected_model=expected_model,
                now=instant,
            )
            current_expiry = parse_timestamp(
                task["claim"]["lease_expires_at"],
                label="lease_expires_at",
            )
            expires_at = format_timestamp(
                max(current_expiry, instant + timedelta(seconds=duration))
            )
            task["claim"]["lease_expires_at"] = expires_at
            attempt = self._current_attempt(task)
            attempt["lease_expires_at"] = expires_at
            task["updated_at"] = format_timestamp(instant)
            save = locked.save(now=instant)
            return RenewResult(copy.deepcopy(task), save.document["revision"])

    def recover_stale(
        self,
        *,
        task_id: str | None = None,
        now: datetime | str | None = None,
    ) -> RecoveryResult:
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        with self.locked() as locked:
            candidates = [
                task
                for task in locked.document["tasks"]
                if task["status"] == "running"
                and (task_id is None or task["task_id"] == task_id)
                and parse_timestamp(task["claim"]["lease_expires_at"], label="lease_expires_at")
                <= instant
            ]
            candidates.sort(key=lambda task: task["task_id"])
            if task_id is not None and not any(
                task["task_id"] == task_id for task in locked.document["tasks"]
            ):
                raise TaskNotFound(f"task not found: {task_id}")
            recovered: list[str] = []
            for task in candidates:
                attempt = self._current_attempt(task)
                attempt.update(
                    {
                        "status": "abandoned",
                        "ended_at": timestamp,
                        "outcome": "lease_expired",
                    }
                )
                task["status"] = "open"
                task["claim"] = None
                task["resolution"] = None
                task["available_at"] = timestamp
                task["updated_at"] = timestamp
                recovered.append(task["task_id"])
            save = locked.save(now=instant)
            return RecoveryResult(
                save.changed,
                tuple(recovered),
                save.document["revision"],
            )

    def resolve(
        self,
        task_id: str,
        claim_token: str,
        outcome: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        now: datetime | str | None = None,
        escalate_inconclusive: bool = True,
        follow_up_target_revision: int | None = None,
        follow_up_context: Mapping[str, Any] | None = None,
        require_unexpired: bool = True,
    ) -> TransitionResult:
        with self.locked() as locked:
            return self.resolve_locked(
                locked,
                task_id,
                claim_token,
                outcome,
                resolution=resolution,
                attempt_id=attempt_id,
                expected_model=expected_model,
                now=now,
                escalate_inconclusive=escalate_inconclusive,
                follow_up_target_revision=follow_up_target_revision,
                follow_up_context=follow_up_context,
                require_unexpired=require_unexpired,
            )

    # Compatibility name for callers that describe queue-only terminal updates.
    transition = resolve

    def resolve_locked(
        self,
        locked: LockedQueue,
        task_id: str,
        claim_token: str,
        outcome: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        now: datetime | str | None = None,
        escalate_inconclusive: bool = True,
        follow_up_target_revision: int | None = None,
        follow_up_context: Mapping[str, Any] | None = None,
        require_unexpired: bool = True,
    ) -> TransitionResult:
        """Re-verify and commit a terminal state while retaining a queue lock."""

        if locked.engine is not self:
            raise QueueError("locked queue belongs to a different engine")
        if outcome not in TERMINAL_STATUSES:
            raise InvalidTransition(
                f"running tasks may resolve only to {', '.join(sorted(TERMINAL_STATUSES))}"
            )
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        task = locked.require_claim(
            task_id,
            claim_token,
            attempt_id=attempt_id,
            expected_model=expected_model,
            now=instant,
            require_unexpired=require_unexpired,
        )
        claim = copy.deepcopy(task["claim"])
        final_resolution = copy.deepcopy(dict(resolution or {}))
        final_resolution.update(
            {
                "outcome": outcome,
                "resolved_at": timestamp,
                "attempt_id": claim["attempt_id"],
            }
        )
        task["status"] = outcome
        task["claim"] = None
        task["resolution"] = final_resolution
        task["updated_at"] = timestamp
        attempt = self._current_attempt(task, claim["attempt_id"])
        attempt.update(
            {
                "status": outcome,
                "ended_at": timestamp,
                "outcome": outcome,
                "resolution": copy.deepcopy(final_resolution),
            }
        )

        follow_up: JsonObject | None = None
        follow_up_created = False
        if outcome == "inconclusive" and escalate_inconclusive:
            model = task["expected_model"]
            if model == "sol":
                task["resolution"]["human_review_required"] = True
                attempt["resolution"]["human_review_required"] = True
            else:
                next_model = "terra" if model == "luna" else "sol"
                follow_up = self._build_escalation_child(
                    task,
                    next_model,
                    instant,
                    target_revision=follow_up_target_revision,
                    context=follow_up_context,
                )
                existing = self._find_by_dedupe_key(
                    locked.document,
                    follow_up["dedupe_key"],
                )
                if existing is None:
                    locked.document["tasks"].append(follow_up)
                    locked.document["tasks"].sort(
                        key=lambda item: (item["created_at"], item["task_id"])
                    )
                    follow_up_created = True
                else:
                    follow_up = existing
                task["resolution"]["follow_up_task_id"] = follow_up["task_id"]
                task["resolution"]["follow_up_model"] = next_model
                task["resolution"]["follow_up_created"] = follow_up_created
                attempt["resolution"] = copy.deepcopy(task["resolution"])

        save = locked.save(now=instant)
        saved_task = self._find_task(save.document, task_id)
        saved_follow_up = (
            self._find_by_dedupe_key(save.document, follow_up["dedupe_key"])
            if follow_up is not None
            else None
        )
        return TransitionResult(
            copy.deepcopy(saved_task),
            copy.deepcopy(saved_follow_up) if saved_follow_up is not None else None,
            follow_up_created,
            save.document["revision"],
        )

    def handle_access_failure(
        self,
        task_id: str,
        claim_token: str,
        error_class: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        retry_limit: int = DEFAULT_ACCESS_RETRY_LIMIT,
        backoff_seconds: Sequence[int] = DEFAULT_ACCESS_BACKOFF_SECONDS,
        now: datetime | str | None = None,
        require_unexpired: bool = True,
    ) -> AccessFailureResult:
        with self.locked() as locked:
            return self.handle_access_failure_locked(
                locked,
                task_id,
                claim_token,
                error_class,
                resolution=resolution,
                attempt_id=attempt_id,
                expected_model=expected_model,
                retry_limit=retry_limit,
                backoff_seconds=backoff_seconds,
                now=now,
                require_unexpired=require_unexpired,
            )

    def release_environment_failure(
        self,
        task_id: str,
        claim_token: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        backoff_seconds: int = DEFAULT_ENVIRONMENT_BACKOFF_SECONDS,
        now: datetime | str | None = None,
        require_unexpired: bool = True,
    ) -> RetryResult:
        """Release a claim after a local environment outage without charging
        the target source's access-retry allowance.
        """

        with self.locked() as locked:
            return self.release_environment_failure_locked(
                locked,
                task_id,
                claim_token,
                resolution=resolution,
                attempt_id=attempt_id,
                expected_model=expected_model,
                backoff_seconds=backoff_seconds,
                now=now,
                require_unexpired=require_unexpired,
            )

    def release_environment_failure_locked(
        self,
        locked: LockedQueue,
        task_id: str,
        claim_token: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        backoff_seconds: int = DEFAULT_ENVIRONMENT_BACKOFF_SECONDS,
        now: datetime | str | None = None,
        require_unexpired: bool = True,
    ) -> RetryResult:
        if locked.engine is not self:
            raise QueueError("locked queue belongs to a different engine")
        if backoff_seconds < 0:
            raise ValueError("backoff seconds must be non-negative")
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        task = locked.require_claim(
            task_id,
            claim_token,
            attempt_id=attempt_id,
            expected_model=expected_model,
            now=instant,
            require_unexpired=require_unexpired,
        )
        claim = copy.deepcopy(task["claim"])
        attempt = self._current_attempt(task, claim["attempt_id"])
        details = copy.deepcopy(dict(resolution or {}))
        details.update(
            {
                "outcome": "environment_failure",
                "error_class": "environment_network",
                "retry_budget_consumed": False,
                "resolved_at": timestamp,
                "retry_at": format_timestamp(
                    instant + timedelta(seconds=backoff_seconds)
                ),
            }
        )
        attempt.update(
            {
                "ended_at": timestamp,
                "status": "retry_scheduled",
                "outcome": "environment_failure",
                "error_class": "environment_network",
                "resolution": copy.deepcopy(details),
            }
        )
        task["claim"] = None
        task["status"] = "open"
        task["available_at"] = details["retry_at"]
        task["resolution"] = None
        task["updated_at"] = timestamp
        save = locked.save(now=instant)
        saved_task = self._find_task(save.document, task_id)
        return RetryResult(copy.deepcopy(saved_task), save.document["revision"])

    def handle_access_failure_locked(
        self,
        locked: LockedQueue,
        task_id: str,
        claim_token: str,
        error_class: str,
        *,
        resolution: Mapping[str, Any] | None = None,
        attempt_id: str | None = None,
        expected_model: str | None = None,
        retry_limit: int = DEFAULT_ACCESS_RETRY_LIMIT,
        backoff_seconds: Sequence[int] = DEFAULT_ACCESS_BACKOFF_SECONDS,
        now: datetime | str | None = None,
        require_unexpired: bool = True,
    ) -> AccessFailureResult:
        if locked.engine is not self:
            raise QueueError("locked queue belongs to a different engine")
        if retry_limit < 0:
            raise ValueError("retry_limit must be non-negative")
        allowed_classes = {
            "network",
            "authentication",
            "rate_limit",
            "robots",
            "source_access",
            "timeout",
        }
        if error_class not in allowed_classes:
            raise QueueValidationError(
                f"access error_class must be one of {', '.join(sorted(allowed_classes))}"
            )
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        task = locked.require_claim(
            task_id,
            claim_token,
            attempt_id=attempt_id,
            expected_model=expected_model,
            now=instant,
            require_unexpired=require_unexpired,
        )
        claim = copy.deepcopy(task["claim"])
        prior_failures = sum(
            1 for attempt in task["attempts"] if attempt.get("outcome") == "access_failure"
        )
        failure_number = prior_failures + 1
        attempt = self._current_attempt(task, claim["attempt_id"])
        details = copy.deepcopy(dict(resolution or {}))
        details.update(
            {
                "outcome": "access_failure",
                "error_class": error_class,
                "failure_number": failure_number,
                "resolved_at": timestamp,
            }
        )
        attempt.update(
            {
                "ended_at": timestamp,
                "outcome": "access_failure",
                "error_class": error_class,
                "resolution": copy.deepcopy(details),
            }
        )
        task["claim"] = None
        task["updated_at"] = timestamp
        if prior_failures < retry_limit:
            delay_index = min(prior_failures, max(0, len(backoff_seconds) - 1))
            delay = backoff_seconds[delay_index] if backoff_seconds else 0
            if delay < 0:
                raise ValueError("backoff seconds must be non-negative")
            task["status"] = "open"
            task["resolution"] = None
            task["available_at"] = format_timestamp(instant + timedelta(seconds=delay))
            attempt["status"] = "retry_scheduled"
            details["retry_at"] = task["available_at"]
            attempt["resolution"] = copy.deepcopy(details)
            retry_scheduled = True
            blocked = False
        else:
            task["status"] = "blocked"
            details["human_action_required"] = True
            task["resolution"] = details
            attempt["status"] = "blocked"
            attempt["resolution"] = copy.deepcopy(details)
            retry_scheduled = False
            blocked = True
        save = locked.save(now=instant)
        saved_task = self._find_task(save.document, task_id)
        return AccessFailureResult(
            copy.deepcopy(saved_task),
            retry_scheduled,
            blocked,
            save.document["revision"],
        )

    def retry(
        self,
        task_id: str,
        *,
        available_at: datetime | str | None = None,
        reason: str = "operator_retry",
        now: datetime | str | None = None,
    ) -> RetryResult:
        instant = _as_datetime(now) if now is not None else _as_datetime(self.now_factory())
        timestamp = format_timestamp(instant)
        due = instant if available_at is None else _as_datetime(available_at)
        with self.locked() as locked:
            task = self._find_task(locked.document, task_id)
            if task["status"] not in ("blocked", "failed"):
                raise InvalidTransition(
                    f"explicit retry requires blocked or failed, found {task['status']}"
                )
            prior = copy.deepcopy(task["resolution"])
            history = task["context"].setdefault("prior_resolutions", [])
            if not isinstance(history, list):
                raise QueueValidationError("context prior_resolutions must be an array")
            history.append({"retried_at": timestamp, "reason": reason, "resolution": prior})
            task["status"] = "open"
            task["claim"] = None
            task["resolution"] = None
            task["available_at"] = format_timestamp(due)
            task["updated_at"] = timestamp
            save = locked.save(now=instant)
            saved = self._find_task(save.document, task_id)
            return RetryResult(copy.deepcopy(saved), save.document["revision"])

    def inspect(
        self,
        *,
        status: str | None = None,
        model: str | None = None,
        due_at: datetime | str | None = None,
    ) -> tuple[JsonObject, ...]:
        if status is not None and status not in STATUSES:
            raise QueueValidationError(f"unknown queue status: {status}")
        normalized_model = normalize_model(model) if model is not None else None
        instant = _as_datetime(due_at) if due_at is not None else None
        document = self.read()
        tasks = [
            task
            for task in document["tasks"]
            if (status is None or task["status"] == status)
            and (normalized_model is None or task["expected_model"] == normalized_model)
            and (
                instant is None
                or parse_timestamp(task["available_at"], label="available_at") <= instant
            )
        ]
        tasks.sort(key=lambda task: (-task["priority"], task["created_at"], task["task_id"]))
        return tuple(copy.deepcopy(tasks))

    def validate_document(self, document: JsonObject) -> None:
        if not isinstance(document, dict):
            raise QueueValidationError("queue document must be an object")
        required = {"schema_version", "revision", "updated_at", "tasks"}
        missing = required - set(document)
        if missing:
            raise QueueValidationError(f"queue document missing fields: {sorted(missing)}")
        extra = set(document) - required - {"batches"}
        if extra:
            raise QueueValidationError(f"queue document has unknown fields: {sorted(extra)}")
        if document["schema_version"] != 1:
            raise QueueValidationError("queue schema_version must be 1")
        revision = document["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise QueueValidationError("queue revision must be a non-negative integer")
        if document["updated_at"] is not None:
            parse_timestamp(document["updated_at"], label="queue updated_at")
        if not isinstance(document["tasks"], list):
            raise QueueValidationError("queue tasks must be an array")
        batches = document.get("batches", [])
        if not isinstance(batches, list):
            raise QueueValidationError("queue batches must be an array")
        batch_ids: set[str] = set()
        for batch in batches:
            self.validate_batch(batch)
            if batch["batch_id"] in batch_ids:
                raise QueueValidationError(f"duplicate batch_id: {batch['batch_id']}")
            batch_ids.add(batch["batch_id"])
        task_ids: set[str] = set()
        dedupe_keys: set[str] = set()
        tasks_by_id: dict[str, JsonObject] = {}
        for task in document["tasks"]:
            self.validate_task(task)
            if task["task_id"] in task_ids:
                raise QueueValidationError(f"duplicate task_id: {task['task_id']}")
            if task["dedupe_key"] in dedupe_keys:
                raise QueueValidationError(f"duplicate dedupe_key: {task['dedupe_key']}")
            task_ids.add(task["task_id"])
            dedupe_keys.add(task["dedupe_key"])
            tasks_by_id[task["task_id"]] = task
        for task in document["tasks"]:
            parent_id = task["parent_task_id"]
            if parent_id is not None and parent_id not in task_ids:
                raise QueueValidationError(
                    f"task {task['task_id']} references missing parent {parent_id}"
                )
            if task["root_task_id"] not in task_ids:
                raise QueueValidationError(
                    f"task {task['task_id']} references missing root {task['root_task_id']}"
                )
            root = tasks_by_id[task["root_task_id"]]
            if root["parent_task_id"] is not None or root["root_task_id"] != root["task_id"]:
                raise QueueValidationError(
                    f"task {task['task_id']} has an invalid root lineage"
                )
            if parent_id is None:
                if task["root_task_id"] != task["task_id"]:
                    raise QueueValidationError(
                        f"root task {task['task_id']} must refer to itself"
                    )
            else:
                parent = tasks_by_id[parent_id]
                if parent["root_task_id"] != task["root_task_id"]:
                    raise QueueValidationError(
                        f"task {task['task_id']} and parent do not share a root"
                    )
                if parent["target"] != task["target"]:
                    raise QueueValidationError(
                        f"task {task['task_id']} and parent do not share an exact target"
                    )
                expected_child_model = {"luna": "terra", "terra": "sol"}.get(
                    parent["expected_model"]
                )
                if task["expected_model"] != expected_child_model:
                    raise QueueValidationError(
                        f"task {task['task_id']} has invalid model escalation lineage"
                    )
        for batch in batches:
            for task_id in batch["task_ids"]:
                if task_id not in tasks_by_id:
                    raise QueueValidationError(
                        f"batch {batch['batch_id']} references missing task {task_id}"
                    )
                if not any(
                    attempt.get("claimed_by") == batch["batch_id"]
                    for attempt in tasks_by_id[task_id]["attempts"]
                ):
                    raise QueueValidationError(
                        f"batch {batch['batch_id']} does not own an attempt for {task_id}"
                    )
        if self.external_validator is not None:
            self.external_validator(document)

    def validate_batch(self, batch: JsonObject) -> None:
        if not isinstance(batch, dict):
            raise QueueValidationError("batch session must be an object")
        required = {
            "batch_id", "workflow", "model", "limit", "claimed_count",
            "task_ids", "status", "close_reason", "created_at", "updated_at",
        }
        if set(batch) != required:
            raise QueueValidationError(f"batch fields must be exactly {sorted(required)}")
        self._require_batch_workflow(batch["batch_id"])
        if batch["workflow"] != self.workflow:
            raise QueueValidationError("batch workflow does not match its queue")
        normalize_model(batch["model"])
        maximum = BATCH_LIMITS[self.workflow]
        limit = batch["limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise QueueValidationError(
                f"batch limit must be an integer from 1 through {maximum}"
            )
        count = batch["claimed_count"]
        task_ids = batch["task_ids"]
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= limit:
            raise QueueValidationError("batch claimed_count is outside its limit")
        if (
            not isinstance(task_ids, list)
            or not all(isinstance(item, str) and item for item in task_ids)
            or len(task_ids) != len(set(task_ids))
            or len(task_ids) != count
        ):
            raise QueueValidationError("batch task_ids must be unique and match claimed_count")
        if batch["status"] not in BATCH_STATUSES:
            raise QueueValidationError("batch has an invalid status")
        if batch["status"] == "limit_reached" and count != limit:
            raise QueueValidationError("limit_reached batch must consume its full limit")
        if count == limit and batch["status"] == "active":
            raise QueueValidationError("a full batch cannot remain active")
        if batch["status"] == "closed":
            if batch["close_reason"] not in BATCH_CLOSE_REASONS:
                raise QueueValidationError("closed batch requires a valid close_reason")
        elif batch["close_reason"] is not None:
            raise QueueValidationError("open batch cannot have a close_reason")
        parse_timestamp(batch["created_at"], label="batch created_at")
        parse_timestamp(batch["updated_at"], label="batch updated_at")

    def validate_task(self, task: JsonObject) -> None:
        if not isinstance(task, dict):
            raise QueueValidationError("queue task must be an object")
        required = {
            "task_id",
            "dedupe_key",
            "workflow",
            "status",
            "expected_model",
            "priority",
            "available_at",
            "created_at",
            "updated_at",
            "target",
            "reason_codes",
            "policy_version",
            "target_revision",
            "context",
            "attempts",
            "parent_task_id",
            "root_task_id",
            "claim",
            "resolution",
        }
        missing = required - set(task)
        if missing:
            raise QueueValidationError(f"task missing fields: {sorted(missing)}")
        extra = set(task) - required
        if extra:
            raise QueueValidationError(f"task has unknown fields: {sorted(extra)}")
        for field in ("task_id", "dedupe_key", "policy_version", "root_task_id"):
            if not isinstance(task[field], str) or not task[field]:
                raise QueueValidationError(f"task {field} must be a non-empty string")
        if task["workflow"] != self.workflow:
            raise QueueValidationError(
                f"task workflow {task['workflow']!r} does not belong in {self.workflow!r} queue"
            )
        if task["status"] not in STATUSES:
            raise QueueValidationError(f"invalid task status: {task['status']!r}")
        normalize_model(task["expected_model"])
        if not isinstance(task["priority"], int) or isinstance(task["priority"], bool):
            raise QueueValidationError("task priority must be an integer")
        for field in ("available_at", "created_at", "updated_at"):
            parse_timestamp(task[field], label=f"task {field}")
        target = task["target"]
        if not isinstance(target, dict):
            raise QueueValidationError("task target must be an object")
        if set(target) != {"company_ids", "source_ids"}:
            raise QueueValidationError(
                "task target must contain only company_ids and source_ids"
            )
        company_ids = target.get("company_ids")
        source_ids = target.get("source_ids")
        if not isinstance(company_ids, list) or len(company_ids) != 1:
            raise QueueValidationError("every task must target exactly one company_id")
        if not all(isinstance(item, str) and item for item in company_ids):
            raise QueueValidationError("target company IDs must be non-empty strings")
        if any(item != item.strip() for item in company_ids):
            raise QueueValidationError("target company IDs cannot have surrounding whitespace")
        if not isinstance(source_ids, list):
            raise QueueValidationError("task source_ids must be an array")
        if not all(isinstance(item, str) and item for item in source_ids):
            raise QueueValidationError("target source IDs must be non-empty strings")
        if any(item != item.strip() for item in source_ids):
            raise QueueValidationError("target source IDs cannot have surrounding whitespace")
        if source_ids != sorted(set(source_ids)):
            raise QueueValidationError("target source IDs must be unique and sorted")
        if self.workflow == "find_jobs" and len(source_ids) != 1:
            raise QueueValidationError("job tasks must target exactly one source_id")
        reasons = task["reason_codes"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(item, str) and item for item in reasons)
            or reasons != sorted(set(reasons))
            or not all(REASON_CODE_PATTERN.fullmatch(item) for item in reasons)
        ):
            raise QueueValidationError(
                "task reason_codes must be non-empty, unique, sorted snake_case values"
            )
        target_revision = task["target_revision"]
        if (
            not isinstance(target_revision, int)
            or isinstance(target_revision, bool)
            or target_revision < 1
        ):
            raise QueueValidationError("task target_revision must be a positive integer")
        if not isinstance(task["context"], dict):
            raise QueueValidationError("task context must be an object")
        if not isinstance(task["attempts"], list) or not all(
            isinstance(attempt, dict) for attempt in task["attempts"]
        ):
            raise QueueValidationError("task attempts must be an array of objects")
        attempt_ids = [attempt.get("attempt_id") for attempt in task["attempts"]]
        if any(not isinstance(item, str) or not item for item in attempt_ids):
            raise QueueValidationError("every attempt must have a non-empty attempt_id")
        if len(attempt_ids) != len(set(attempt_ids)):
            raise QueueValidationError("attempt IDs must be unique within a task")
        attempt_statuses = {
            "running",
            "abandoned",
            "completed",
            "inconclusive",
            "blocked",
            "failed",
            "retry_scheduled",
        }
        for attempt in task["attempts"]:
            required_attempt = {
                "attempt_id",
                "claim_token_sha256",
                "model",
                "status",
                "started_at",
                "ended_at",
                "lease_expires_at",
                "claimed_by",
            }
            if required_attempt - set(attempt):
                raise QueueValidationError("attempt history entry is incomplete")
            digest = attempt["claim_token_sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise QueueValidationError("attempt claim token digest must be SHA-256 hex")
            if attempt["model"] != task["expected_model"]:
                raise QueueValidationError("attempt model must match task expected_model")
            if attempt["status"] not in attempt_statuses:
                raise QueueValidationError("attempt has an invalid status")
            parse_timestamp(attempt["started_at"], label="attempt started_at")
            parse_timestamp(attempt["lease_expires_at"], label="attempt lease_expires_at")
            if attempt["ended_at"] is not None:
                parse_timestamp(attempt["ended_at"], label="attempt ended_at")
            if attempt["status"] == "running" and attempt["ended_at"] is not None:
                raise QueueValidationError("running attempt cannot have ended_at")
            if attempt["status"] != "running" and attempt["ended_at"] is None:
                raise QueueValidationError("finished attempt requires ended_at")
            if attempt["claimed_by"] is not None and (
                not isinstance(attempt["claimed_by"], str) or not attempt["claimed_by"]
            ):
                raise QueueValidationError("attempt claimed_by must be null or non-empty string")
        if task["parent_task_id"] is not None and (
            not isinstance(task["parent_task_id"], str) or not task["parent_task_id"]
        ):
            raise QueueValidationError("parent_task_id must be null or a non-empty string")

        if task["status"] == "running":
            claim = task["claim"]
            if not isinstance(claim, dict):
                raise QueueValidationError("running task must have a claim")
            claim_fields = {
                "claim_token",
                "attempt_id",
                "claimed_at",
                "lease_expires_at",
                "requested_model",
                "claimed_by",
            }
            if claim_fields - set(claim):
                raise QueueValidationError("running task claim is incomplete")
            if not isinstance(claim["claim_token"], str) or not claim["claim_token"]:
                raise QueueValidationError("claim_token must be a non-empty string")
            if not isinstance(claim["attempt_id"], str) or not claim["attempt_id"]:
                raise QueueValidationError("attempt_id must be a non-empty string")
            if claim["claimed_by"] is not None and (
                not isinstance(claim["claimed_by"], str) or not claim["claimed_by"]
            ):
                raise QueueValidationError("claim claimed_by must be null or non-empty string")
            parse_timestamp(claim["claimed_at"], label="claim claimed_at")
            parse_timestamp(claim["lease_expires_at"], label="claim lease_expires_at")
            if claim["requested_model"] != task["expected_model"]:
                raise QueueValidationError("claim model must match task expected_model")
            matching = [
                attempt
                for attempt in task["attempts"]
                if attempt.get("attempt_id") == claim["attempt_id"]
            ]
            if len(matching) != 1 or matching[0].get("status") != "running":
                raise QueueValidationError("claim must identify exactly one running attempt")
            if matching[0]["lease_expires_at"] != claim["lease_expires_at"]:
                raise QueueValidationError("claim and running attempt leases must match")
            if matching[0]["claimed_by"] != claim["claimed_by"]:
                raise QueueValidationError("claim and running attempt owner must match")
            if matching[0]["claim_token_sha256"] != hashlib.sha256(
                claim["claim_token"].encode("utf-8")
            ).hexdigest():
                raise QueueValidationError("claim token does not match attempt ownership digest")
            if task["resolution"] is not None:
                raise QueueValidationError("running task cannot have a resolution")
        else:
            if task["claim"] is not None:
                raise QueueValidationError("non-running task cannot retain a claim")
            if task["status"] == "open" and task["resolution"] is not None:
                raise QueueValidationError("open task cannot have a resolution")
            if task["status"] in TERMINAL_STATUSES and not isinstance(task["resolution"], dict):
                raise QueueValidationError("terminal task must have a resolution object")

    def validate_transition(self, before: JsonObject, after: JsonObject) -> None:
        """Reject state changes that bypass the documented transition table."""

        before_batches = {item["batch_id"]: item for item in before.get("batches", [])}
        after_batches = {item["batch_id"]: item for item in after.get("batches", [])}
        removed_batches = set(before_batches) - set(after_batches)
        if removed_batches:
            raise InvalidTransition(
                f"queue transactions cannot remove batches: {sorted(removed_batches)}"
            )
        for batch_id, old in before_batches.items():
            new = after_batches[batch_id]
            for field in ("batch_id", "workflow", "model", "limit", "created_at"):
                if old[field] != new[field]:
                    raise InvalidTransition(
                        f"batch {batch_id} cannot change immutable field {field}"
                    )
            if new["task_ids"][: len(old["task_ids"])] != old["task_ids"]:
                raise InvalidTransition(f"batch {batch_id} cannot remove or reorder task IDs")
            added = len(new["task_ids"]) - len(old["task_ids"])
            if added not in (0, 1):
                raise InvalidTransition(f"batch {batch_id} may claim at most one task per transaction")
            if new["claimed_count"] != old["claimed_count"] + added:
                raise InvalidTransition(f"batch {batch_id} claim count must match its task IDs")
            allowed = {
                "active": {"active", "limit_reached", "closed"},
                "limit_reached": {"limit_reached"},
                "closed": {"closed"},
            }
            if new["status"] not in allowed[old["status"]]:
                raise InvalidTransition(
                    f"forbidden batch transition for {batch_id}: {old['status']} -> {new['status']}"
                )
            if old["status"] != "active" and old != new:
                raise InvalidTransition(f"terminal batch {batch_id} cannot be edited")
            if old["status"] == new["status"] == "active" and added == 0 and old != new:
                raise InvalidTransition(
                    f"active batch {batch_id} can change only when claiming a task"
                )
        for batch_id in set(after_batches) - set(before_batches):
            batch = after_batches[batch_id]
            if batch["status"] != "active" or batch["claimed_count"] != 0 or batch["task_ids"]:
                raise InvalidTransition(f"new batch {batch_id} must start empty and active")

        before_by_id = {task["task_id"]: task for task in before["tasks"]}
        after_by_id = {task["task_id"]: task for task in after["tasks"]}
        removed = set(before_by_id) - set(after_by_id)
        if removed:
            raise InvalidTransition(f"queue transactions cannot remove tasks: {sorted(removed)}")
        immutable_fields = (
            "task_id",
            "dedupe_key",
            "workflow",
            "expected_model",
            "created_at",
            "target",
            "reason_codes",
            "policy_version",
            "target_revision",
            "parent_task_id",
            "root_task_id",
        )
        allowed = {
            "open": {"open", "running"},
            "running": {"running", "open", "completed", "inconclusive", "blocked", "failed"},
            "blocked": {"blocked", "open"},
            "failed": {"failed", "open"},
            "completed": {"completed"},
            "inconclusive": {"inconclusive"},
        }
        for task_id, old in before_by_id.items():
            new = after_by_id[task_id]
            if new["status"] not in allowed[old["status"]]:
                raise InvalidTransition(
                    f"forbidden transition for {task_id}: {old['status']} -> {new['status']}"
                )
            for field in immutable_fields:
                if old[field] != new[field]:
                    raise InvalidTransition(f"task {task_id} cannot change immutable field {field}")
            if old["status"] == new["status"] and old["status"] != "running" and old != new:
                raise InvalidTransition(
                    f"task {task_id} cannot be edited while remaining {old['status']}"
                )
            if old["status"] == new["status"] == "running":
                # Renewal may change only the task timestamp and the mirrored
                # lease expiry in the active claim/attempt.
                old_normalized = copy.deepcopy(old)
                new_normalized = copy.deepcopy(new)
                new_normalized["updated_at"] = old_normalized["updated_at"]
                new_normalized["claim"]["lease_expires_at"] = old_normalized["claim"][
                    "lease_expires_at"
                ]
                active_attempt_id = old_normalized["claim"]["attempt_id"]
                old_attempt = next(
                    item
                    for item in old_normalized["attempts"]
                    if item["attempt_id"] == active_attempt_id
                )
                new_attempt = next(
                    item
                    for item in new_normalized["attempts"]
                    if item["attempt_id"] == active_attempt_id
                )
                new_attempt["lease_expires_at"] = old_attempt["lease_expires_at"]
                if new_normalized != old_normalized:
                    raise InvalidTransition(
                        f"running task {task_id} may change only its lease expiry"
                    )
        for task_id in set(after_by_id) - set(before_by_id):
            if after_by_id[task_id]["status"] != "open":
                raise InvalidTransition(f"new task {task_id} must start open")

    @staticmethod
    def _find_task(document: JsonObject, task_id: str) -> JsonObject:
        for task in document["tasks"]:
            if task["task_id"] == task_id:
                return task
        raise TaskNotFound(f"task not found: {task_id}")

    def _require_batch_workflow(self, batch_id: str) -> None:
        if not isinstance(batch_id, str) or not batch_id:
            raise QueueValidationError("batch_id must be a non-empty string")
        prefix = f"batch:{self.workflow}:"
        if not batch_id.startswith(prefix) or len(batch_id) == len(prefix):
            raise ClaimRejected(f"batch {batch_id!r} does not belong to {self.workflow}")

    @staticmethod
    def _find_batch(document: JsonObject, batch_id: str) -> JsonObject:
        for batch in document.get("batches", []):
            if batch["batch_id"] == batch_id:
                return batch
        raise TaskNotFound(f"batch not found: {batch_id}")

    @staticmethod
    def _find_by_dedupe_key(document: JsonObject, dedupe_key: str) -> JsonObject | None:
        for task in document["tasks"]:
            if task["dedupe_key"] == dedupe_key:
                return task
        return None

    def _require_claim(
        self,
        document: JsonObject,
        task_id: str,
        claim_token: str,
        *,
        attempt_id: str | None,
        expected_model: str | None,
        now: datetime,
        require_unexpired: bool,
    ) -> JsonObject:
        task = self._find_task(document, task_id)
        if task["status"] != "running" or not isinstance(task["claim"], dict):
            raise ClaimRejected(f"task is not currently running: {task_id}")
        claim = task["claim"]
        if not secrets.compare_digest(str(claim["claim_token"]), str(claim_token)):
            raise ClaimRejected(f"claim token is stale or invalid for task {task_id}")
        if attempt_id is not None and claim["attempt_id"] != attempt_id:
            raise ClaimRejected(f"attempt ID does not own task {task_id}")
        if expected_model is not None and task["expected_model"] != normalize_model(expected_model):
            raise ClaimRejected(f"requested model does not match task {task_id}")
        if require_unexpired and parse_timestamp(
            claim["lease_expires_at"], label="lease_expires_at"
        ) <= now:
            raise ClaimRejected(f"lease has expired for task {task_id}")
        return task

    @staticmethod
    def _current_attempt(task: JsonObject, attempt_id: str | None = None) -> JsonObject:
        if attempt_id is None:
            if not isinstance(task.get("claim"), dict):
                raise ClaimRejected(f"task has no active claim: {task.get('task_id')}")
            attempt_id = task["claim"]["attempt_id"]
        matches = [
            attempt for attempt in task["attempts"] if attempt.get("attempt_id") == attempt_id
        ]
        if len(matches) != 1:
            raise QueueValidationError(
                f"task {task.get('task_id')} has {len(matches)} attempts named {attempt_id!r}"
            )
        return matches[0]

    def _build_escalation_child(
        self,
        parent: JsonObject,
        next_model: str,
        instant: datetime,
        *,
        target_revision: int | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        next_context = copy.deepcopy(
            dict(context) if context is not None else parent["context"]
        )
        prior_attempts = next_context.setdefault("prior_attempts", [])
        if not isinstance(prior_attempts, list):
            raise QueueValidationError("context prior_attempts must be an array")
        prior_attempts.append(
            {
                "task_id": parent["task_id"],
                "model": parent["expected_model"],
                "outcome": "inconclusive",
                "resolution": copy.deepcopy(parent["resolution"]),
            }
        )
        source_ids = parent["target"]["source_ids"]
        return self.build_task(
            company_id=parent["target"]["company_ids"][0],
            source_ids=source_ids,
            reason_codes=parent["reason_codes"],
            policy_version=parent["policy_version"],
            target_revision=(
                parent["target_revision"] if target_revision is None else target_revision
            ),
            context=next_context,
            expected_model=next_model,
            priority=parent["priority"],
            available_at=instant,
            created_at=instant,
            parent_task_id=parent["task_id"],
            root_task_id=parent["root_task_id"],
        )


__all__ = [
    "AccessFailureResult",
    "BATCH_CLOSE_REASONS",
    "BATCH_LIMITS",
    "BATCH_STATUSES",
    "BatchResult",
    "ClaimRejected",
    "ClaimResult",
    "DEFAULT_ACCESS_BACKOFF_SECONDS",
    "DEFAULT_ACCESS_RETRY_LIMIT",
    "DEFAULT_ENVIRONMENT_BACKOFF_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DedupeConflict",
    "EnqueueResult",
    "InvalidTransition",
    "LockedQueue",
    "MODELS",
    "QueueEngine",
    "QueueError",
    "QueueValidationError",
    "RecoveryResult",
    "RenewResult",
    "RetryResult",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TaskNotFound",
    "TransitionResult",
    "WORKFLOWS",
    "empty_queue_document",
    "format_timestamp",
    "make_dedupe_key",
    "make_task_id",
    "normalize_model",
    "normalize_workflow",
    "parse_timestamp",
    "utc_now",
]
