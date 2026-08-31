"""Small, explicit repairs for canonical data created by older V4 code."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .json_store import JsonStore, canonical_json_bytes, sha256_bytes
from .paths import RepoPaths
from .queue import format_timestamp
from .schemas import validate


QUALIFICATION_REPAIR_VERSION = "job_qualification_status_v1"


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


__all__ = ["QUALIFICATION_REPAIR_VERSION", "repair_job_qualification_statuses"]
