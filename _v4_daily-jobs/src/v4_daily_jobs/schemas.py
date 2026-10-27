"""JSON-schema loading and validation helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"


class SchemaError(ValueError):
    """Raised when a persisted document violates its contract."""


@lru_cache(maxsize=1)
def _schemas() -> tuple[dict[str, dict[str, Any]], Registry]:
    documents: dict[str, dict[str, Any]] = {}
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        with path.open(encoding="utf-8") as handle:
            schema = json.load(handle)
        documents[path.name] = schema
        if "$id" in schema:
            resources.append((schema["$id"], Resource.from_contents(schema)))
    return documents, Registry().with_resources(resources)


def validate(instance: Any, schema_name: str, label: str | None = None) -> None:
    documents, registry = _schemas()
    try:
        schema = documents[schema_name]
    except KeyError as exc:
        raise SchemaError(f"Unknown schema {schema_name!r}") from exc
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    messages: list[str] = []
    for error in errors[:20]:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        messages.append(f"{location}: {error.message}")
    if len(errors) > 20:
        messages.append(f"... and {len(errors) - 20} more errors")
    subject = label or schema_name
    raise SchemaError(f"{subject} failed schema validation:\n- " + "\n- ".join(messages))


def validator(schema_name: str, label: str | None = None):
    """Return a callback suitable for JsonStore/QueueEngine validation."""

    def _validate(value: Any) -> None:
        validate(value, schema_name, label)

    return _validate

