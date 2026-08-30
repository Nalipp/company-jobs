"""Repository path contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoPaths:
    root: Path

    @classmethod
    def discover(cls, root: Path | None = None) -> "RepoPaths":
        return cls((root or Path(__file__).resolve().parents[2]).resolve())

    @property
    def companies(self) -> Path:
        return self.root / "data" / "companies.json"

    @property
    def company_queue(self) -> Path:
        return self.root / "data" / "queues" / "company-queue.json"

    @property
    def job_queue(self) -> Path:
        return self.root / "data" / "queues" / "job-queue.json"

    @property
    def profile(self) -> Path:
        return self.root / "data" / "profiles" / "ai-product-frontend-v1.json"

    @property
    def research_archive(self) -> Path:
        return self.root / "archive" / "v3" / "company-research.json"

    @property
    def job_sources(self) -> Path:
        return self.root / "data" / "jobs" / "sources"

    @property
    def jobs_lock(self) -> Path:
        return self.root / "data" / "jobs.lock"

    @property
    def audit_events(self) -> Path:
        return self.root / "data" / "audit" / "events.jsonl"

    def queue_path(self, workflow: str) -> Path:
        return self.company_queue if workflow == "validate_companies" else self.job_queue

    def result_dir(self, workflow: str) -> Path:
        leaf = "companies" if workflow == "validate_companies" else "jobs"
        return self.root / "data" / "results" / leaf

    def journal_path(self, workflow: str) -> Path:
        leaf = "company" if workflow == "validate_companies" else "jobs"
        return self.root / "data" / "transactions" / f"{leaf}.journal.json"

