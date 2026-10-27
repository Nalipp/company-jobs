"""SQLite connection, migration, and integrity helpers for V5."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Iterator


MIGRATION_DIR = Path(__file__).resolve().parent / "migrations"


class DatabaseError(RuntimeError):
    """Raised when the local database cannot satisfy the V5 contract."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    checksum: str
    sql: str


def utc_now_string() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def migrations() -> tuple[Migration, ...]:
    values: list[Migration] = []
    for path in sorted(MIGRATION_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        prefix, _, stem_name = path.stem.partition("_")
        if not prefix.isdigit() or not stem_name:
            raise DatabaseError(f"Invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        values.append(
            Migration(
                version=int(prefix),
                name=stem_name,
                path=path,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    if not values:
        raise DatabaseError(f"No SQL migrations found under {MIGRATION_DIR}")
    versions = [item.version for item in values]
    if versions != list(range(1, len(values) + 1)):
        raise DatabaseError(f"Migration versions must be contiguous from 1: {versions}")
    return tuple(values)


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    resolved = path.resolve()
    if read_only:
        if not resolved.exists():
            raise DatabaseError(f"Database does not exist: {resolved}")
        connection = sqlite3.connect(
            f"file:{resolved}?mode=ro",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            resolved,
            timeout=5.0,
            isolation_level=None,
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if not read_only:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL CHECK (length(checksum) = 64),
            applied_at TEXT NOT NULL
        ) STRICT
        """
    )
    applied_rows = {
        int(row["version"]): row
        for row in connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
    }
    changed: list[int] = []
    for migration in migrations():
        applied = applied_rows.get(migration.version)
        if applied is not None:
            if applied["name"] != migration.name or applied["checksum"] != migration.checksum:
                raise DatabaseError(
                    f"Applied migration {migration.version} does not match "
                    f"{migration.path.name}"
                )
            continue
        applied_at = utc_now_string()
        script = "\n".join(
            (
                "BEGIN IMMEDIATE;",
                migration.sql,
                (
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                    f"VALUES ({migration.version}, {_sql_literal(migration.name)}, "
                    f"{_sql_literal(migration.checksum)}, {_sql_literal(applied_at)});"
                ),
                "COMMIT;",
            )
        )
        try:
            connection.executescript(script)
        except sqlite3.Error as exc:
            connection.rollback()
            raise DatabaseError(
                f"Failed to apply migration {migration.path.name}: {exc}"
            ) from exc
        changed.append(migration.version)
    return changed


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()
    return int(row["version"])


def integrity_report(connection: sqlite3.Connection) -> dict[str, object]:
    foreign_key_rows = [
        dict(row) for row in connection.execute("PRAGMA foreign_key_check")
    ]
    integrity_rows = [
        str(row[0]) for row in connection.execute("PRAGMA integrity_check")
    ]
    return {
        "foreign_key_violations": foreign_key_rows,
        "integrity_check": integrity_rows,
        "ok": not foreign_key_rows and integrity_rows == ["ok"],
    }


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise

