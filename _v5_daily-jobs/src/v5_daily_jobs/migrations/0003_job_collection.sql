-- Preserve the complete normalized capture and the scan that last observed a job.
ALTER TABLE jobs ADD COLUMN job_details_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(job_details_json) AND json_type(job_details_json) = 'object');
ALTER TABLE jobs ADD COLUMN last_scan_id TEXT;

CREATE TABLE job_scans (
    scan_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES career_sources(source_id),
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    claimed_by TEXT NOT NULL CHECK (length(trim(claimed_by)) > 0),
    claim_token TEXT NOT NULL CHECK (length(claim_token) >= 24),
    started_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    finished_at TEXT,
    next_due_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'complete', 'incomplete', 'blocked', 'abandoned')
    ),
    observed_jobs INTEGER NOT NULL DEFAULT 0 CHECK (observed_jobs >= 0),
    error_class TEXT,
    notes TEXT,
    fetch_summary_json TEXT CHECK (
        fetch_summary_json IS NULL OR json_valid(fetch_summary_json)
    ),
    CHECK (
        (status = 'running' AND finished_at IS NULL AND next_due_at IS NULL)
        OR (status <> 'running' AND finished_at IS NOT NULL AND next_due_at IS NOT NULL)
    )
) STRICT;

CREATE UNIQUE INDEX one_running_scan_per_source
    ON job_scans(source_id) WHERE status = 'running';
CREATE INDEX job_scans_source_started_idx
    ON job_scans(source_id, started_at DESC, scan_id DESC);
CREATE INDEX job_scans_due_idx ON job_scans(next_due_at, status);

CREATE TABLE source_collection_recipes (
    source_id TEXT PRIMARY KEY REFERENCES career_sources(source_id),
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    recipe_json TEXT NOT NULL CHECK (
        json_valid(recipe_json) AND json_type(recipe_json) = 'object'
    ),
    validated_at TEXT NOT NULL,
    last_scan_id TEXT NOT NULL REFERENCES job_scans(scan_id)
) STRICT;

CREATE TRIGGER jobs_require_capture_insert
BEFORE INSERT ON jobs
WHEN NEW.raw_posting_json IS NULL
  OR NEW.job_details_json = '{}'
  OR NEW.last_scan_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'job capture and scan provenance are required');
END;

CREATE TRIGGER jobs_require_capture_update
BEFORE UPDATE OF raw_posting_json, job_details_json, last_scan_id ON jobs
WHEN NEW.raw_posting_json IS NULL
  OR NEW.job_details_json = '{}'
  OR NEW.last_scan_id IS NULL
BEGIN
    SELECT RAISE(ABORT, 'job capture and scan provenance are required');
END;
