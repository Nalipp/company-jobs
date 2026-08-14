from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parent.parent


class StaticSchemaTests(unittest.TestCase):
    def validation_errors(self, instance: dict, schema_path: Path) -> list:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        return list(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(instance)
        )

    def assert_valid(self, instance_path: Path, schema_path: Path) -> None:
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        errors = self.validation_errors(instance, schema_path)
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_ai_product_frontend_profile_validates(self) -> None:
        self.assert_valid(
            ROOT / "config" / "search-profiles" / "ai-product-frontend-v1.json",
            ROOT / "schemas" / "search-profile.schema.json",
        )

    def test_empty_career_source_registry_validates(self) -> None:
        self.assert_valid(
            ROOT / "data" / "career_sources.json",
            ROOT / "schemas" / "career-sources.schema.json",
        )

    def test_ready_career_source_requires_scan_capabilities(self) -> None:
        registry = {
            "schema_version": 1,
            "updated_at": "2026-08-14T12:00:00Z",
            "sources": [
                {
                    "source_id": "example-greenhouse",
                    "company_id": "example",
                    "careers_url": "https://example.com/careers",
                    "listings_url": "https://job-boards.greenhouse.io/example",
                    "provider": "greenhouse",
                    "board_id": "example",
                    "access_mode": "structured_feed",
                    "scope": "All roles",
                    "instructions": "Enumerate the public board and open canonical job details.",
                    "capabilities": {
                        "complete_enumeration": True,
                        "stable_job_identity": True,
                        "structured_data": True,
                    },
                    "status": "ready",
                    "official_relationship_url": "https://example.com/careers",
                    "checked_at": "2026-08-14T12:00:00Z",
                    "last_success_at": "2026-08-14T12:00:00Z",
                    "added_date": "2026-08-14T12:00:00Z",
                    "updated_date": "2026-08-14T12:00:00Z",
                }
            ],
        }
        schema_path = ROOT / "schemas" / "career-sources.schema.json"
        self.assertEqual(self.validation_errors(registry, schema_path), [])

        registry["sources"][0]["capabilities"]["complete_enumeration"] = False
        self.assertNotEqual(self.validation_errors(registry, schema_path), [])


if __name__ == "__main__":
    unittest.main()
