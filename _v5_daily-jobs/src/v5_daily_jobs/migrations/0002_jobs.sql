-- Job inventory only. Collection and job-specific workplace verification arrive later.
-- The source row owns its reusable discovery instructions and company relationship.
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES career_sources(source_id),
    source_job_key TEXT NOT NULL CHECK (length(trim(source_job_key)) > 0),
    source_job_id TEXT,
    canonical_url TEXT NOT NULL CHECK (length(trim(canonical_url)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description_text TEXT,
    posting_locations_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(posting_locations_json)
               AND json_type(posting_locations_json) = 'array'),
    workplace_text TEXT,
    employment_text TEXT,
    compensation_text TEXT,
    published_at TEXT,
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    inactive_at TEXT,
    raw_posting_json TEXT CHECK (
        raw_posting_json IS NULL OR json_valid(raw_posting_json)
    ),
    UNIQUE (source_id, source_job_key),
    CHECK (
        (status = 'active' AND inactive_at IS NULL)
        OR (status = 'inactive' AND inactive_at IS NOT NULL)
    )
) STRICT;

CREATE INDEX jobs_source_status_idx ON jobs(source_id, status);
CREATE INDEX jobs_last_seen_idx ON jobs(last_seen_at);

-- Collection must use the shared SF eligibility view, including when a caller
-- bypasses the future collector and writes SQL directly.
CREATE TRIGGER jobs_collectible_source_insert
BEFORE INSERT ON jobs
WHEN NOT EXISTS (
    SELECT 1 FROM collectible_sources WHERE source_id = NEW.source_id
)
BEGIN
    SELECT RAISE(ABORT, 'job source is not currently collectible');
END;

CREATE TRIGGER jobs_collectible_source_change
BEFORE UPDATE OF source_id ON jobs
WHEN NOT EXISTS (
    SELECT 1 FROM collectible_sources WHERE source_id = NEW.source_id
)
BEGIN
    SELECT RAISE(ABORT, 'job source is not currently collectible');
END;
