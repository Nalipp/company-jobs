import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "_v5_daily-jobs" / "data" / "v5.sqlite"
)
JSON_COLUMNS = {
    "fetch_summary_json",
    "job_details_json",
    "payload_json",
    "posting_locations_json",
    "raw_posting_json",
    "recipe_json",
}


def as_json_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    result = dict(row)
    for column in JSON_COLUMNS:
        if result.get(column):
            result[column] = json.loads(result[column])
    return result


def fetch_one(connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()):
    return as_json_row(connection.execute(query, parameters).fetchone())


def fetch_all(connection: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()):
    return [as_json_row(row) for row in connection.execute(query, parameters)]


def fetch_by_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    values: list[str],
    order_by: str,
) -> list[dict[str, Any]]:
    if not values:
        return []

    placeholders = ", ".join("?" for _ in values)
    return fetch_all(
        connection,
        f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY {order_by}",
        tuple(values),
    )


def build_payload() -> dict[str, Any]:
    database_uri = f"file:{DATABASE_PATH}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row

        selected_job = fetch_one(
            connection,
            """
            SELECT *
            FROM jobs
            WHERE status = 'active'
            ORDER BY RANDOM()
            LIMIT 1
            """,
        )
        if selected_job is None:
            raise LookupError("No active jobs found")

        selected_source = fetch_one(
            connection,
            "SELECT * FROM career_sources WHERE source_id = ?",
            (selected_job["source_id"],),
        )
        if selected_source is None:
            raise LookupError("The selected job has no career source")

        company_id = selected_source["company_id"]
        company = fetch_one(
            connection,
            "SELECT * FROM companies WHERE company_id = ?",
            (company_id,),
        )
        if company is None:
            raise LookupError("The selected job's source has no company")

        locations = fetch_all(
            connection,
            "SELECT * FROM company_locations WHERE company_id = ? ORDER BY location_id",
            (company_id,),
        )
        location_evidence = fetch_all(
            connection,
            """
            SELECT *
            FROM company_location_evidence
            WHERE company_id = ?
            ORDER BY evidence_id
            """,
            (company_id,),
        )
        research_records = fetch_all(
            connection,
            """
            SELECT *
            FROM company_research_records
            WHERE company_id = ? OR referenced_company_id = ?
            ORDER BY research_record_id
            """,
            (company_id, company_id),
        )
        career_sources = fetch_all(
            connection,
            "SELECT * FROM career_sources WHERE company_id = ? ORDER BY source_id",
            (company_id,),
        )
        source_ids = [source["source_id"] for source in career_sources]
        source_collection_recipes = fetch_by_ids(
            connection,
            "source_collection_recipes",
            "source_id",
            source_ids,
            "source_id",
        )
        job_scans = fetch_by_ids(
            connection,
            "job_scans",
            "source_id",
            source_ids,
            "started_at",
        )
        company_jobs = fetch_by_ids(
            connection,
            "jobs",
            "source_id",
            source_ids,
            "job_id",
        )

        import_run_ids = {
            record["last_import_run_id"]
            for record in [
                company,
                *locations,
                *location_evidence,
                *research_records,
                *career_sources,
            ]
            if record.get("last_import_run_id")
        }
        import_runs = fetch_by_ids(
            connection,
            "import_runs",
            "import_run_id",
            sorted(import_run_ids),
            "started_at",
        )

        eligible_company = fetch_one(
            connection,
            "SELECT * FROM eligible_companies WHERE company_id = ?",
            (company_id,),
        )
        collectible_sources = fetch_all(
            connection,
            "SELECT * FROM collectible_sources WHERE company_id = ? ORDER BY source_id",
            (company_id,),
        )

    return {
        "selected_job": selected_job,
        "selected_job_source": selected_source,
        "company": {
            "record": company,
            "eligible_record": eligible_company,
            "collectible_sources": collectible_sources,
            "locations": locations,
            "location_evidence": location_evidence,
            "research_records": research_records,
            "career_sources": career_sources,
            "source_collection_recipes": source_collection_recipes,
            "job_scans": job_scans,
            "jobs": company_jobs,
            "import_runs": import_runs,
        },
    }


class JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path not in {"/", "/json"}:
            self.send_error(404, "Use / or /json")
            return

        try:
            body = json.dumps(build_payload(), indent=2, ensure_ascii=False).encode("utf-8")
        except (LookupError, sqlite3.Error, json.JSONDecodeError) as error:
            body = json.dumps({"error": str(error)}, indent=2).encode("utf-8")
            self.send_response(500)
        else:
            self.send_response(200)

        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show a random V5 job and its company data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), JsonHandler)
    print(f"Open http://{args.host}:{args.port}/ in your browser")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
