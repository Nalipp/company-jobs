#!/usr/bin/env python3
"""Regression tests for replay-safe and order-independent handoff application."""

from __future__ import annotations

import copy
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
IMPORTER = ROOT / "scripts" / "apply_handoff.py"
EXAMPLES = [
    ROOT / "examples" / "company-discovery.example.json",
    ROOT / "examples" / "company-address-located.example.json",
    ROOT / "examples" / "company-job-fetch.example.json",
]


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")


def base_catalog() -> dict:
    company = copy.deepcopy(load(EXAMPLES[0])["changes"][0])
    company.update(
        {
            "company_id": "example-ai",
            "name": "Example AI",
            "website_url": "https://example.com/",
            "primary_domain": "example.com",
            "jobs_url": "https://jobs.ashbyhq.com/example-ai",
            "discovered_at": "2026-08-05T19:15:00Z",
            "last_verified_at": "2026-08-05T19:15:00Z",
        }
    )
    company["ats"] = {"provider": "ashby", "board_id": "example-ai"}
    company["jobs"][0]["job_id"] = "example-job-123"
    company["jobs"][0]["url"] = (
        "https://jobs.ashbyhq.com/example-ai/example-job-123"
    )
    company["jobs"][0]["verified_at"] = "2026-08-05T19:15:00Z"
    return {
        "schema_version": 1,
        "updated_at": "2026-08-05T19:15:00Z",
        "companies": [company],
    }


class ApplyHandoffTests(unittest.TestCase):
    def run_importer(self, handoff: Path, catalog: Path) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(IMPORTER), str(handoff), "--catalog", str(catalog)],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        summary = json.loads(result.stdout) if result.stdout else {}
        return result.returncode, summary, result.stderr

    def test_all_orders_converge_and_replays_are_noops(self) -> None:
        final_catalogs = []
        for order in itertools.permutations(EXAMPLES):
            with tempfile.TemporaryDirectory() as temporary:
                catalog_path = Path(temporary) / "companies.json"
                write(catalog_path, base_catalog())

                for handoff in order:
                    code, summary, stderr = self.run_importer(handoff, catalog_path)
                    self.assertEqual(code, 0, stderr)
                    self.assertTrue(summary["would_change"])

                final_catalog = load(catalog_path)
                final_catalogs.append(final_catalog)
                self.assertEqual(len(final_catalog["companies"]), 2)
                example = next(
                    item
                    for item in final_catalog["companies"]
                    if item["company_id"] == "example-ai"
                )
                self.assertEqual(example["map_location"]["status"], "located")
                self.assertEqual(len(example["jobs"]), 2)

                for handoff in EXAMPLES:
                    code, summary, stderr = self.run_importer(handoff, catalog_path)
                    self.assertEqual(code, 0, stderr)
                    self.assertFalse(summary["would_change"])
                    self.assertEqual(summary["applied"], 0)

                self.assertEqual(load(catalog_path), final_catalog)

        for catalog in final_catalogs[1:]:
            self.assertEqual(catalog, final_catalogs[0])

    def test_stale_address_transition_conflicts_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog_path = Path(temporary) / "companies.json"
            catalog = base_catalog()
            catalog["companies"][0]["map_location"] = copy.deepcopy(
                load(EXAMPLES[1])["changes"][0]["map_location"]
            )
            write(catalog_path, catalog)
            before = catalog_path.read_bytes()

            stale = load(EXAMPLES[1])
            stale["changes"][0]["map_location"] = {
                "city": "San Francisco",
                "region": "CA",
                "country": "US",
                "address": None,
                "postal_code": None,
                "latitude": None,
                "longitude": None,
                "status": "not_found",
                "location_type": None,
                "source_label": None,
                "source_url": None,
                "checked_at": "2026-08-06T20:00:00Z",
            }
            handoff_path = Path(temporary) / "stale.json"
            write(handoff_path, stale)

            code, summary, stderr = self.run_importer(handoff_path, catalog_path)
            self.assertEqual(code, 1, stderr)
            self.assertFalse(summary["would_change"])
            self.assertEqual(len(summary["conflicts"]), 1)
            self.assertEqual(catalog_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
