from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import v4_daily_jobs.fetching as fetching_module
import v4_daily_jobs.finalize as finalize_module
from v4_daily_jobs.adapters import ashby
from conftest import PROJECT_ROOT, STAMP, write_json
from v4_daily_jobs.adapters.base import stable_job_id
from v4_daily_jobs.audit import (
    audit_and_enqueue_companies,
    audit_and_enqueue_jobs,
    profile_digest,
)
from v4_daily_jobs.companies import validate_integrity
from v4_daily_jobs.cli import main as cli_main
from v4_daily_jobs.fetching import prepare_job_result
from v4_daily_jobs.finalize import finalize_result, recover_journal
from v4_daily_jobs.migration import EXPECTED_COMPANY_COUNTS, build_migration
from v4_daily_jobs.paths import RepoPaths
from v4_daily_jobs.queue import QueueEngine
from v4_daily_jobs.reporting import render_status, status_snapshot
from v4_daily_jobs.schemas import validate, validator


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def company_engine(paths: RepoPaths) -> QueueEngine:
    return QueueEngine(
        paths.company_queue,
        "validate_companies",
        validator=validator("company-queue.schema.json"),
    )


def job_engine(paths: RepoPaths) -> QueueEngine:
    return QueueEngine(
        paths.job_queue,
        "find_jobs",
        validator=validator("job-queue.schema.json"),
    )


def claim_company(paths: RepoPaths):
    canonical = load(paths.companies)
    company = canonical["companies"][0]
    engine = company_engine(paths)
    task = engine.build_task(
        company_id=company["company_id"],
        source_ids=[source["source_id"] for source in company["career_sources"]],
        reason_codes=["pending_validation"],
        policy_version="test_v1",
        target_revision=company["record_revision"],
        context={
            "company_snapshot": copy.deepcopy(company),
            "source_snapshots": copy.deepcopy(company["career_sources"]),
            "relevant_evidence": [],
        },
        expected_model="luna",
    )
    engine.enqueue(task)
    claim = engine.claim_next("luna")
    assert claim.claimed and claim.task is not None
    return engine, claim


def company_result(claim, *, outcome: str = "inconclusive") -> dict:
    task = claim.task
    assert task is not None
    return {
        "schema_version": 1,
        "workflow": "validate_companies",
        "task_id": task["task_id"],
        "attempt_id": claim.attempt_id,
        "claim_token": claim.claim_token,
        "model": "luna",
        "created_at": STAMP,
        "target": copy.deepcopy(task["target"]),
        "outcome": outcome,
        "reason_code": "address_evidence_inconclusive" if outcome == "inconclusive" else "validated",
        "evidence": [
            {
                "url": "https://example.com/about",
                "retrieved_at": STAMP,
                "evidence_type": "official_company_page",
                "finding": "The official site establishes company identity but not an office address.",
            }
        ],
        "patch": {
            "company_id": "example-company",
            "expected_record_revision": task["target_revision"],
            "company_changes": {"website_url": "https://example.com/"},
            "source_upserts": [],
        },
        "item_results": [
            {
                "item": "company website",
                "status": "resolved",
                "summary": "Official website verified.",
            },
            {
                "item": "physical address",
                "status": "inconclusive",
                "summary": "No credible current physical office evidence.",
            },
        ],
        "failure_class": None,
        "notes": "Escalate only the unresolved address evidence.",
    }


def test_company_partial_result_applies_and_escalates_at_new_revision(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory())
    _engine, claim = claim_company(paths)
    result = company_result(claim)
    result_path = paths.result_dir("validate_companies") / "partial.json"
    write_json(result_path, result)

    report = finalize_result(paths, result_path)
    canonical = load(paths.companies)
    company = canonical["companies"][0]
    queue = load(paths.company_queue)
    parent = next(task for task in queue["tasks"] if task["task_id"] == result["task_id"])
    child = next(task for task in queue["tasks"] if task["parent_task_id"] == parent["task_id"])

    assert report["execution_status"] == "completed"
    assert company["website_url"] == "https://example.com/"
    assert company["record_revision"] == 2
    assert parent["status"] == "inconclusive"
    assert parent["resolution"]["result_file"] == str(result_path)
    assert child["expected_model"] == "terra"
    assert child["target_revision"] == 2
    assert child["context"]["company_snapshot"]["record_revision"] == 2

    ledger_size = len(canonical["application_ledger"])
    replay = finalize_result(paths, result_path)
    assert "already finalized" in replay["message"]
    assert len(load(paths.companies)["application_ledger"]) == ledger_size


def test_crash_after_canonical_commit_recovers_queue_and_child(repo_factory, monkeypatch) -> None:
    paths = RepoPaths.discover(repo_factory())
    _engine, claim = claim_company(paths)
    result_path = paths.result_dir("validate_companies") / "crash.json"
    write_json(result_path, company_result(claim))
    original = finalize_module._queue_resolution

    def crash(*args, **kwargs):
        raise RuntimeError("injected crash after canonical commit")

    monkeypatch.setattr(finalize_module, "_queue_resolution", crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        finalize_result(paths, result_path)
    assert paths.journal_path("validate_companies").exists()
    assert load(paths.companies)["companies"][0]["record_revision"] == 2

    monkeypatch.setattr(finalize_module, "_queue_resolution", original)
    recovered = recover_journal(paths, "validate_companies")
    assert recovered and recovered["recovered"] is True
    queue = load(paths.company_queue)
    assert sorted(task["status"] for task in queue["tasks"]) == ["inconclusive", "open"]
    assert next(task for task in queue["tasks"] if task["status"] == "open")[
        "target_revision"
    ] == 2
    assert not paths.journal_path("validate_companies").exists()


def test_crash_before_canonical_phase_journal_update_recovers(repo_factory, monkeypatch) -> None:
    paths = RepoPaths.discover(repo_factory())
    _engine, claim = claim_company(paths)
    result_path = paths.result_dir("validate_companies") / "narrow-crash.json"
    write_json(result_path, company_result(claim))
    original = finalize_module._write_journal

    def crash_on_phase_update(path, document):
        if document.get("phase") == "canonical_applied":
            raise RuntimeError("injected phase-journal crash")
        return original(path, document)

    monkeypatch.setattr(finalize_module, "_write_journal", crash_on_phase_update)
    with pytest.raises(RuntimeError, match="phase-journal"):
        finalize_result(paths, result_path)
    journal = load(paths.journal_path("validate_companies"))
    assert journal["phase"] == "prepared"
    assert load(paths.companies)["companies"][0]["record_revision"] == 2

    monkeypatch.setattr(finalize_module, "_write_journal", original)
    recovered = recover_journal(paths, "validate_companies")
    assert recovered and recovered["recovered"] is True
    queue = load(paths.company_queue)
    child = next(task for task in queue["tasks"] if task["status"] == "open")
    assert child["expected_model"] == "terra"
    assert child["target_revision"] == 2


def test_crash_after_queue_commit_only_leaves_removable_journal(repo_factory, monkeypatch) -> None:
    paths = RepoPaths.discover(repo_factory())
    _engine, claim = claim_company(paths)
    result_path = paths.result_dir("validate_companies") / "post-queue-crash.json"
    write_json(result_path, company_result(claim))
    original = finalize_module._remove_journal

    def crash_remove(_path):
        raise RuntimeError("injected journal-removal crash")

    monkeypatch.setattr(finalize_module, "_remove_journal", crash_remove)
    with pytest.raises(RuntimeError, match="journal-removal"):
        finalize_result(paths, result_path)
    assert paths.journal_path("validate_companies").exists()
    assert sorted(task["status"] for task in load(paths.company_queue)["tasks"]) == [
        "inconclusive",
        "open",
    ]

    monkeypatch.setattr(finalize_module, "_remove_journal", original)
    recovered = recover_journal(paths, "validate_companies")
    assert recovered == {"recovered": False, "removed_completed_journal": True}
    assert not paths.journal_path("validate_companies").exists()


def normalized_job(source_id: str) -> dict:
    provider_id = "job-1"
    return {
        "stable_id": stable_job_id(source_id, provider_id),
        "company_id": "example-company",
        "source_id": source_id,
        "provider": "ashby",
        "source_job_id": provider_id,
        "canonical_url": "https://jobs.ashbyhq.com/example/job-1",
        "title": "Product Engineer",
        "description_text": "Build AI product experiences.",
        "locations": ["San Francisco, CA"],
        "workplace_arrangement": "hybrid",
        "workplace_text": "Hybrid",
        "employment_type": "full-time",
        "employment_text": "Full-time",
        "compensation": {
            "minimum": 180000,
            "maximum": 240000,
            "currency": "USD",
            "basis": "base",
            "period": "year",
            "source_text": "$180,000-$240,000 USD base per year",
        },
        "compensation_text": "$180,000-$240,000 USD base per year",
        "department": "Engineering",
        "team": "Product",
        "posted_at": STAMP,
        "updated_at": STAMP,
    }


def job_claim(paths: RepoPaths, *, policy: str = "jobs_test_v1"):
    canonical = load(paths.companies)
    company = canonical["companies"][0]
    source = company["career_sources"][0]
    profile = load(paths.profile)
    from v4_daily_jobs.audit import profile_digest

    engine = job_engine(paths)
    task = engine.build_task(
        company_id=company["company_id"],
        source_id=source["source_id"],
        reason_codes=["missing_snapshot"],
        policy_version=policy,
        target_revision=source["record_revision"],
        context={
            "company_snapshot": copy.deepcopy(company),
            "source_snapshot": copy.deepcopy(source),
            "previous_job_snapshot": None,
            "profile": profile,
            "profile_digest": profile_digest(profile),
            "structured_adapter_available": True,
        },
        expected_model="luna",
    )
    engine.enqueue(task)
    claim = engine.claim_next("luna")
    assert claim.claimed and claim.task is not None
    return claim


def job_result(paths: RepoPaths, claim, jobs: list[dict], qualifying: list[dict | str]) -> dict:
    profile = load(paths.profile)
    from v4_daily_jobs.audit import profile_digest

    task = claim.task
    assert task is not None
    return {
        "schema_version": 1,
        "workflow": "find_jobs",
        "task_id": task["task_id"],
        "attempt_id": claim.attempt_id,
        "claim_token": claim.claim_token,
        "model": "luna",
        "created_at": STAMP,
        "target": copy.deepcopy(task["target"]),
        "outcome": "resolved",
        "reason_code": "structured_scan_complete",
        "evidence": [],
        "snapshot": {
            "source_id": "example-company-ashby",
            "company_id": "example-company",
            "expected_source_revision": 1,
            "profile_id": profile["profile_id"],
            "profile_digest": profile_digest(profile),
            "scanned_at": STAMP,
            "adapter_result": {
                "provider": "ashby",
                "company_id": "example-company",
                "source_id": "example-company-ashby",
                "jobs": copy.deepcopy(jobs),
                "fetch": {
                    "provider": "ashby",
                    "complete": True,
                    "completeness_reason": "complete_single_page",
                    "pages_received": 1,
                    "pages_expected": 1,
                    "total_reported": len(jobs),
                    "raw_jobs_seen": len(jobs),
                    "unique_jobs": len(jobs),
                    "duplicates_removed": 0,
                },
            },
            "qualifying_jobs": qualifying,
            "unresolved_candidates": [],
            "rejection_counts": {},
            "warnings": [],
        },
        "item_results": [
            {
                "item": "example-company-ashby",
                "status": "resolved",
                "summary": "Complete source enumeration reviewed.",
            }
        ],
        "failure_class": None,
        "notes": None,
    }


def test_job_finalizer_preserves_lifecycle_history(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    first_claim = job_claim(paths, policy="jobs_first_v1")
    job = normalized_job("example-company-ashby")
    first = job_result(
        paths,
        first_claim,
        [job],
        [{"stable_id": job["stable_id"], "qualification_status": "qualifying"}],
    )
    first_path = paths.result_dir("find_jobs") / "first.json"
    write_json(first_path, first)
    finalize_result(paths, first_path)

    snapshot_path = paths.job_sources / "example-company-ashby.json"
    first_snapshot = load(snapshot_path)
    assert first_snapshot["jobs"][0]["status"] == "active"
    assert first_snapshot["jobs"][0]["qualification_status"] == "qualifying"

    second_claim = job_claim(paths, policy="jobs_second_v1")
    second = job_result(paths, second_claim, [], [])
    second["created_at"] = "2026-08-30T13:00:00Z"
    second["snapshot"]["scanned_at"] = "2026-08-30T13:00:00Z"
    second_path = paths.result_dir("find_jobs") / "second.json"
    write_json(second_path, second)
    finalize_result(paths, second_path)
    second_snapshot = load(snapshot_path)

    assert second_snapshot["revision"] == 2
    assert second_snapshot["current_job_count"] == 0
    assert second_snapshot["jobs"][0]["status"] == "inactive"
    assert second_snapshot["jobs"][0]["inactive_at"] == "2026-08-30T13:00:00Z"


def test_prepare_structured_result_is_schema_valid(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    claim = job_claim(paths)
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "ashby-1",
                "title": "Product Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/example/ashby-1",
                "location": "San Francisco, CA",
                "descriptionPlain": "Build AI product features.",
                "workplaceType": "Hybrid",
                "employmentType": "Full-time",
                "isListed": True,
            }
        ],
    }
    prepared = prepare_job_result(paths, claim.task, fetcher=lambda _url, _timeout: payload)
    document = load(Path(prepared["result_path"]))
    validate(document, "job-result.schema.json")
    assert document["snapshot"]["adapter_result"]["fetch"]["complete"] is True
    assert document["snapshot"]["adapter_result"]["fetch"]["unique_jobs"] == 1


def test_prepare_incomplete_or_invalid_feed_fails_closed(repo_factory, monkeypatch) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    claim = job_claim(paths)
    incomplete = ashby.parse_payload(
        {"jobs": [], "meta": {"total": 1}},
        company_id="example-company",
        source_id="example-company-ashby",
        board_id="example",
        enumeration_complete=True,
    )
    monkeypatch.setattr(
        fetching_module,
        "fetch_structured_source",
        lambda *_args, **_kwargs: (incomplete, ["https://example.test/feed"]),
    )
    prepared = prepare_job_result(paths, claim.task)
    assert prepared["result"]["outcome"] == "blocked"
    assert prepared["result"]["failure_class"] == "source_access"
    assert prepared["result"]["snapshot"] is None

    Path(prepared["result_path"]).unlink()

    def invalid_feed(*_args, **_kwargs):
        raise ValueError("bad feed")

    monkeypatch.setattr(fetching_module, "fetch_structured_source", invalid_feed)
    invalid = prepare_job_result(paths, claim.task)
    assert invalid["result"]["outcome"] == "failed"
    assert invalid["result"]["failure_class"] == "invalid_output"


def test_resolved_job_result_cannot_change_profile_or_leave_candidates(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    claim = job_claim(paths)
    job = normalized_job("example-company-ashby")
    unresolved = job_result(paths, claim, [job], [])
    unresolved["snapshot"]["unresolved_candidates"] = [copy.deepcopy(job)]
    unresolved_path = paths.result_dir("find_jobs") / "unresolved-resolved.json"
    write_json(unresolved_path, unresolved)
    with pytest.raises(Exception, match="cannot retain unresolved"):
        finalize_result(paths, unresolved_path)
    assert not paths.journal_path("find_jobs").exists()

    changed_profile = job_result(paths, claim, [job], [])
    changed_profile["snapshot"]["profile_digest"] = "f" * 64
    profile_path = paths.result_dir("find_jobs") / "changed-profile.json"
    write_json(profile_path, changed_profile)
    with pytest.raises(Exception, match="profile digest"):
        finalize_result(paths, profile_path)
    assert not paths.journal_path("find_jobs").exists()


def test_audits_are_idempotent_and_status_is_read_only(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    canonical = load(paths.companies)
    profile = load(paths.profile)
    companies = company_engine(paths)
    jobs = job_engine(paths)
    first_company = audit_and_enqueue_companies(canonical, companies)
    first_jobs = audit_and_enqueue_jobs(canonical, jobs, profile, {})
    assert first_company["created"] == 0
    assert first_jobs["created"] == 1

    before_company = paths.company_queue.read_bytes()
    before_jobs = paths.job_queue.read_bytes()
    assert audit_and_enqueue_companies(canonical, companies)["changed"] is False
    assert audit_and_enqueue_jobs(canonical, jobs, profile, {})["changed"] is False
    assert paths.company_queue.read_bytes() == before_company
    assert paths.job_queue.read_bytes() == before_jobs

    tracked = [paths.companies, paths.company_queue, paths.job_queue, paths.profile]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
    report = status_snapshot(paths, now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc))
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
    assert before == after
    assert report["companies"]["total"] == 1
    assert report["queues"]["find_jobs"]["by_status"]["open"] == 1
    rendered = render_status(report)
    assert (
        "Deterministic adapters: Ashby 1 | Greenhouse 0 | Lever 0 | "
        "Agent enumeration 0"
    ) in rendered
    assert "Recent activity" in rendered
    assert "Last 24h:" in rendered


def test_recurring_stale_job_audits_get_a_new_cycle_identity(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory(verified=True, with_source=True))
    canonical = load(paths.companies)
    profile = load(paths.profile)
    digest = profile_digest(profile)
    engine = job_engine(paths)

    def snapshot(revision: int, scanned_at: str) -> dict:
        return {
            "revision": revision,
            "profile_digest": digest,
            "fetch_complete": True,
            "last_successful_scan_at": scanned_at,
        }

    first_time = datetime(2026, 8, 31, 14, tzinfo=timezone.utc)
    first = audit_and_enqueue_jobs(
        canonical,
        engine,
        profile,
        {"example-company-ashby": snapshot(1, STAMP)},
        now=first_time,
    )
    assert first["created"] == 1
    first_task = engine.read()["tasks"][0]
    first_claim = engine.claim_next("luna", now=first_time)
    engine.resolve(
        first_task["task_id"],
        first_claim.claim_token,
        "completed",
        attempt_id=first_claim.attempt_id,
        now=first_time,
    )

    second_time = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    second = audit_and_enqueue_jobs(
        canonical,
        engine,
        profile,
        {"example-company-ashby": snapshot(2, "2026-08-31T14:00:00Z")},
        now=second_time,
    )
    tasks = engine.read()["tasks"]
    assert second["created"] == 1
    assert len(tasks) == 2
    assert tasks[0]["dedupe_key"] != tasks[1]["dedupe_key"]
    assert {task["policy_version"] for task in tasks} == {
        f"job_search_v1:profile_{digest[:16]}:snapshot_1",
        f"job_search_v1:profile_{digest[:16]}:snapshot_2",
    }


def test_precommit_conflict_does_not_leave_poison_journal(repo_factory) -> None:
    paths = RepoPaths.discover(repo_factory())
    engine, claim = claim_company(paths)
    result_path = paths.result_dir("validate_companies") / "conflict.json"
    write_json(result_path, company_result(claim))

    canonical = load(paths.companies)
    canonical["companies"][0]["record_revision"] = 2
    canonical["revision"] = 2
    write_json(paths.companies, canonical)

    with pytest.raises(Exception, match="revision conflict"):
        finalize_result(paths, result_path)
    assert not paths.journal_path("validate_companies").exists()

    renewed = engine.renew(
        claim.task["task_id"],
        claim.claim_token,
        attempt_id=claim.attempt_id,
        expected_model="luna",
    )
    assert renewed.task["status"] == "running"


def test_live_migration_checkpoint_and_documents() -> None:
    documents = build_migration()
    canonical = documents["companies"]
    counts: dict[str, int] = {"total": len(canonical["companies"])}
    for status in ("verified", "outside_sf", "not_found", "pending"):
        counts[status] = sum(
            company["address_status"] == status for company in canonical["companies"]
        )
    assert counts == EXPECTED_COMPANY_COUNTS
    assert documents["research"]["record_count"] == 1446
    assert documents["research"]["company_count"] == 1365
    assert documents["research"]["records"][0]["issue_index"] >= 0
    assert set(documents["manifest"]["output_digests"]) == {
        "data/companies.json",
        "data/profiles/ai-product-frontend-v1.json",
        "archive/v3/company-research.json",
    }
    assert documents["manifest"]["research_unknown_company_ids"] == ["qualified"]
    assert len(documents["manifest"]["warnings"]) >= 5
    validate_integrity(canonical)

    profile_source = (
        Path("/Users/natelipp/ai-code/_v3_daily-jobs")
        / "config"
        / "search-profiles"
        / "ai-product-frontend-v1.json"
    )
    profile_v4 = PROJECT_ROOT / "data" / "profiles" / "ai-product-frontend-v1.json"
    assert profile_source.read_bytes() == profile_v4.read_bytes()


def test_live_migrated_documents_validate() -> None:
    validate(load(PROJECT_ROOT / "data" / "companies.json"), "companies.schema.json")
    validate(load(PROJECT_ROOT / "data" / "queues" / "company-queue.json"), "company-queue.schema.json")
    validate(load(PROJECT_ROOT / "data" / "queues" / "job-queue.json"), "job-queue.schema.json")
    validate(load(PROJECT_ROOT / "data" / "profiles" / "ai-product-frontend-v1.json"), "search-profile.schema.json")


def test_search_profile_schema_rejects_missing_nested_contract() -> None:
    broken = load(PROJECT_ROOT / "data" / "profiles" / "ai-product-frontend-v1.json")
    del broken["hard_constraints"]["role"]["ai_involvement"]
    with pytest.raises(Exception, match="ai_involvement"):
        validate(broken, "search-profile.schema.json")


def test_low_level_replacement_and_direct_data_writes_stay_confined() -> None:
    source_files = sorted((PROJECT_ROOT / "src" / "v4_daily_jobs").rglob("*.py"))
    replace_users: list[str] = []
    direct_write_users: list[str] = []
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        if "os.replace(" in text:
            replace_users.append(path.name)
        if ".write_text(" in text or ".write_bytes(" in text:
            direct_write_users.append(path.name)
    assert replace_users == ["json_store.py", "migration.py"]
    assert direct_write_users == ["migration.py"]


def test_real_cli_audit_claim_and_empty_claim_contract(repo_factory, capsys) -> None:
    root = repo_factory()
    assert cli_main(
        ["--root", str(root), "company", "audit-and-enqueue", "--json"]
    ) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["queue_delta"]["created"] == 1

    assert cli_main(
        ["--root", str(root), "company", "claim-next", "--model", "luna", "--json"]
    ) == 0
    claim = json.loads(capsys.readouterr().out)
    assert claim["execution_status"] == "completed"
    assert claim["task"]["target"]["company_ids"] == ["example-company"]
    queue_path = root / "data" / "queues" / "company-queue.json"
    before_empty = queue_path.read_bytes()

    assert cli_main(
        ["--root", str(root), "company", "claim-next", "--model", "luna", "--json"]
    ) == 0
    empty = json.loads(capsys.readouterr().out)
    assert empty["execution_status"] == "no_task"
    assert queue_path.read_bytes() == before_empty
