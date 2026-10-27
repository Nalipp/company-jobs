"""Command-line interface for the V5 local data foundation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

from .db import DatabaseError, apply_migrations, connect, integrity_report, schema_version
from .import_v4 import (
    V4ImportError,
    import_snapshot,
    load_v4_snapshot,
    preview_report,
    verify_database,
)


DEFAULT_DATABASE = Path("data/v5.sqlite")
DEFAULT_V4_ROOT = Path("../_v4_daily-jobs")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _dry_run_database_status(report: dict[str, Any], database: Path) -> None:
    report["database"] = str(database.resolve())
    if not database.exists():
        report["database_exists"] = False
        return
    report["database_exists"] = True
    try:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT status FROM import_runs WHERE source_digest = ?",
                (report["source"]["source_digest"],),
            ).fetchone()
        finally:
            connection.close()
    except (DatabaseError, sqlite3.Error):
        report["database_schema_available"] = False
        return
    report["database_schema_available"] = True
    if row is not None and row["status"] == "completed":
        report["would_change"] = False
        report["no_op_reason"] = "source_digest_already_imported"


def _render_preview(report: dict[str, Any]) -> str:
    v4 = report["v4_counts"]
    mapped = report["mapped_counts"]
    lines = [
        "V5 Phase 1 migration preview",
        f"  V4 revision: {report['source']['revision']}",
        f"  Source digest: {report['source']['source_digest']}",
        f"  Companies: {v4['companies']}",
        f"  Career sources: {v4['sources']}",
        f"  Current locations: {mapped['current_locations']}",
        f"  Eligible companies: {mapped['eligible_companies']}",
        f"  Collectible ready sources: {mapped['collectible_sources']}",
        (
            "  Research records: "
            f"{mapped['total_research_records']} "
            f"({mapped['v3_archive_research']} V3 archive + "
            f"{mapped['applied_v4_company_results']} applied V4 results)"
        ),
        "  V4 job history: excluded",
    ]
    if report.get("would_change") is False:
        lines.append("Result: source digest is already imported; an apply would be a no-op.")
    else:
        lines.append("Result: database would change on apply.")
    lines.append("Dry run complete. No database files changed.")
    return "\n".join(lines)


def _render_import(report: dict[str, Any]) -> str:
    verification = report.get("verification", {})
    counts = verification.get("database_counts", report.get("mapped_counts", {}))
    lines = [
        "V5 Phase 1 import " + ("completed" if report.get("changed") else "was a no-op"),
        f"  Import run: {report.get('import_run_id', 'existing source digest')}",
        f"  Source revision: {report['source']['revision']}",
        f"  Source digest: {report['source']['source_digest']}",
        f"  Current companies: {counts.get('current_companies', report['v4_counts']['companies'])}",
        f"  Current sources: {counts.get('current_sources', report['v4_counts']['sources'])}",
        f"  Eligible companies: {counts.get('eligible_companies', report['mapped_counts']['eligible_companies'])}",
        f"  Collectible sources: {counts.get('collectible_sources', report['mapped_counts']['collectible_sources'])}",
        f"  Research records: {counts.get('research_records', report['mapped_counts']['total_research_records'])}",
        "  V4 job history: excluded",
    ]
    if verification:
        lines.append(f"Verification: {'passed' if verification.get('ok') else 'failed'}")
    elif report.get("no_op_reason"):
        lines.append(f"Reason: {report['no_op_reason']}")
    return "\n".join(lines)


def _render_verify(report: dict[str, Any]) -> str:
    counts = report["database_counts"]
    lines = [
        "V5 database verification",
        f"  Database schema version: {report['database_schema_version']}",
        f"  Current companies: {counts['current_companies']}",
        f"  Current sources: {counts['current_sources']}",
        f"  Current locations: {counts['current_locations']}",
        f"  Eligible companies: {counts['eligible_companies']}",
        f"  Collectible sources: {counts['collectible_sources']}",
        f"  Jobs: {counts['jobs']}",
        f"  Research records: {counts['research_records']}",
        f"  SQLite integrity: {'passed' if report['integrity']['ok'] else 'failed'}",
        f"Result: {'passed' if report['ok'] else 'failed'}",
    ]
    if report["errors"]:
        lines.extend(f"  ERROR: {message}" for message in report["errors"])
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="v5-data",
        description="V5 local SQLite data foundation",
    )
    subparsers = root.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init-db", help="Apply SQLite schema migrations")
    init.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    init.add_argument("--json", action="store_true")

    import_v4 = subparsers.add_parser(
        "import-v4", help="Preview or apply the read-only V4 company/source import"
    )
    import_v4.add_argument("--v4-root", type=Path, default=DEFAULT_V4_ROOT)
    import_v4.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    import_v4.add_argument("--dry-run", action="store_true")
    import_v4.add_argument("--json", action="store_true")

    verify = subparsers.add_parser(
        "verify", help="Verify a migrated database against a coherent V4 snapshot"
    )
    verify.add_argument("--v4-root", type=Path, default=DEFAULT_V4_ROOT)
    verify.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    verify.add_argument("--json", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init-db":
            connection = connect(args.database)
            try:
                applied = apply_migrations(connection)
                value = {
                    "database": str(args.database.resolve()),
                    "schema_version": schema_version(connection),
                    "applied_migrations": applied,
                    "integrity": integrity_report(connection),
                }
            finally:
                connection.close()
            if args.json:
                _print_json(value)
            else:
                print(
                    f"Database schema version {value['schema_version']} ready at "
                    f"{value['database']}; applied migrations: {applied or 'none'}."
                )
            return 0

        snapshot = load_v4_snapshot(args.v4_root)
        if args.command == "import-v4":
            if args.dry_run:
                value = preview_report(snapshot)
                _dry_run_database_status(value, args.database)
                if args.json:
                    _print_json(value)
                else:
                    print(_render_preview(value))
                return 0
            value = import_snapshot(args.database, snapshot)
            if args.json:
                _print_json(value)
            else:
                print(_render_import(value))
            return 0

        if args.command == "verify":
            value = verify_database(args.database, snapshot)
            if args.json:
                _print_json(value)
            else:
                print(_render_verify(value))
            return 0 if value["ok"] else 1
    except (DatabaseError, V4ImportError, sqlite3.Error, OSError) as exc:
        error = {"error": type(exc).__name__, "message": str(exc)}
        if getattr(args, "json", False):
            _print_json(error)
        else:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


__all__ = ["main", "parser"]
