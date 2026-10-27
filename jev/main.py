import json
import sqlite3
from pathlib import Path


DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "_v5_daily-jobs" / "data" / "v5.sqlite"
)


def main() -> None:
    database_uri = f"file:{DATABASE_PATH}?mode=ro"

    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'active'
            ORDER BY RANDOM()
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        print(json.dumps({"error": "No active jobs found"}, indent=2))
        return

    job = dict(row)
    for field in ("posting_locations_json", "raw_posting_json", "job_details_json"):
        if job[field]:
            job[field] = json.loads(job[field])

    print(json.dumps(job, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
