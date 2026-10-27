from __future__ import annotations

from v5_daily_jobs.providers import fetch_structured


def test_greenhouse_capture_keeps_full_posting_and_location() -> None:
    source = {"provider": "greenhouse", "board_id": "example"}
    raw = {
        "id": 123,
        "title": "Designer",
        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/123",
        "content": "<p>Build useful things.</p>",
        "location": {"name": "San Francisco, CA"},
        "offices": [{"name": "New York, NY"}],
        "departments": [{"name": "Product"}],
    }
    urls: list[str] = []

    def fetch(url: str, _timeout: float) -> dict[str, object]:
        urls.append(url)
        return {"jobs": [raw]}

    capture = fetch_structured(source, fetcher=fetch)
    assert capture.complete
    assert len(urls) == 1
    assert urls[0].endswith("/jobs?content=true")
    assert capture.jobs[0]["source_job_id"] == "123"
    assert capture.jobs[0]["description_text"] == "Build useful things."
    assert capture.jobs[0]["locations"] == ["San Francisco, CA", "New York, NY"]
    assert capture.jobs[0]["raw_posting"] == raw


def test_lever_paginates_until_terminal_page() -> None:
    source = {
        "provider": "lever", "board_id": "example",
        "listings_url": "https://jobs.lever.co/example",
    }
    urls: list[str] = []

    def posting(job_id: str) -> dict[str, object]:
        return {
            "id": job_id,
            "text": f"Engineer {job_id}",
            "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
            "descriptionPlain": "Build the product.",
            "categories": {"location": "San Francisco, CA"},
        }

    def fetch(url: str, _timeout: float) -> list[dict[str, object]]:
        urls.append(url)
        return [posting("a"), posting("b")] if "skip=0" in url else []

    capture = fetch_structured(source, fetcher=fetch, lever_page_size=2)
    assert capture.complete
    assert capture.summary["pages"] == 2
    assert [job["source_job_id"] for job in capture.jobs] == ["a", "b"]
    assert "skip=0" in urls[0] and "skip=2" in urls[1]
    assert all(job["raw_posting"]["descriptionPlain"] == "Build the product." for job in capture.jobs)
