from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from v4_daily_jobs.adapters import AdapterError, parse_provider_payload
from v4_daily_jobs.adapters import ashby, greenhouse, lever
from v4_daily_jobs.adapters.base import normalize_url, stable_job_id


FIXTURES = Path(__file__).parent / "fixtures" / "adapters"


def fixture(name: str):
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


class AshbyAdapterTests(unittest.TestCase):
    def test_parses_listed_jobs_and_explicit_structured_facts(self) -> None:
        result = ashby.parse_payload(
            fixture("ashby.json"),
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.fetch.raw_jobs_seen, 3)
        self.assertEqual(result.fetch.unique_jobs, 2)
        jobs = {job.source_job_id: job for job in result.jobs}
        good = jobs["ashby-good-1"]
        self.assertEqual(good.locations, ("San Francisco, CA",))
        self.assertEqual(good.workplace_arrangement, "hybrid")
        self.assertEqual(good.employment_type, "full-time")
        self.assertEqual(good.compensation["maximum"], 240000)
        self.assertEqual(good.compensation["period"], "year")
        self.assertEqual(good.description_text, "Build AI-powered user experiences.")
        self.assertNotIn("utm_", good.canonical_url)

    def test_false_is_remote_does_not_invent_onsite(self) -> None:
        payload = fixture("ashby.json")
        payload["jobs"][0].pop("workplaceType")
        result = ashby.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        good = next(job for job in result.jobs if job.source_job_id == "ashby-good-1")
        self.assertEqual(good.workplace_arrangement, "unknown")

    def test_reported_total_mismatch_is_incomplete(self) -> None:
        payload = fixture("ashby.json")
        payload["meta"]["total"] = 99
        result = ashby.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.fetch.completeness_reason, "reported_total_mismatch")

    def test_unknown_api_version_fails_closed(self) -> None:
        payload = fixture("ashby.json")
        payload["apiVersion"] = "2"
        with self.assertRaisesRegex(AdapterError, "unsupported Ashby"):
            ashby.parse_payload(
                payload,
                company_id="acme",
                source_id="acme-ashby",
                board_id="acme",
            )

    def test_different_geographic_compensation_tiers_remain_ambiguous(self) -> None:
        payload = fixture("ashby.json")
        second = deepcopy(
            payload["jobs"][0]["compensation"]["compensationTiers"][0]
        )
        second["title"] = "Zone B"
        second["components"][0]["minValue"] = 150000
        second["components"][0]["maxValue"] = 190000
        payload["jobs"][0]["compensation"]["compensationTiers"].append(second)
        result = ashby.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        good = next(job for job in result.jobs if job.source_job_id == "ashby-good-1")
        self.assertIsNone(good.compensation)
        self.assertIsNotNone(good.compensation_text)

    def test_identical_overlap_deduplicates_but_conflict_fails(self) -> None:
        payload = fixture("ashby.json")
        payload["jobs"].append(deepcopy(payload["jobs"][0]))
        payload["meta"]["total"] = 4
        result = ashby.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        self.assertEqual(result.fetch.duplicates_removed, 1)

        conflicting = deepcopy(payload)
        conflicting["jobs"][-1]["title"] = "A different title"
        with self.assertRaisesRegex(AdapterError, "conflicting duplicate"):
            ashby.parse_payload(
                conflicting,
                company_id="acme",
                source_id="acme-ashby",
                board_id="acme",
            )


class GreenhouseAdapterTests(unittest.TestCase):
    def test_parses_metadata_and_html(self) -> None:
        result = greenhouse.parse_payload(
            fixture("greenhouse.json"),
            company_id="acme",
            source_id="acme-greenhouse",
            board_id="acme",
        )
        self.assertTrue(result.complete)
        jobs = {job.source_job_id: job for job in result.jobs}
        good = jobs["101"]
        self.assertEqual(good.workplace_arrangement, "on-site")
        self.assertEqual(good.employment_type, "regular")
        self.assertEqual(good.team, "Applications")
        self.assertEqual(good.compensation["maximum"], 225000)
        self.assertEqual(good.description_text, "Ship AI product workflows.")
        self.assertIn("gh_jid=101", good.canonical_url)
        self.assertNotIn("utm_medium", good.canonical_url)

    def test_greenhouse_job_identity_query_is_not_treated_as_tracking(self) -> None:
        payload = fixture("greenhouse.json")
        for job in payload["jobs"]:
            job["absolute_url"] = "https://example.com/careers?gh_jid=" + str(job["id"])
        result = greenhouse.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-greenhouse",
            board_id="acme",
        )
        self.assertEqual(len({job.canonical_url for job in result.jobs}), 2)

    def test_office_uses_structured_location_before_display_name(self) -> None:
        payload = fixture("greenhouse.json")
        payload["jobs"][0]["offices"] = [
            {"name": "West Coast", "location": "San Francisco, CA, United States"}
        ]
        result = greenhouse.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-greenhouse",
            board_id="acme",
        )
        good = next(job for job in result.jobs if job.source_job_id == "101")
        self.assertIn("San Francisco, CA, United States", good.locations)
        self.assertNotIn("West Coast", good.locations)

    def test_missing_required_identity_fails_closed(self) -> None:
        payload = fixture("greenhouse.json")
        payload["jobs"][0].pop("id")
        with self.assertRaisesRegex(AdapterError, "job ID"):
            greenhouse.parse_payload(
                payload,
                company_id="acme",
                source_id="acme-greenhouse",
                board_id="acme",
            )

    def test_reported_total_mismatch_is_incomplete(self) -> None:
        payload = fixture("greenhouse.json")
        payload["meta"]["total"] = 50
        result = greenhouse.parse_payload(
            payload,
            company_id="acme",
            source_id="acme-greenhouse",
            board_id="acme",
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.fetch.completeness_reason, "reported_total_mismatch")


class LeverAdapterTests(unittest.TestCase):
    def test_paginated_capture_marks_overlap_incomplete(self) -> None:
        result = lever.parse_pages(
            [fixture("lever-page-1.json"), fixture("lever-page-2.json")],
            company_id="acme",
            source_id="acme-lever",
            board_id="acme",
            enumeration_complete=True,
            expected_pages=2,
            expected_total=4,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.fetch.completeness_reason, "pagination_overlap_detected")
        self.assertEqual(result.fetch.raw_jobs_seen, 4)
        self.assertEqual(result.fetch.unique_jobs, 3)
        self.assertEqual(result.fetch.duplicates_removed, 1)
        first = next(job for job in result.jobs if job.source_job_id == "lever-1")
        self.assertEqual(first.compensation["maximum"], 230000)
        self.assertEqual(first.workplace_arrangement, "hybrid")
        self.assertNotIn("lever-source", first.canonical_url)
        self.assertEqual(first.posted_at, "2026-08-01T12:00:00Z")

    def test_paginated_capture_is_complete_after_terminal_disjoint_page(self) -> None:
        second_page = fixture("lever-page-2.json")[1:]
        result = lever.parse_pages(
            [fixture("lever-page-1.json"), second_page],
            company_id="acme",
            source_id="acme-lever",
            board_id="acme",
            enumeration_complete=True,
            expected_pages=2,
            expected_total=3,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.fetch.unique_jobs, 3)
        self.assertEqual(result.fetch.duplicates_removed, 0)

    def test_lever_never_assumes_final_page(self) -> None:
        result = lever.parse_payload(
            fixture("lever-page-1.json"),
            company_id="acme",
            source_id="acme-lever",
            board_id="acme",
            enumeration_complete=False,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.fetch.completeness_reason, "caller_reported_incomplete")

    def test_page_or_total_mismatch_is_incomplete(self) -> None:
        result = lever.parse_pages(
            [fixture("lever-page-1.json")],
            company_id="acme",
            source_id="acme-lever",
            board_id="acme",
            enumeration_complete=True,
            expected_pages=2,
            expected_total=2,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.fetch.completeness_reason, "page_count_mismatch")


class AdapterContractTests(unittest.TestCase):
    def test_official_request_urls_are_deterministic_and_encoded(self) -> None:
        self.assertEqual(
            ashby.request_url("Example Board"),
            "https://api.ashbyhq.com/posting-api/job-board/Example%20Board?includeCompensation=true",
        )
        self.assertEqual(
            greenhouse.request_url("example-board"),
            "https://boards-api.greenhouse.io/v1/boards/example-board/jobs?content=true",
        )
        self.assertEqual(
            lever.request_url("example", skip=100, limit=100),
            "https://api.lever.co/v0/postings/example?mode=json&skip=100&limit=100",
        )

    def test_ids_and_url_normalization_are_deterministic(self) -> None:
        self.assertEqual(
            stable_job_id("acme-ashby", "abc/123"),
            stable_job_id("acme-ashby", "abc/123"),
        )
        self.assertNotEqual(
            stable_job_id("acme-ashby", "abc/123"),
            stable_job_id("other-ashby", "abc/123"),
        )
        self.assertEqual(
            normalize_url("HTTPS://Example.COM/jobs/1/?utm_source=x&a=1#top"),
            "https://example.com/jobs/1?a=1",
        )

    def test_dispatcher_and_unsupported_provider(self) -> None:
        result = parse_provider_payload(
            "ashby",
            fixture("ashby.json"),
            company_id="acme",
            source_id="acme-ashby",
            board_id="acme",
        )
        self.assertEqual(result.provider, "ashby")
        with self.assertRaisesRegex(AdapterError, "unsupported"):
            parse_provider_payload("custom", {})


if __name__ == "__main__":
    unittest.main()
