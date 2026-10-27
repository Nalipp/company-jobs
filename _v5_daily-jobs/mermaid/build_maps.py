#!/usr/bin/env python3
"""Build Mermaid Markdown maps from the current V5 SQLite database."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "v5.sqlite"
OUTPUT_DIR = ROOT / "mermaid"

IMPORTANT_COLUMNS: dict[str, list[str]] = {
    "schema_migrations": ["version", "name", "applied_at"],
    "import_runs": [
        "import_run_id", "source_digest", "source_revision", "status",
        "company_count", "source_count", "location_count", "evidence_count",
        "research_record_count", "completed_at",
    ],
    "companies": [
        "company_id", "company_name", "sf_validation_status",
        "source_record_revision", "last_import_run_id", "is_current",
    ],
    "company_locations": [
        "location_id", "company_id", "street", "city", "latitude", "longitude",
        "validation_status", "is_primary_map_location", "is_current",
        "last_import_run_id",
    ],
    "company_location_evidence": [
        "evidence_id", "company_id", "location_id", "evidence_type",
        "disposition", "last_import_run_id",
    ],
    "company_research_records": [
        "research_record_id", "company_id", "referenced_company_id", "source_kind",
        "outcome", "last_import_run_id",
    ],
    "career_sources": [
        "source_id", "company_id", "provider", "board_id", "instructions",
        "status", "last_import_run_id",
    ],
    "jobs": [
        "job_id", "source_id", "source_job_key", "title", "posting_locations_json",
        "workplace_text", "compensation_text", "status", "last_scan_id",
    ],
    "job_scans": [
        "scan_id", "source_id", "source_record_revision", "status", "observed_jobs",
        "finished_at",
    ],
    "source_collection_recipes": [
        "source_id", "source_record_revision", "recipe_json", "validated_at", "last_scan_id",
    ],
}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def mermaid_id(value: str) -> str:
    pieces = [piece for piece in re.split(r"[^A-Za-z0-9]+", value) if piece]
    if not pieces:
        return "table"
    return pieces[0].lower() + "".join(piece[:1].upper() + piece[1:] for piece in pieces[1:])


def mermaid_type(sql_type: str, column: str) -> str:
    lowered = (sql_type or "").lower()
    if column.endswith("_json"):
        return "json"
    if "int" in lowered:
        return "int"
    if any(token in lowered for token in ("real", "floa", "doub", "dec")):
        return "float"
    if "blob" in lowered:
        return "binary"
    if "date" in lowered or "time" in lowered:
        return "datetime"
    return "string"


def load_schema(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    records: list[dict] = []
    relationships: list[dict] = []
    for table in tables:
        identifier = quote_identifier(table)
        columns = list(connection.execute(f"PRAGMA table_info({identifier})"))
        foreign_keys = list(connection.execute(f"PRAGMA foreign_key_list({identifier})"))
        primary_keys = {row[1] for row in columns if row[5]}
        foreign_columns = {row[3] for row in foreign_keys}
        available = {row[1] for row in columns}
        selected = [column for column in IMPORTANT_COLUMNS.get(table, []) if column in available]
        for column in [row[1] for row in columns if row[5] or row[1] in foreign_columns]:
            if column not in selected:
                selected.insert(0, column)
        if not selected:
            selected = [row[1] for row in columns[:6]]
        by_name = {row[1]: row for row in columns}
        fields = []
        for column in selected:
            row = by_name[column]
            keys = []
            if column in primary_keys:
                keys.append("PK")
            if column in foreign_columns:
                keys.append("FK")
            suffix = " " + ", ".join(keys) if keys else ""
            fields.append({"name": column, "type": mermaid_type(row[2], column), "suffix": suffix})
        records.append({"name": table, "id": mermaid_id(table), "fields": fields})
        for foreign_key in foreign_keys:
            relationships.append({
                "parent": foreign_key[2],
                "child": table,
                "from": foreign_key[3],
                "to": foreign_key[4],
            })
    return records, relationships


def count_objects(connection: sqlite3.Connection, kind: str) -> dict[str, int]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' ORDER BY name",
            (kind,),
        )
    ]
    return {
        name: int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0])
        for name in names
    }


def schema_markdown(tables: list[dict], relationships: list[dict], generated: str) -> str:
    lines = [
        "# V5 SQLite table and relationship map",
        "",
        f"Generated from `data/v5.sqlite` at `{generated}`.",
        "This diagram includes physical SQLite tables only. The eligibility views are documented in the counts map.",
        "",
        "```mermaid",
        "erDiagram",
        "    direction LR",
        "",
    ]
    for table in tables:
        lines.append(f'    {table["id"]}["{table["name"]}"] {{')
        for field in table["fields"]:
            lines.append(f'        {field["type"]} {field["name"]}{field["suffix"]}')
        lines.extend(["    }", ""])
    id_by_name = {table["name"]: table["id"] for table in tables}
    seen: set[tuple[str, str]] = set()
    for relation in relationships:
        key = (relation["parent"], relation["child"])
        if key in seen or relation["parent"] not in id_by_name or relation["child"] not in id_by_name:
            continue
        seen.add(key)
        lines.append(f'    {id_by_name[relation["parent"]]} ||--o{{ {id_by_name[relation["child"]]} : references')
    lines.extend(["```", ""])
    return "\n".join(lines)


def counts_markdown(
    tables: dict[str, int], views: dict[str, int], relationships: list[dict], generated: str
) -> str:
    def label(name: str, count: int, view: bool = False) -> str:
        suffix = " view" if view else ""
        noun = "row" if count == 1 else "rows"
        return f'{name}{suffix}: {count:,} {noun}'

    lines = [
        "# V5 SQLite data counts map",
        "",
        f"Generated from `data/v5.sqlite` at `{generated}`.",
        "Each node shows the current row count. Views are included so the map distinguishes stored data from derived eligibility data.",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    all_names = list(tables) + list(views)
    ids = {name: mermaid_id(name) for name in all_names}
    for name, count in tables.items():
        lines.append(f'    {ids[name]}["{label(name, count)}"]')
    for name, count in views.items():
        lines.append(f'    {ids[name]}["{label(name, count, True)}"]')
    lines.append("")
    seen: set[tuple[str, str]] = set()
    for relation in relationships:
        key = (relation["parent"], relation["child"])
        if key in seen or relation["parent"] not in ids or relation["child"] not in ids:
            continue
        seen.add(key)
        lines.append(f'    {ids[relation["parent"]]} --> {ids[relation["child"]]}')
    view_edges = [
        ("company_locations", "eligible_company_locations"),
        ("companies", "eligible_companies"),
        ("eligible_company_locations", "eligible_companies"),
        ("career_sources", "collectible_sources"),
        ("eligible_companies", "collectible_sources"),
    ]
    for parent, child in view_edges:
        if parent in ids and child in ids:
            lines.append(f'    {ids[parent]} -.-> {ids[child]}')
    lines.extend(["```", ""])
    lines.append("## Counts")
    lines.append("")
    for name, count in tables.items():
        lines.append(f"- `{name}`: **{count:,}** rows")
    for name, count in views.items():
        lines.append(f"- `{name}` (view): **{count:,}** rows")
    lines.append("")
    return "\n".join(lines)


def build(database: Path) -> dict:
    connection = sqlite3.connect(database)
    try:
        tables, relationships = load_schema(connection)
        table_counts = count_objects(connection, "table")
        view_counts = count_objects(connection, "view")
    finally:
        connection.close()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "db-schema.md").write_text(
        schema_markdown(tables, relationships, generated), encoding="utf-8"
    )
    (OUTPUT_DIR / "db-counts.md").write_text(
        counts_markdown(table_counts, view_counts, relationships, generated), encoding="utf-8"
    )
    (OUTPUT_DIR / "db-counts.json").write_text(
        json.dumps({"generated": generated, "tables": table_counts, "views": view_counts}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "database": str(database),
        "schema": str(OUTPUT_DIR / "db-schema.md"),
        "counts": str(OUTPUT_DIR / "db-counts.md"),
        "tables": table_counts,
        "views": view_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(json.dumps(build(args.database), indent=2))


if __name__ == "__main__":
    main()
