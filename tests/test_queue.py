from __future__ import annotations

from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from v4_daily_jobs.queue import (
    ClaimRejected,
    InvalidTransition,
    QueueEngine,
    QueueValidationError,
)
from v4_daily_jobs.schemas import validator


T0 = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)


def _company_engine(path: Path | str, **kwargs: object) -> QueueEngine:
    return QueueEngine(
        path,
        "validate_companies",
        validator=validator("company-queue.schema.json"),
        **kwargs,
    )


def _task(
    engine: QueueEngine,
    company_id: str,
    *,
    model: str = "luna",
    priority: int = 50,
    created_at: datetime = T0,
) -> dict[str, object]:
    return engine.build_task(
        company_id=company_id,
        reason_codes=["pending_validation"],
        policy_version="company_validation_v1",
        target_revision=1,
        context={"company_snapshot": {"company_id": company_id}},
        expected_model=model,
        priority=priority,
        created_at=created_at,
    )


def _claim_one(path_string: str, model: str) -> str | None:
    result = QueueEngine(path_string, "company", lock_timeout=15).claim_next(
        model,
        claimed_by="multiprocess-test",
        now=T0,
    )
    return result.task["task_id"] if result.task is not None else None


def _enqueue_same(path_string: str) -> bool:
    engine = QueueEngine(path_string, "company", lock_timeout=15)
    task = engine.build_task(
        company_id="same-company",
        reason_codes=["pending_validation"],
        policy_version="company_validation_v1",
        target_revision=1,
        context={},
        created_at=T0,
    )
    return engine.enqueue(task, now=T0).changed


def _drain_worker(
    path_string: str,
    claimed_by: str,
) -> list[str]:
    """Model a persistent worker: claim/finalize until no due task remains."""

    engine = QueueEngine(path_string, "company", lock_timeout=15)
    completed: list[str] = []
    for _index in range(1000):
        claim = engine.claim_next(
            "luna",
            claimed_by=claimed_by,
            now=T0 + timedelta(minutes=1),
        )
        if not claim.claimed:
            break
        assert claim.task is not None
        engine.resolve(
            claim.task["task_id"],
            claim.claim_token,
            "completed",
            attempt_id=claim.attempt_id,
            expected_model="luna",
            now=T0 + timedelta(minutes=2),
        )
        completed.append(claim.task["task_id"])
    else:
        raise AssertionError("drain worker exceeded its test safety bound")
    return completed


def _new_queue(tmp_path: Path, **kwargs: object) -> QueueEngine:
    engine = _company_engine(tmp_path / "company-queue.json", **kwargs)
    engine.initialize()
    return engine


def test_build_and_enqueue_are_stable_and_deduplicated(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    first = _task(engine, "cmp_123")
    repeated = _task(engine, "cmp_123")
    assert first == repeated

    created = engine.enqueue(first, now=T0)
    before = engine.store.path.read_bytes()
    duplicate = engine.enqueue(repeated, now=T0 + timedelta(minutes=1))

    assert created.changed and len(created.created) == 1
    assert not duplicate.changed and len(duplicate.existing) == 1
    assert duplicate.revision == 1
    assert engine.store.path.read_bytes() == before


def test_claim_order_is_priority_then_created_then_task_id(tmp_path: Path) -> None:
    tokens = iter(("token-1", "token-2", "token-3"))
    attempts = iter(("attempt-1", "attempt-2", "attempt-3"))
    engine = _new_queue(
        tmp_path,
        token_factory=lambda: next(tokens),
        attempt_id_factory=lambda: next(attempts),
    )
    tasks = [
        _task(engine, "low", priority=10, created_at=T0),
        _task(engine, "later", priority=80, created_at=T0 + timedelta(seconds=1)),
        _task(engine, "earlier", priority=80, created_at=T0),
    ]
    engine.enqueue_many(tasks, now=T0)

    claims = [engine.claim_next("luna", now=T0 + timedelta(minutes=1)) for _ in range(3)]

    assert [item.task["target"]["company_ids"][0] for item in claims] == [
        "earlier",
        "later",
        "low",
    ]


def test_empty_or_wrong_model_claim_is_exact_byte_noop(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    engine.enqueue(_task(engine, "terra-only", model="terra"), now=T0)
    before = (engine.store.path.read_bytes(), engine.store.path.stat().st_mtime_ns)

    result = engine.claim_next("luna", now=T0)

    assert not result.claimed and result.task is None
    assert (engine.store.path.read_bytes(), engine.store.path.stat().st_mtime_ns) == before


def test_mixed_model_claims_never_cross_model_scope(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    engine.enqueue_many(
        [
            _task(engine, "luna_company", model="luna"),
            _task(engine, "terra_company", model="terra"),
            _task(engine, "sol_company", model="sol"),
        ],
        now=T0,
    )

    for model in ("terra", "sol", "luna"):
        claim = engine.claim_next(model, now=T0)
        assert claim.task["expected_model"] == model
        assert claim.task["target"]["company_ids"] == [f"{model}_company"]


def test_job_lane_claims_are_atomic_and_do_not_cross_execution_modes(tmp_path: Path) -> None:
    engine = QueueEngine(tmp_path / "job-queue.json", "find_jobs")
    engine.initialize()
    structured = engine.build_task(
        company_id="structured-company",
        source_id="structured-source",
        reason_codes=["missing_snapshot"],
        policy_version="job_search_v1",
        target_revision=1,
        context={"structured_adapter_available": True},
        created_at=T0,
    )
    manual = engine.build_task(
        company_id="manual-company",
        source_id="manual-source",
        reason_codes=["missing_snapshot"],
        policy_version="job_search_v1",
        target_revision=1,
        context={"structured_adapter_available": False},
        created_at=T0,
    )
    engine.enqueue_many((structured, manual), now=T0)

    manual_claim = engine.claim_next("luna", lane="manual", now=T0)
    structured_claim = engine.claim_next("luna", lane="structured", now=T0)

    assert manual_claim.task["task_id"] == manual["task_id"]
    assert structured_claim.task["task_id"] == structured["task_id"]
    empty = engine.claim_next("luna", lane="manual", now=T0)
    assert not empty.claimed
    assert empty.reason == "manual_queue_empty"


def test_twenty_concurrent_claimers_receive_distinct_tasks(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    engine.enqueue_many((_task(engine, f"cmp_{index:02d}") for index in range(25)), now=T0)
    context = get_context("spawn")
    with context.Pool(processes=8) as pool:
        claimed = pool.starmap(_claim_one, [(str(engine.store.path), "luna")] * 20)

    assert None not in claimed
    assert len(claimed) == len(set(claimed)) == 20
    document = engine.read()
    assert sum(task["status"] == "running" for task in document["tasks"]) == 20
    assert sum(task["status"] == "open" for task in document["tasks"]) == 5


def test_concurrent_drain_workers_complete_queue_without_overlap(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    engine.enqueue_many((_task(engine, f"batch_{index:02d}") for index in range(32)), now=T0)
    context = get_context("spawn")
    with context.Pool(processes=4) as pool:
        worker_results = pool.starmap(
            _drain_worker,
            [
                (str(engine.store.path), f"company-worker:{index}")
                for index in range(4)
            ],
        )

    completed = [task_id for worker in worker_results for task_id in worker]
    assert len(completed) == len(set(completed)) == 32
    document = engine.read()
    assert sum(task["status"] == "completed" for task in document["tasks"]) == 32
    assert sum(task["status"] == "open" for task in document["tasks"]) == 0
    assert sum(task["status"] == "running" for task in document["tasks"]) == 0


def test_interrupted_worker_strands_only_current_item_while_healthy_worker_drains(
    tmp_path: Path,
) -> None:
    engine = _new_queue(tmp_path)
    engine.enqueue_many((_task(engine, f"interrupt_{index:02d}") for index in range(10)), now=T0)

    abandoned_agent = engine.claim_next(
        "luna",
        claimed_by="company-worker:interrupted",
        now=T0 + timedelta(minutes=1),
    )
    assert abandoned_agent.claimed
    completed = _drain_worker(
        str(engine.store.path),
        "company-worker:healthy",
    )

    assert len(completed) == 9
    document = engine.read()
    assert sum(task["status"] == "running" for task in document["tasks"]) == 1
    assert sum(task["status"] == "completed" for task in document["tasks"]) == 9
    assert sum(task["status"] == "open" for task in document["tasks"]) == 0
    running = next(task for task in document["tasks"] if task["status"] == "running")
    assert running["claim"]["claimed_by"] == "company-worker:interrupted"


def test_concurrent_enqueue_creates_exactly_one_task(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    context = get_context("spawn")
    with context.Pool(processes=6) as pool:
        changed = pool.map(_enqueue_same, [str(engine.store.path)] * 12)

    assert sum(changed) == 1
    document = engine.read()
    assert document["revision"] == 1
    assert len(document["tasks"]) == 1


def test_renew_recovery_and_old_token_rejection(tmp_path: Path) -> None:
    tokens = iter(("old-token", "new-token"))
    attempts = iter(("old-attempt", "new-attempt"))
    engine = _new_queue(
        tmp_path,
        lease_seconds=60,
        token_factory=lambda: next(tokens),
        attempt_id_factory=lambda: next(attempts),
    )
    task = _task(engine, "cmp_lease")
    engine.enqueue(task, now=T0)
    claimed = engine.claim_next("luna", now=T0)
    engine.renew(
        task["task_id"],
        "old-token",
        attempt_id="old-attempt",
        now=T0 + timedelta(seconds=30),
    )
    # A shorter override never shortens an already-owned lease.
    unchanged_expiry = engine.renew(
        task["task_id"],
        "old-token",
        attempt_id="old-attempt",
        lease_seconds=10,
        now=T0 + timedelta(seconds=31),
    )
    assert unchanged_expiry.task["claim"]["lease_expires_at"] == "2026-08-29T18:01:30Z"

    not_stale = engine.recover_stale(now=T0 + timedelta(seconds=61))
    recovered = engine.recover_stale(now=T0 + timedelta(seconds=91))

    assert not not_stale.changed
    assert recovered.recovered_task_ids == (task["task_id"],)
    with pytest.raises(ClaimRejected, match="not currently running"):
        engine.resolve(
            task["task_id"],
            "old-token",
            "completed",
            attempt_id="old-attempt",
            now=T0 + timedelta(seconds=92),
        )
    reclaimed = engine.claim_next("luna", now=T0 + timedelta(seconds=92))
    assert reclaimed.claim_token == "new-token"
    assert reclaimed.attempt_id == "new-attempt"


def test_operator_abandon_reopens_unexpired_claim_and_preserves_history(
    tmp_path: Path,
) -> None:
    engine = _new_queue(
        tmp_path,
        lease_seconds=3600,
        token_factory=lambda: "stopped-token",
        attempt_id_factory=lambda: "stopped-attempt",
    )
    task = _task(engine, "stopped-company")
    engine.enqueue(task, now=T0)
    engine.claim_next(
        "luna",
        claimed_by="company-worker:stopped",
        now=T0 + timedelta(minutes=1),
    )

    result = engine.abandon(
        task["task_id"],
        reason="operator_stopped_agent",
        now=T0 + timedelta(minutes=2),
    )

    assert result.recovered_task_ids == (task["task_id"],)
    reopened = engine.read()["tasks"][0]
    assert reopened["status"] == "open"
    assert reopened["claim"] is None
    assert reopened["attempts"][-1]["status"] == "abandoned"
    assert reopened["attempts"][-1]["outcome"] == "operator_stopped_agent"
    with pytest.raises(ClaimRejected, match="not currently running"):
        engine.resolve(
            task["task_id"],
            "stopped-token",
            "completed",
            attempt_id="stopped-attempt",
            now=T0 + timedelta(minutes=3),
        )


def test_operator_abandon_rejects_non_running_task_and_invalid_reason(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    task = _task(engine, "not-running")
    engine.enqueue(task, now=T0)

    with pytest.raises(InvalidTransition, match="requires running"):
        engine.abandon(task["task_id"], now=T0 + timedelta(minutes=1))
    with pytest.raises(QueueValidationError, match="snake_case"):
        engine.abandon(task["task_id"], reason="bad reason")


def test_expired_wrong_token_and_wrong_model_claims_are_rejected(tmp_path: Path) -> None:
    engine = _new_queue(
        tmp_path,
        lease_seconds=60,
        token_factory=lambda: "token",
        attempt_id_factory=lambda: "attempt",
    )
    task = _task(engine, "cmp_claim")
    engine.enqueue(task, now=T0)
    claim = engine.claim_next("luna", now=T0)

    with pytest.raises(ClaimRejected, match="stale or invalid"):
        engine.renew(task["task_id"], "wrong", now=T0 + timedelta(seconds=1))
    with pytest.raises(ClaimRejected, match="attempt ID"):
        engine.renew(
            task["task_id"],
            "token",
            attempt_id="wrong-attempt",
            now=T0 + timedelta(seconds=1),
        )
    with pytest.raises(ClaimRejected, match="model"):
        engine.renew(
            task["task_id"],
            "token",
            expected_model="terra",
            now=T0 + timedelta(seconds=1),
        )
    with pytest.raises(ClaimRejected, match="expired"):
        engine.resolve(
            task["task_id"],
            "token",
            "completed",
            now=T0 + timedelta(seconds=60),
        )

    recovered_commit = engine.resolve(
        task["task_id"],
        "token",
        "completed",
        attempt_id="attempt",
        now=T0 + timedelta(seconds=60),
        require_unexpired=False,
    )
    assert recovered_commit.task["status"] == "completed"


def test_inconclusive_escalates_luna_to_terra_to_sol_then_human(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path, lease_seconds=3600)
    initial = _task(engine, "cmp_escalate")
    engine.enqueue(initial, now=T0)

    luna = engine.claim_next("luna", now=T0)
    terra_child = engine.resolve(
        initial["task_id"],
        luna.claim_token,
        "inconclusive",
        attempt_id=luna.attempt_id,
        resolution={"reason_code": "conflicting_evidence"},
        now=T0 + timedelta(seconds=1),
    )
    assert terra_child.follow_up_created
    assert terra_child.follow_up_task["expected_model"] == "terra"
    assert terra_child.follow_up_task["parent_task_id"] == initial["task_id"]

    terra = engine.claim_next("terra", now=T0 + timedelta(seconds=2))
    sol_child = engine.resolve(
        terra.task["task_id"],
        terra.claim_token,
        "inconclusive",
        attempt_id=terra.attempt_id,
        now=T0 + timedelta(seconds=3),
    )
    assert sol_child.follow_up_task["expected_model"] == "sol"
    assert sol_child.follow_up_task["root_task_id"] == initial["task_id"]

    sol = engine.claim_next("sol", now=T0 + timedelta(seconds=4))
    human = engine.resolve(
        sol.task["task_id"],
        sol.claim_token,
        "inconclusive",
        attempt_id=sol.attempt_id,
        now=T0 + timedelta(seconds=5),
    )
    assert human.follow_up_task is None
    assert human.task["resolution"]["human_review_required"] is True
    assert len(engine.read()["tasks"]) == 3


def test_escalation_can_use_post_patch_revision_and_context(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    task = _task(engine, "cmp_partial")
    engine.enqueue(task, now=T0)
    claim = engine.claim_next("luna", now=T0)

    result = engine.resolve(
        task["task_id"],
        claim.claim_token,
        "inconclusive",
        now=T0 + timedelta(seconds=1),
        follow_up_target_revision=2,
        follow_up_context={"company_snapshot": {"record_revision": 2}},
    )

    assert result.follow_up_task["target_revision"] == 2
    assert result.follow_up_task["context"]["company_snapshot"]["record_revision"] == 2
    assert result.follow_up_task["context"]["prior_attempts"][0]["task_id"] == task["task_id"]


def test_access_failures_retry_same_model_twice_then_block(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path, lease_seconds=3600)
    task = _task(engine, "cmp_access")
    engine.enqueue(task, now=T0)
    current = T0

    first_claim = engine.claim_next("luna", now=current)
    first = engine.handle_access_failure(
        task["task_id"],
        first_claim.claim_token,
        "network",
        now=current + timedelta(seconds=1),
    )
    assert first.retry_scheduled and not first.blocked
    assert first.task["expected_model"] == "luna"
    assert first.task["available_at"] == "2026-08-29T18:05:01Z"

    current = T0 + timedelta(minutes=6)
    second_claim = engine.claim_next("luna", now=current)
    second = engine.handle_access_failure(
        task["task_id"],
        second_claim.claim_token,
        "rate_limit",
        now=current + timedelta(seconds=1),
    )
    assert second.retry_scheduled
    assert second.task["available_at"] == "2026-08-29T18:36:01Z"

    current = T0 + timedelta(minutes=37)
    third_claim = engine.claim_next("luna", now=current)
    third = engine.handle_access_failure(
        task["task_id"],
        third_claim.claim_token,
        "source_access",
        now=current + timedelta(seconds=1),
    )
    assert third.blocked and not third.retry_scheduled
    assert third.task["resolution"]["failure_number"] == 3
    assert all(item["expected_model"] == "luna" for item in engine.read()["tasks"])
    assert len(engine.read()["tasks"]) == 1


def test_environment_failure_releases_claim_without_consuming_access_retry(
    tmp_path: Path,
) -> None:
    engine = _new_queue(tmp_path, lease_seconds=3600)
    task = _task(engine, "cmp_environment")
    engine.enqueue(task, now=T0)
    claim = engine.claim_next("luna", now=T0)

    released = engine.release_environment_failure(
        task["task_id"],
        claim.claim_token,
        attempt_id=claim.attempt_id,
        expected_model="luna",
        now=T0 + timedelta(seconds=1),
    )

    assert released.task["status"] == "open"
    assert released.task["expected_model"] == "luna"
    assert released.task["available_at"] == "2026-08-29T18:05:01Z"
    attempt = released.task["attempts"][-1]
    assert attempt["outcome"] == "environment_failure"
    assert attempt["resolution"]["retry_budget_consumed"] is False

    retry_claim = engine.claim_next("luna", now=T0 + timedelta(minutes=6))
    access = engine.handle_access_failure(
        task["task_id"],
        retry_claim.claim_token,
        "network",
        now=T0 + timedelta(minutes=6, seconds=1),
    )
    assert access.task["attempts"][-1]["resolution"]["failure_number"] == 1


def test_explicit_retry_only_reopens_blocked_or_failed(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    task = _task(engine, "cmp_retry")
    engine.enqueue(task, now=T0)
    with pytest.raises(InvalidTransition, match="blocked or failed"):
        engine.retry(task["task_id"], now=T0)

    claim = engine.claim_next("luna", now=T0)
    engine.resolve(task["task_id"], claim.claim_token, "failed", now=T0 + timedelta(seconds=1))
    retried = engine.retry(task["task_id"], now=T0 + timedelta(seconds=2))
    assert retried.task["status"] == "open"
    assert retried.task["context"]["prior_resolutions"][0]["resolution"]["outcome"] == "failed"


def test_locked_interface_rechecks_claim_and_commits_without_relocking(tmp_path: Path) -> None:
    engine = _new_queue(
        tmp_path,
        token_factory=lambda: "token",
        attempt_id_factory=lambda: "attempt",
    )
    task = _task(engine, "cmp_tx")
    engine.enqueue(task, now=T0)
    engine.claim_next("luna", now=T0)

    with engine.transaction() as transaction:
        verified = transaction.require_claim(
            task["task_id"], "token", attempt_id="attempt", now=T0 + timedelta(seconds=1)
        )
        assert verified["target"]["company_ids"] == ["cmp_tx"]
        result = engine.resolve_locked(
            transaction,
            task["task_id"],
            "token",
            "completed",
            attempt_id="attempt",
            now=T0 + timedelta(seconds=1),
        )

    assert result.task["status"] == "completed"


def test_locked_save_rejects_undocumented_transition_and_immutable_change(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    task = _task(engine, "cmp_guard")
    engine.enqueue(task, now=T0)

    with engine.locked() as locked:
        locked.find_task(task["task_id"])["status"] = "completed"
        locked.find_task(task["task_id"])["resolution"] = {"outcome": "completed"}
        with pytest.raises(InvalidTransition, match="open -> completed"):
            locked.save(now=T0)

    with engine.locked() as locked:
        locked.find_task(task["task_id"])["target_revision"] = 2
        with pytest.raises(InvalidTransition, match="immutable"):
            locked.save(now=T0)

    claim = engine.claim_next("luna", now=T0)
    with engine.locked() as locked:
        locked.find_task(task["task_id"])["context"]["unauthorized"] = True
        with pytest.raises(InvalidTransition, match="only its lease"):
            locked.save(now=T0 + timedelta(seconds=1))


def test_job_queue_requires_exactly_one_source(tmp_path: Path) -> None:
    engine = QueueEngine(tmp_path / "jobs.json", "find_jobs")
    engine.initialize()
    with pytest.raises(QueueValidationError, match="source_id"):
        engine.build_task(
            company_id="cmp_1",
            reason_codes=["missing_snapshot"],
            policy_version="job_search_v1",
            target_revision=1,
            context={},
            created_at=T0,
        )


def test_company_task_supports_zero_or_more_exact_sorted_source_ids(tmp_path: Path) -> None:
    engine = _new_queue(tmp_path)
    task = engine.build_task(
        company_id="cmp_multi",
        source_ids=["source_b", "source_a"],
        reason_codes=["career_source_stale"],
        policy_version="company_validation_v1",
        target_revision=3,
        context={},
        created_at=T0,
    )

    assert task["target"]["source_ids"] == ["source_a", "source_b"]
    assert "sources=source_a,source_b" in task["dedupe_key"]
    engine.enqueue(task, now=T0)

    with pytest.raises(QueueValidationError, match="not both"):
        engine.build_task(
            company_id="cmp_multi",
            source_id="source_a",
            source_ids=["source_a"],
            reason_codes=["career_source_stale"],
            policy_version="company_validation_v1",
            target_revision=3,
            context={},
            created_at=T0,
        )


def test_job_queue_accepts_source_ids_compatibility_form(tmp_path: Path) -> None:
    engine = QueueEngine(
        tmp_path / "jobs.json",
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )
    engine.initialize()
    task = engine.build_task(
        company_id="cmp_1",
        source_ids=["source_1"],
        reason_codes=["missing_snapshot"],
        policy_version="job_search_v1",
        target_revision=1,
        context={},
        created_at=T0,
    )
    assert task["target"]["source_ids"] == ["source_1"]
