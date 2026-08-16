from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "apply_handoff.py"
T1 = "2026-08-09T18:00:00Z"
T2 = "2026-08-09T19:00:00Z"
T3 = "2026-08-09T20:00:00Z"


def company(
    company_id: str,
    name: str,
    *,
    website: str | None = None,
    status: str = "pending",
    address: dict | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    source_url: str | None = None,
    source_label: str | None = None,
    verified_date: str | None = None,
    timestamp: str = T1,
) -> dict:
    return {
        "company_id": company_id,
        "company_name": name,
        "website_url": website,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "address_status": status,
        "address_source_url": source_url,
        "address_source_label": source_label,
        "verified_date": verified_date,
        "added_date": timestamp,
        "updated_date": timestamp,
    }


def sf_address(street: str = "123 Market St") -> dict:
    return {
        "street": street,
        "city": "San Francisco",
        "region": "CA",
        "postal_code": "94105",
        "country": "US",
    }


def career_source(
    company_id: str = "acme-ai", *, timestamp: str = T2, status: str = "ready"
) -> dict:
    return {
        "source_id": "acme-ai-greenhouse",
        "company_id": company_id,
        "careers_url": "https://acme.example/careers",
        "listings_url": "https://job-boards.greenhouse.io/acmeai",
        "provider": "greenhouse",
        "board_id": "acmeai",
        "access_mode": "structured_feed",
        "scope": "All roles",
        "instructions": "Enumerate the public board and open canonical job details.",
        "capabilities": {
            "complete_enumeration": status == "ready",
            "stable_job_identity": status == "ready",
            "structured_data": True,
        },
        "status": status,
        "official_relationship_url": "https://acme.example/careers",
        "checked_at": timestamp,
        "last_success_at": timestamp if status == "ready" else None,
        "added_date": timestamp,
        "updated_date": timestamp,
    }


def job_observation(*, workplace: str = "hybrid") -> dict:
    return {
        "company_id": "acme-ai",
        "source_id": "acme-ai-greenhouse",
        "source_job_id": "job-123",
        "canonical_url": "https://job-boards.greenhouse.io/acmeai/jobs/job-123",
        "title": "Senior AI Product Engineer",
        "location_text": "San Francisco, CA",
        "location": {"city": "San Francisco", "region": "CA", "country": "US"},
        "workplace_arrangement": workplace,
        "workplace_text": "Hybrid in San Francisco",
        "employment_type": None,
        "employment_text": None,
        "seniority": "senior",
        "compensation": {
            "minimum": 180000,
            "maximum": 230000,
            "currency": "USD",
            "basis": "base",
            "period": "year",
            "source_text": "$180,000-$230,000 base salary",
        },
        "posted_date": "2026-08-01",
        "evidence": {
            "active_status": "Listed on the current official board.",
            "location": "San Francisco, CA",
            "workplace_arrangement": "Hybrid in San Francisco",
            "employment_type": None,
            "compensation": "$180,000-$230,000 base salary",
            "seniority": "Senior",
            "role_fit": "Build user-facing product features across frontend and backend.",
            "ai_involvement": "Build LLM-powered product workflows and evaluations.",
        },
    }


def job_scan(
    *, outcome: str = "success_matches", observation: dict | None = None
) -> dict:
    observation = observation or job_observation()
    if outcome == "success_matches":
        identities = [
            {
                "source_job_id": observation["source_job_id"],
                "canonical_url": observation["canonical_url"],
            }
        ]
        matching_jobs = [observation]
        current_job_count = 1
        failure_details = None
    elif outcome == "success_zero_jobs":
        identities = []
        matching_jobs = []
        current_job_count = 0
        failure_details = None
    elif outcome == "success_no_matches":
        identities = [
            {
                "source_job_id": observation["source_job_id"],
                "canonical_url": observation["canonical_url"],
            }
        ]
        matching_jobs = []
        current_job_count = 1
        failure_details = None
    elif outcome == "failed":
        identities = []
        matching_jobs = []
        current_job_count = None
        failure_details = "The source could not be enumerated."
    else:
        raise ValueError(outcome)
    return {
        "company_id": "acme-ai",
        "source_id": "acme-ai-greenhouse",
        "expected_source_updated_date": T1,
        "expected_last_successful_scan_at": None,
        "outcome": outcome,
        "scanned_at": T2,
        "current_job_count": current_job_count,
        "active_job_identities": identities,
        "matching_jobs": matching_jobs,
        "failure_details": failure_details,
    }


class ApplyHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.catalog_path = self.directory / "companies.json"
        self.career_sources_path = self.directory / "career_sources.json"
        self.job_results_dir = self.directory / "job-results"
        self.handoff_path = self.directory / "handoff.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_catalog(self, companies: list[dict]) -> None:
        self.catalog_path.write_text(
            json.dumps({"schema_version": 1, "updated_at": None, "companies": companies}),
            encoding="utf-8",
        )

    def write_handoff(
        self,
        operation: str,
        changes: list[dict],
        timestamp: str = T2,
        profile_id: str | None = None,
    ) -> None:
        envelope = {
            "schema_version": 1,
            "operation": operation,
            "created_at": timestamp,
            "changes": changes,
            "issues": [],
        }
        if profile_id is not None:
            envelope["profile_id"] = profile_id
        self.handoff_path.write_text(
            json.dumps(envelope),
            encoding="utf-8",
        )

    def write_career_sources(self, sources: list[dict]) -> None:
        self.career_sources_path.write_text(
            json.dumps({"schema_version": 1, "updated_at": None, "sources": sources}),
            encoding="utf-8",
        )

    def write_job_results(self, jobs: list[dict]) -> None:
        self.job_results_dir.mkdir(parents=True, exist_ok=True)
        (self.job_results_dir / "ai-product-frontend-v1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile_id": "ai-product-frontend-v1",
                    "updated_at": None,
                    "source_states": [],
                    "jobs": jobs,
                }
            ),
            encoding="utf-8",
        )

    def run_apply(self, expected_code: int = 0) -> dict:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(self.handoff_path),
                "--catalog",
                str(self.catalog_path),
                "--career-sources",
                str(self.career_sources_path),
                "--job-results-dir",
                str(self.job_results_dir),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected_code, result.stderr or result.stdout)
        return json.loads(result.stdout if result.stdout else result.stderr)

    def read_catalog(self) -> dict:
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def read_career_sources(self) -> dict:
        return json.loads(self.career_sources_path.read_text(encoding="utf-8"))

    def read_job_results(self) -> dict:
        return json.loads(
            (self.job_results_dir / "ai-product-frontend-v1.json").read_text(
                encoding="utf-8"
            )
        )

    def test_ingest_appends_and_replay_is_noop(self) -> None:
        self.write_catalog([])
        self.write_handoff(
            "company-ingest",
            [company("acme-ai", "Acme AI", website="https://acme.example", timestamp=T2)],
        )

        first = self.run_apply()
        self.assertTrue(first["catalog_changed"])
        self.assertEqual(first["applied"], 1)
        self.assertEqual([c["company_id"] for c in self.read_catalog()["companies"]], ["acme-ai"])

        second = self.run_apply()
        self.assertFalse(second["catalog_changed"])
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_ingest_matches_domain_and_fills_missing_address(self) -> None:
        existing = company(
            "acme",
            "Acme, Inc.",
            website="https://www.acme.example/about",
            status="not_found",
            verified_date=T1,
        )
        self.write_catalog([existing])
        incoming = company(
            "acme-inc",
            "ACME Incorporated",
            website="https://acme.example",
            address=sf_address(),
            latitude=37.793,
            longitude=-122.397,
            source_url="https://acme.example/contact",
            source_label="Official contact page",
            timestamp=T2,
        )
        self.write_handoff("company-ingest", [incoming])

        result = self.run_apply()
        saved = self.read_catalog()["companies"]
        self.assertEqual(result["applied"], 1)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["company_id"], "acme")
        self.assertEqual(saved[0]["address_status"], "pending")
        self.assertIsNone(saved[0]["verified_date"])
        self.assertEqual(saved[0]["address"]["street"], "123 Market St")

    def test_ingest_does_not_replace_verified_address(self) -> None:
        existing = company(
            "acme-ai",
            "Acme AI",
            website="https://acme.example",
            status="verified",
            address=sf_address("1 Existing St"),
            latitude=37.79,
            longitude=-122.40,
            source_url="https://acme.example/contact",
            source_label="Official contact page",
            verified_date=T1,
        )
        self.write_catalog([existing])
        incoming = company(
            "acme-ai",
            "Acme AI",
            website="https://acme.example",
            address=sf_address("999 Incoming St"),
            latitude=37.70,
            longitude=-122.50,
            timestamp=T2,
        )
        self.write_handoff("company-ingest", [incoming])

        result = self.run_apply()
        saved = self.read_catalog()["companies"][0]
        self.assertFalse(result["catalog_changed"])
        self.assertEqual(saved["address"]["street"], "1 Existing St")
        self.assertEqual(saved["address_status"], "verified")

    def test_validation_applies_and_replay_is_noop(self) -> None:
        self.write_catalog([company("acme-ai", "Acme AI")])
        address_result = {
            "address": sf_address(),
            "latitude": 37.793,
            "longitude": -122.397,
            "address_status": "verified",
            "address_source_url": "https://acme.example/contact",
            "address_source_label": "Official contact page",
            "verified_date": T2,
        }
        self.write_handoff(
            "company-validate",
            [
                {
                    "company_id": "acme-ai",
                    "expected_status": "pending",
                    "address_result": address_result,
                }
            ],
        )

        first = self.run_apply()
        self.assertEqual(first["applied"], 1)
        self.assertEqual(self.read_catalog()["companies"][0]["address_status"], "verified")

        second = self.run_apply()
        self.assertFalse(second["catalog_changed"])
        self.assertEqual(second["skipped"], 1)

    def test_validation_rejects_stale_expected_status(self) -> None:
        existing = company(
            "acme-ai",
            "Acme AI",
            status="verified",
            address=sf_address(),
            latitude=37.793,
            longitude=-122.397,
            source_url="https://acme.example/contact",
            source_label="Official contact page",
            verified_date=T1,
        )
        self.write_catalog([existing])
        self.write_handoff(
            "company-validate",
            [
                {
                    "company_id": "acme-ai",
                    "expected_status": "pending",
                    "address_result": {
                        "address": sf_address("999 New St"),
                        "latitude": 37.70,
                        "longitude": -122.50,
                        "address_status": "verified",
                        "address_source_url": "https://example.com/new",
                        "address_source_label": "New source",
                        "verified_date": T2,
                    },
                }
            ],
        )

        result = self.run_apply(expected_code=2)
        self.assertFalse(result["catalog_changed"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(self.read_catalog()["companies"][0]["address"]["street"], "123 Market St")

    def test_validation_fills_missing_website_and_replay_is_noop(self) -> None:
        self.write_catalog([company("acme-ai", "Acme AI")])
        address_result = {
            "address": sf_address(),
            "latitude": 37.793,
            "longitude": -122.397,
            "address_status": "verified",
            "address_source_url": "https://acme.example/contact",
            "address_source_label": "Official contact page",
            "verified_date": T2,
        }
        self.handoff_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "company-validate",
                    "created_at": T2,
                    "changes": [
                        {
                            "company_id": "acme-ai",
                            "expected_status": "pending",
                            "website_url": "https://www.acme.example/",
                            "address_result": address_result,
                        }
                    ],
                    "issues": [],
                }
            ),
            encoding="utf-8",
        )

        first = self.run_apply()
        saved = self.read_catalog()["companies"][0]
        self.assertEqual(first["applied"], 1)
        self.assertEqual(saved["website_url"], "https://www.acme.example/")
        self.assertEqual(saved["address_status"], "verified")

        second = self.run_apply()
        self.assertFalse(second["catalog_changed"])
        self.assertEqual(second["skipped"], 1)

    def test_validation_preserves_conflicting_website(self) -> None:
        self.write_catalog(
            [company("acme-ai", "Acme AI", website="https://acme.example/")]
        )
        self.handoff_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "company-validate",
                    "created_at": T2,
                    "changes": [
                        {
                            "company_id": "acme-ai",
                            "expected_status": "pending",
                            "website_url": "https://different.example/",
                            "address_result": {
                                "address": sf_address(),
                                "latitude": 37.793,
                                "longitude": -122.397,
                                "address_status": "verified",
                                "address_source_url": "https://acme.example/contact",
                                "address_source_label": "Official contact page",
                                "verified_date": T2,
                            },
                        }
                    ],
                    "issues": [],
                }
            ),
            encoding="utf-8",
        )

        result = self.run_apply(expected_code=2)
        saved = self.read_catalog()["companies"][0]
        self.assertEqual(saved["website_url"], "https://acme.example/")
        self.assertEqual(saved["address_status"], "verified")
        self.assertEqual(result["conflicts"][0]["field"], "website_url")

    def test_validation_enriches_verified_website_without_address_result(self) -> None:
        self.write_catalog(
            [
                company(
                    "acme-ai",
                    "Acme AI",
                    status="verified",
                    address=sf_address(),
                    latitude=37.793,
                    longitude=-122.397,
                    source_url="https://acme.example/contact",
                    source_label="Official contact page",
                    verified_date=T1,
                )
            ]
        )
        self.write_handoff(
            "company-validate",
            [
                {
                    "company_id": "acme-ai",
                    "expected_status": "verified",
                    "website_url": "https://acme.example/",
                }
            ],
        )

        first = self.run_apply()
        saved = self.read_catalog()["companies"][0]
        self.assertEqual(first["applied"], 1)
        self.assertEqual(saved["website_url"], "https://acme.example/")
        self.assertEqual(saved["address"], sf_address())
        self.assertEqual(saved["address_status"], "verified")

        second = self.run_apply()
        self.assertFalse(second["catalog_changed"])
        self.assertEqual(second["skipped"], 1)

    def test_career_source_discovery_applies_and_replay_is_noop(self) -> None:
        self.write_catalog(
            [
                company(
                    "acme-ai",
                    "Acme AI",
                    status="verified",
                    address=sf_address(),
                    latitude=37.793,
                    longitude=-122.397,
                    source_url="https://acme.example/contact",
                    source_label="Official contact page",
                    verified_date=T1,
                )
            ]
        )
        self.write_career_sources([])
        self.write_handoff(
            "career-source-discover",
            [{"expected_updated_date": None, "source": career_source()}],
        )

        first = self.run_apply()
        self.assertFalse(first["catalog_changed"])
        self.assertTrue(first["career_sources_changed"])
        self.assertEqual(first["applied"], 1)
        saved = self.read_career_sources()
        self.assertEqual(saved["updated_at"], T2)
        self.assertEqual(saved["sources"][0]["source_id"], "acme-ai-greenhouse")

        second = self.run_apply()
        self.assertFalse(second["career_sources_changed"])
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_career_source_discovery_preserves_ready_source_on_failed_refresh(self) -> None:
        existing_source = career_source(timestamp=T1)
        self.write_catalog(
            [
                company(
                    "acme-ai",
                    "Acme AI",
                    status="verified",
                    address=sf_address(),
                    latitude=37.793,
                    longitude=-122.397,
                    source_url="https://acme.example/contact",
                    source_label="Official contact page",
                    verified_date=T1,
                )
            ]
        )
        self.write_career_sources([existing_source])
        blocked = career_source(timestamp=T2, status="blocked")
        blocked["added_date"] = T1
        self.write_handoff(
            "career-source-discover",
            [{"expected_updated_date": T1, "source": blocked}],
        )

        result = self.run_apply(expected_code=2)
        self.assertFalse(result["career_sources_changed"])
        self.assertEqual(self.read_career_sources()["sources"][0]["status"], "ready")
        self.assertEqual(result["conflicts"][0]["field"], "source.status")

    def setup_job_scan_state(self) -> None:
        self.write_catalog(
            [
                company(
                    "acme-ai",
                    "Acme AI",
                    status="verified",
                    address=sf_address(),
                    latitude=37.793,
                    longitude=-122.397,
                    source_url="https://acme.example/contact",
                    source_label="Official contact page",
                    verified_date=T1,
                )
            ]
        )
        self.write_career_sources([career_source(timestamp=T1)])
        self.write_job_results([])

    def test_job_scan_adds_match_and_replay_is_noop(self) -> None:
        self.setup_job_scan_state()
        self.write_handoff(
            "job-scan",
            [job_scan()],
            profile_id="ai-product-frontend-v1",
        )

        first = self.run_apply()
        self.assertTrue(first["job_results_changed"])
        self.assertEqual(first["applied"], 1)
        saved = self.read_job_results()["jobs"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["status"], "active")
        self.assertEqual(saved[0]["first_seen_at"], T2)

        second = self.run_apply()
        self.assertFalse(second["job_results_changed"])
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_failed_job_scan_preserves_jobs_and_complete_empty_scan_deactivates(self) -> None:
        self.setup_job_scan_state()
        self.write_handoff(
            "job-scan",
            [job_scan()],
            profile_id="ai-product-frontend-v1",
        )
        self.run_apply()

        failed_scan = job_scan(outcome="failed")
        failed_scan["scanned_at"] = T3
        failed_scan["expected_last_successful_scan_at"] = T2
        self.write_handoff(
            "job-scan",
            [failed_scan],
            timestamp=T3,
            profile_id="ai-product-frontend-v1",
        )
        failed = self.run_apply()
        self.assertFalse(failed["job_results_changed"])
        self.assertEqual(self.read_job_results()["jobs"][0]["status"], "active")

        empty_scan = job_scan(outcome="success_zero_jobs")
        empty_scan["scanned_at"] = T3
        empty_scan["expected_last_successful_scan_at"] = T2
        self.write_handoff(
            "job-scan",
            [empty_scan],
            timestamp=T3,
            profile_id="ai-product-frontend-v1",
        )
        complete = self.run_apply()
        saved = self.read_job_results()["jobs"][0]
        self.assertTrue(complete["job_results_changed"])
        self.assertEqual(saved["status"], "inactive")
        self.assertEqual(saved["inactive_at"], T3)

    def test_job_scan_rejects_explicit_remote_match(self) -> None:
        self.setup_job_scan_state()
        remote = job_observation(workplace="remote")
        self.write_handoff(
            "job-scan",
            [job_scan(observation=remote)],
            profile_id="ai-product-frontend-v1",
        )

        result = self.run_apply(expected_code=2)
        self.assertFalse(result["job_results_changed"])
        self.assertEqual(self.read_job_results()["jobs"], [])
        self.assertIn("excluded by profile", result["conflicts"][0]["details"])

    def test_job_scan_accepts_unknown_workplace(self) -> None:
        self.setup_job_scan_state()
        unknown = job_observation(workplace="unknown")
        unknown["workplace_text"] = None
        unknown["evidence"]["workplace_arrangement"] = None
        self.write_handoff(
            "job-scan",
            [job_scan(observation=unknown)],
            profile_id="ai-product-frontend-v1",
        )

        result = self.run_apply()
        self.assertTrue(result["job_results_changed"])
        self.assertEqual(
            self.read_job_results()["jobs"][0]["workplace_arrangement"], "unknown"
        )

    def test_job_scan_rejects_stale_source_watermark(self) -> None:
        self.setup_job_scan_state()
        self.write_handoff(
            "job-scan",
            [job_scan()],
            profile_id="ai-product-frontend-v1",
        )
        self.run_apply()

        stale = job_scan(outcome="success_zero_jobs")
        stale["scanned_at"] = T3
        self.write_handoff(
            "job-scan",
            [stale],
            timestamp=T3,
            profile_id="ai-product-frontend-v1",
        )
        result = self.run_apply(expected_code=2)

        self.assertFalse(result["job_results_changed"])
        self.assertEqual(self.read_job_results()["jobs"][0]["status"], "active")
        self.assertEqual(result["conflicts"][0]["field"], "last_successful_scan_at")

    def test_existing_job_stays_active_when_identity_exists_but_is_not_reemitted(self) -> None:
        self.setup_job_scan_state()
        self.write_handoff(
            "job-scan",
            [job_scan()],
            profile_id="ai-product-frontend-v1",
        )
        self.run_apply()

        no_matches = job_scan(outcome="success_no_matches")
        no_matches["scanned_at"] = T3
        no_matches["expected_last_successful_scan_at"] = T2
        self.write_handoff(
            "job-scan",
            [no_matches],
            timestamp=T3,
            profile_id="ai-product-frontend-v1",
        )
        result = self.run_apply()

        saved = self.read_job_results()
        self.assertTrue(result["job_results_changed"])
        self.assertEqual(saved["jobs"][0]["status"], "active")
        self.assertEqual(saved["jobs"][0]["updated_at"], T2)
        self.assertEqual(saved["source_states"][0]["last_successful_scan_at"], T3)


if __name__ == "__main__":
    unittest.main()
