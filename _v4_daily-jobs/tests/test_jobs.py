from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from v4_daily_jobs.adapters import ashby
from v4_daily_jobs.jobs import (
    JobInvariantError,
    active_jobs,
    compare_snapshots,
    evaluate_jobs,
    evaluate_profile,
    rejection_counts_from_evaluation,
    reconcile_snapshot,
    record_failed_snapshot,
    semantic_candidates,
)
from v4_daily_jobs.schemas import validate


FIXTURES = Path(__file__).parent / "fixtures" / "adapters"
PROFILE_DIGEST = "a" * 64


def fixture(name: str):
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def profile() -> dict:
    return {
        "schema_version": 1,
        "profile_id": "ai-product-frontend-v1",
        "hard_constraints": {
            "location": {
                "mode": "city",
                "city": "San Francisco",
                "region": "CA",
                "country": "US",
                "broader_region_only_is_eligible": False,
            },
            "workplace": {
                "allowed_arrangements": ["hybrid", "on-site"],
                "unknown_policy": "include",
            },
            "compensation": {
                "threshold": 200000,
                "comparison": "published_maximum_at_least",
                "currency": "USD",
                "basis": "base",
                "period": "year",
                "unknown_policy": "exclude",
            },
            "employment": {
                "allowed_types": ["full-time"],
                "explicitly_disallowed_types": [
                    "part-time",
                    "contract",
                    "temporary",
                    "internship",
                ],
                "missing_type_policy": "assume_full_time",
            },
            "role": {
                "primary_titles": ["AI Product Engineer"],
                "adjacent_titles": ["Product Engineer", "Frontend Engineer"],
                "excluded_titles": [
                    "DevOps Engineer",
                    "Site Reliability Engineer",
                    "SRE",
                    "Platform Engineer",
                    "Infrastructure Engineer",
                ],
                "role_fit_policy": "title_or_responsibilities",
                "ai_involvement": {
                    "required": True,
                    "employer_ai_focus_alone_is_sufficient": False,
                },
            },
        },
    }


def result(*, complete: bool = True):
    payload = fixture("ashby.json")
    payload["jobs"] = [payload["jobs"][0]]
    payload["meta"]["total"] = 1
    return ashby.parse_payload(
        payload,
        company_id="acme",
        source_id="acme-ashby",
        board_id="acme",
        enumeration_complete=complete,
    )


def snapshot(previous, adapter_result, scanned_at, *, qualifying_jobs=None, unresolved=()):
    if qualifying_jobs is None:
        qualifying_jobs = list(adapter_result.jobs)
    return reconcile_snapshot(
        previous,
        adapter_result,
        scanned_at=scanned_at,
        source_revision=1,
        profile_id="ai-product-frontend-v1",
        profile_digest=PROFILE_DIGEST,
        qualifying_jobs=qualifying_jobs,
        unresolved_candidates=unresolved,
    )


class DeterministicProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = result().jobs[0].to_dict()

    def test_viable_structured_facts_still_require_ai_review(self) -> None:
        decision = evaluate_profile(self.job, profile())
        self.assertEqual(decision.status, "needs_semantic_review")
        self.assertEqual(decision.rejection_reasons, ())
        self.assertIn(
            "ai_involvement_requires_official_posting_review",
            decision.semantic_requirements,
        )
        self.assertNotIn("role_fit_requires_review", decision.semantic_requirements)

    def test_explicit_remote_rejects(self) -> None:
        job = deepcopy(self.job)
        job["workplace_arrangement"] = "remote"
        decision = evaluate_profile(job, profile())
        self.assertEqual(decision.status, "rejected")
        self.assertIn("workplace_remote_excluded", decision.rejection_reasons)
        self.assertEqual(decision.semantic_requirements, ())

    def test_city_only_location_rejects_bay_area_and_other_city(self) -> None:
        for location in (
            "San Francisco Bay Area",
            "South San Francisco, CA",
            "Oakland, CA",
        ):
            job = deepcopy(self.job)
            job["locations"] = [location]
            with self.subTest(location=location):
                decision = evaluate_profile(job, profile())
                self.assertIn(
                    "location_outside_target_city", decision.rejection_reasons
                )

    def test_location_alias_is_reviewed_instead_of_forced(self) -> None:
        job = deepcopy(self.job)
        job["locations"] = ["SF, CA"]
        decision = evaluate_profile(job, profile())
        self.assertEqual(decision.status, "needs_semantic_review")
        self.assertIn("location_requires_review", decision.semantic_requirements)

    def test_exact_explicit_hard_predicates_reject(self) -> None:
        cases = [
            ("employment_type", "contract", "employment_contract_excluded"),
            ("title", "Platform Engineer", "title_explicitly_excluded"),
        ]
        for field, value, reason in cases:
            job = deepcopy(self.job)
            job[field] = value
            with self.subTest(field=field):
                self.assertIn(
                    reason, evaluate_profile(job, profile()).rejection_reasons
                )

        low = deepcopy(self.job)
        low["compensation"]["maximum"] = 199999
        self.assertIn(
            "compensation_below_profile_threshold",
            evaluate_profile(low, profile()).rejection_reasons,
        )

    def test_unknown_facts_are_preserved_for_review(self) -> None:
        job = deepcopy(self.job)
        job["locations"] = []
        job["compensation"] = None
        job["title"] = "Creative Technologist"
        job["employment_type"] = None
        job["workplace_arrangement"] = "unknown"
        decision = evaluate_profile(job, profile())
        self.assertEqual(decision.status, "needs_semantic_review")
        self.assertIn("location_requires_review", decision.semantic_requirements)
        self.assertIn("compensation_requires_review", decision.semantic_requirements)
        self.assertIn("role_fit_requires_review", decision.semantic_requirements)
        self.assertIn(
            "employment_missing_assumed_full_time", decision.accepted_facts
        )
        self.assertIn(
            "workplace_unknown_included_by_profile", decision.accepted_facts
        )

    def test_explicit_non_target_job_families_are_rejected_without_model_work(self) -> None:
        for title in (
            "Senior Partner Marketing Manager",
            "Commercial Counsel",
            "Staff Product Manager, Growth",
            "Senior Technical Recruiter",
            "Enterprise Account Executive",
        ):
            job = deepcopy(self.job)
            job["title"] = title
            with self.subTest(title=title):
                decision = evaluate_profile(job, profile())
                self.assertIn(
                    "title_explicitly_non_target_function",
                    decision.rejection_reasons,
                )
                self.assertEqual(decision.semantic_requirements, ())

    def test_engineering_titles_are_not_rejected_by_non_target_shortcut(self) -> None:
        for title in (
            "GTM Engineering Manager",
            "Product Marketing Engineer",
            "Developer Support Engineer",
        ):
            job = deepcopy(self.job)
            job["title"] = title
            with self.subTest(title=title):
                self.assertNotIn(
                    "title_explicitly_non_target_function",
                    evaluate_profile(job, profile()).rejection_reasons,
                )

    def test_unrecognized_explicit_employment_label_requires_review(self) -> None:
        job = deepcopy(self.job)
        job["employment_type"] = "regular"
        decision = evaluate_profile(job, profile())
        self.assertEqual(decision.status, "needs_semantic_review")
        self.assertIn(
            "employment_type_requires_review", decision.semantic_requirements
        )

    def test_multi_location_passes_when_one_exact_city_is_present(self) -> None:
        job = deepcopy(self.job)
        job["locations"] = ["New York, NY", "San Francisco, CA"]
        decision = evaluate_profile(job, profile())
        self.assertNotIn(
            "location_outside_target_city", decision.rejection_reasons
        )
        self.assertIn("location_exact_city", decision.accepted_facts)

    def test_evaluate_jobs_returns_reconciled_counts_and_order(self) -> None:
        rejected = deepcopy(self.job)
        rejected["stable_id"] = "job-000"
        rejected["workplace_arrangement"] = "remote"
        summary = evaluate_jobs([self.job, rejected], profile())
        self.assertEqual(summary["counts"]["normalized"], 2)
        self.assertEqual(summary["counts"]["deterministically_rejected"], 1)
        self.assertEqual(summary["counts"]["needs_semantic_review"], 1)
        self.assertEqual(summary["decisions"][0]["stable_id"], "job-000")

    def test_semantic_candidate_and_rejection_helpers_preserve_details(self) -> None:
        rejected = deepcopy(self.job)
        rejected["stable_id"] = "job-000"
        rejected["workplace_arrangement"] = "remote"
        evaluation = evaluate_jobs([self.job, rejected], profile())
        candidates = semantic_candidates([self.job, rejected], evaluation)
        self.assertEqual([item["stable_id"] for item in candidates], [self.job["stable_id"]])
        counts = rejection_counts_from_evaluation(evaluation)
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["workplace_remote_excluded"], 1)


class SnapshotTests(unittest.TestCase):
    def test_first_complete_snapshot_is_current(self) -> None:
        value = snapshot(None, result(), "2026-08-29T10:00:00Z")
        self.assertTrue(value["fetch_complete"])
        self.assertEqual(value["last_successful_scan_at"], "2026-08-29T10:00:00Z")
        self.assertEqual(len(active_jobs(value)), 1)
        self.assertEqual(value["jobs"][0]["first_seen_at"], "2026-08-29T10:00:00Z")
        validate(value, "job-snapshot.schema.json")

    def test_complete_enumeration_closes_disappeared_job(self) -> None:
        previous = snapshot(None, result(), "2026-08-29T10:00:00Z")
        empty_payload = {"jobs": [], "meta": {"total": 0}}
        empty = ashby.parse_payload(
            empty_payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        current = snapshot(
            previous, empty, "2026-08-29T11:00:00Z", qualifying_jobs=[]
        )
        self.assertEqual(current["jobs"][0]["status"], "inactive")
        self.assertEqual(current["jobs"][0]["inactive_at"], "2026-08-29T11:00:00Z")
        self.assertEqual(
            compare_snapshots(previous, current)["closed"],
            [current["jobs"][0]["stable_id"]],
        )

    def test_incomplete_enumeration_never_closes_disappeared_job(self) -> None:
        previous = snapshot(None, result(), "2026-08-29T10:00:00Z")
        incomplete = ashby.parse_payload(
            {"jobs": [], "meta": {"total": 0}},
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
            enumeration_complete=False,
        )
        current = snapshot(
            previous, incomplete, "2026-08-29T11:00:00Z", qualifying_jobs=[]
        )
        self.assertFalse(current["fetch_complete"])
        self.assertEqual(current["jobs"], previous["jobs"])
        self.assertEqual(
            current["last_successful_scan_at"], previous["last_successful_scan_at"]
        )

    def test_failed_attempt_preserves_jobs_and_success_watermark(self) -> None:
        previous = snapshot(None, result(), "2026-08-29T10:00:00Z")
        failed = record_failed_snapshot(
            previous,
            company_id="acme",
            source_id="acme-ashby",
            provider="ashby",
            attempted_at="2026-08-29T11:00:00Z",
            failure_code="network_timeout",
            failure_detail="provider did not respond",
            source_revision=1,
            profile_id="ai-product-frontend-v1",
            profile_digest=PROFILE_DIGEST,
        )
        self.assertFalse(failed["fetch_complete"])
        self.assertEqual(failed["jobs"], previous["jobs"])
        self.assertEqual(
            failed["last_successful_scan_at"], previous["last_successful_scan_at"]
        )
        validate(failed, "job-snapshot.schema.json")

    def test_inactive_job_reopens_when_observed_again(self) -> None:
        previous = snapshot(None, result(), "2026-08-29T10:00:00Z")
        empty = ashby.parse_payload(
            {"jobs": [], "meta": {"total": 0}},
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        inactive = snapshot(
            previous, empty, "2026-08-29T11:00:00Z", qualifying_jobs=[]
        )
        reopened = snapshot(inactive, result(), "2026-08-29T12:00:00Z")
        record = reopened["jobs"][0]
        self.assertEqual(record["status"], "active")
        self.assertIsNone(record["inactive_at"])
        self.assertEqual(record["first_seen_at"], "2026-08-29T10:00:00Z")
        self.assertEqual(
            compare_snapshots(inactive, reopened)["reopened"], [record["stable_id"]]
        )

    def test_changed_and_unchanged_postings_have_stable_lifecycle(self) -> None:
        first_result = result()
        previous = snapshot(None, first_result, "2026-08-29T10:00:00Z")
        unchanged = snapshot(previous, result(), "2026-08-29T11:00:00Z")
        stable_id = unchanged["jobs"][0]["stable_id"]
        self.assertEqual(compare_snapshots(previous, unchanged)["unchanged"], [stable_id])
        self.assertEqual(
            unchanged["jobs"][0]["first_seen_at"],
            previous["jobs"][0]["first_seen_at"],
        )

        changed_payload = fixture("ashby.json")
        changed_payload["jobs"] = [changed_payload["jobs"][0]]
        changed_payload["jobs"][0]["title"] = "AI Product Engineer"
        changed_payload["meta"]["total"] = 1
        changed_result = ashby.parse_payload(
            changed_payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        changed = snapshot(unchanged, changed_result, "2026-08-29T12:00:00Z")
        self.assertEqual(compare_snapshots(unchanged, changed)["updated"], [stable_id])
        self.assertEqual(changed["jobs"][0]["first_seen_at"], "2026-08-29T10:00:00Z")

    def test_source_ownership_and_duplicates_fail(self) -> None:
        prior = snapshot(None, result(), "2026-08-29T10:00:00Z")
        wrong = deepcopy(prior)
        wrong["source_id"] = "wrong-source"
        with self.assertRaisesRegex(JobInvariantError, "own"):
            snapshot(wrong, result(), "2026-08-29T11:00:00Z")

        duplicate = deepcopy(prior)
        duplicate["jobs"].append(deepcopy(duplicate["jobs"][0]))
        with self.assertRaisesRegex(JobInvariantError, "duplicate"):
            snapshot(duplicate, result(), "2026-08-29T11:00:00Z")

    def test_serialized_adapter_result_is_revalidated_by_finalizer_boundary(self) -> None:
        serialized = result().to_dict()
        serialized["jobs"][0]["stable_id"] = "job-forged"
        with self.assertRaisesRegex(JobInvariantError, "stable_id"):
            reconcile_snapshot(
                None,
                serialized,
                scanned_at="2026-08-29T10:00:00Z",
                source_revision=1,
                profile_id="ai-product-frontend-v1",
                profile_digest=PROFILE_DIGEST,
                qualifying_jobs=[],
            )

        wrong_count = result().to_dict()
        wrong_count["fetch"]["unique_jobs"] = 99
        with self.assertRaisesRegex(JobInvariantError, "unique_jobs"):
            reconcile_snapshot(
                None,
                wrong_count,
                scanned_at="2026-08-29T10:00:00Z",
                source_revision=1,
                profile_id="ai-product-frontend-v1",
                profile_digest=PROFILE_DIGEST,
                qualifying_jobs=[],
            )

    def test_semantic_annotations_cannot_overwrite_adapter_identity_facts(self) -> None:
        adapter_result = result()
        forged = adapter_result.jobs[0].to_dict()
        forged["title"] = "A different title"
        forged["ai_involvement_evidence"] = "Official posting evidence"
        with self.assertRaisesRegex(JobInvariantError, "cannot overwrite.*title"):
            snapshot(
                None,
                adapter_result,
                "2026-08-29T10:00:00Z",
                qualifying_jobs=[forged],
            )

        forged_compensation = adapter_result.jobs[0].to_dict()
        forged_compensation["compensation"] = {
            "minimum": 900000,
            "maximum": 1000000,
            "currency": "USD",
            "basis": "base",
            "period": "year",
            "source_text": "forged",
        }
        with self.assertRaisesRegex(JobInvariantError, "cannot overwrite.*compensation"):
            snapshot(
                None,
                adapter_result,
                "2026-08-29T10:00:00Z",
                qualifying_jobs=[forged_compensation],
            )

        with self.assertRaisesRegex(JobInvariantError, "unsupported semantic annotations"):
            snapshot(
                None,
                adapter_result,
                "2026-08-29T10:00:00Z",
                qualifying_jobs=[
                    {
                        "stable_id": adapter_result.jobs[0].stable_id,
                        "invented_provider_fact": "not allowed",
                    }
                ],
            )

        annotated = adapter_result.jobs[0].to_dict()
        annotated["ai_involvement_evidence"] = "Official posting evidence"
        value = snapshot(
            None,
            adapter_result,
            "2026-08-29T10:00:00Z",
            qualifying_jobs=[annotated],
        )
        self.assertEqual(
            value["jobs"][0]["ai_involvement_evidence"],
            "Official posting evidence",
        )
        self.assertEqual(value["jobs"][0]["qualification_status"], "qualifying")

        deterministic = snapshot(
            None,
            adapter_result,
            "2026-08-29T10:00:00Z",
            qualifying_jobs=[adapter_result.jobs[0].stable_id],
        )
        self.assertEqual(
            deterministic["jobs"][0]["qualification_status"],
            "deterministic_pass",
        )

        with self.assertRaisesRegex(JobInvariantError, "invalid qualification_status"):
            snapshot(
                None,
                adapter_result,
                "2026-08-29T10:00:00Z",
                qualifying_jobs=[
                    {
                        "stable_id": adapter_result.jobs[0].stable_id,
                        "qualification_status": "rejected",
                    }
                ],
            )

    def test_incomplete_fetch_preserves_prior_unresolved_candidate(self) -> None:
        initial_result = result()
        prior = snapshot(
            None,
            initial_result,
            "2026-08-29T10:00:00Z",
            qualifying_jobs=[],
            unresolved=list(initial_result.jobs),
        )
        incomplete = ashby.parse_payload(
            {"jobs": [], "meta": {"total": 0}},
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
            enumeration_complete=False,
        )
        current = snapshot(
            prior,
            incomplete,
            "2026-08-29T11:00:00Z",
            qualifying_jobs=[],
            unresolved=[],
        )
        self.assertEqual(current["unresolved_candidates"], prior["unresolved_candidates"])
        self.assertIn("current_job_count_is_observed_partial_count", current["warnings"])

    def test_changed_profile_requires_complete_enumeration(self) -> None:
        prior = snapshot(None, result(), "2026-08-29T10:00:00Z")
        incomplete = result(complete=False)
        with self.assertRaisesRegex(JobInvariantError, "changed profile"):
            reconcile_snapshot(
                prior,
                incomplete,
                scanned_at="2026-08-29T11:00:00Z",
                source_revision=1,
                profile_id="ai-product-frontend-v1",
                profile_digest="b" * 64,
                qualifying_jobs=[],
            )


if __name__ == "__main__":
    unittest.main()
