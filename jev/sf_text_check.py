import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "_v5_daily-jobs" / "data" / "v5.sqlite"
)
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"


def load_api_key() -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]

    for line in (Path(__file__).with_name(".env")).read_text().splitlines():
        line = line.strip()
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")

    raise RuntimeError("TYPESAFE_API_KEY is missing from the environment and .env")


def parse_json_value(value: str | None):
    return json.loads(value) if value else None


def job_state(job: sqlite3.Row) -> dict:
    return {
        "title": job["title"],
        "description": job["description_text"],
        "workplace_text": job["workplace_text"],
        "employment_text": job["employment_text"],
        "compensation_text": job["compensation_text"],
        "posting_locations": parse_json_value(job["posting_locations_json"]),
        "job_details": parse_json_value(job["job_details_json"]),
        "raw_posting": parse_json_value(job["raw_posting_json"]),
    }


def ask_jev(api_key: str, state: dict) -> tuple[str, float]:
    request_body = {
        "state": state,
        "model": "jev-latest",
        "questions": {
            "is_in_san_francisco": {
                "type": "noul",
                "instructions": (
                    "Is this job posting for a work location in San Francisco, "
                    "California? Use posting_locations as direct location evidence "
                    "when it is present, and use the rest of the captured posting "
                    "as supporting evidence. Do not treat an employer headquarters, "
                    "office address, compensation market, or general company "
                    "description as the job's location."
                ),
                "criteria": {
                    "true": (
                        "The posting lists San Francisco, California as a work "
                        "location, including when it is one of several listed work "
                        "locations."
                    ),
                    "false": (
                        "The posting lists another work location, is remote without "
                        "identifying San Francisco as the work location, or does not "
                        "provide enough location evidence."
                    ),
                },
            }
        },
    }
    request = Request(
        TYPESAFE_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            answer = result["answers"]["is_in_san_francisco"]
            return result["model"], float(answer["noul"])
        except HTTPError as error:
            if error.code not in (429, 529) or attempt == 3:
                raise
            time.sleep(2**attempt)

    raise RuntimeError("Jev request did not return an answer")


def select_jobs(job_id: str | None, limit: int) -> list[sqlite3.Row]:
    database_uri = f"file:{DATABASE_PATH}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if job_id:
            rows = connection.execute(
                """
                SELECT job_id, title, description_text, workplace_text,
                       employment_text, compensation_text,
                       posting_locations_json, job_details_json, raw_posting_json
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT job_id, title, description_text, workplace_text,
                       employment_text, compensation_text,
                       posting_locations_json, job_details_json, raw_posting_json
                FROM jobs
                WHERE status = 'active'
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use Jev to judge San Francisco location from the full job capture"
    )
    parser.add_argument("--job-id", help="Check one exact job instead of a random sample")
    parser.add_argument("--limit", type=int, default=20, help="Random active jobs to check")
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    jobs = select_jobs(args.job_id, args.limit)
    if not jobs:
        raise SystemExit("No matching jobs found")

    api_key = load_api_key()
    results = []
    for job in jobs:
        model, probability = ask_jev(api_key, job_state(job))
        results.append(
            {
                "job_id": job["job_id"],
                "title": job["title"],
                "posting_locations": parse_json_value(job["posting_locations_json"]),
                "model": model,
                "probability_in_san_francisco": probability,
                "predicted_in_san_francisco": probability >= 0.5,
            }
        )

    print(json.dumps({"count": len(results), "results": results}, indent=2))


if __name__ == "__main__":
    main()
