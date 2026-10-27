CREATE TABLE import_runs (
    import_run_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (source_system = 'v4'),
    source_root TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 1),
    source_digest TEXT NOT NULL UNIQUE CHECK (length(source_digest) = 64),
    company_document_sha256 TEXT NOT NULL CHECK (length(company_document_sha256) = 64),
    research_document_sha256 TEXT NOT NULL CHECK (length(research_document_sha256) = 64),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed')),
    company_count INTEGER NOT NULL DEFAULT 0 CHECK (company_count >= 0),
    source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    location_count INTEGER NOT NULL DEFAULT 0 CHECK (location_count >= 0),
    evidence_count INTEGER NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    research_record_count INTEGER NOT NULL DEFAULT 0 CHECK (research_record_count >= 0),
    report_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(report_json))
) STRICT;

CREATE TABLE companies (
    company_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL CHECK (length(trim(company_name)) > 0),
    website_url TEXT,
    sf_validation_status TEXT NOT NULL
        CHECK (sf_validation_status IN ('pending', 'verified', 'outside_sf', 'not_found')),
    validation_checked_at TEXT,
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    source_added_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN ('v4', 'v5')),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    last_import_run_id TEXT NOT NULL REFERENCES import_runs(import_run_id)
) STRICT;

CREATE INDEX companies_status_idx
    ON companies(sf_validation_status, is_current);

CREATE TABLE company_locations (
    location_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    address_hash TEXT NOT NULL CHECK (length(address_hash) = 64),
    street TEXT,
    city TEXT,
    region TEXT,
    postal_code TEXT,
    country TEXT,
    latitude REAL,
    longitude REAL,
    validation_status TEXT NOT NULL
        CHECK (validation_status IN ('candidate', 'verified', 'stale', 'rejected')),
    is_physical INTEGER NOT NULL CHECK (is_physical IN (0, 1)),
    is_in_san_francisco INTEGER CHECK (is_in_san_francisco IN (0, 1)),
    is_primary_map_location INTEGER NOT NULL DEFAULT 0
        CHECK (is_primary_map_location IN (0, 1)),
    verified_at TEXT,
    source_system TEXT NOT NULL CHECK (source_system IN ('v4', 'v5')),
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    source_created_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    last_import_run_id TEXT NOT NULL REFERENCES import_runs(import_run_id),
    UNIQUE (company_id, address_hash),
    CHECK (latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0)),
    CHECK (longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0)),
    CHECK (
        is_primary_map_location = 0 OR (
            validation_status = 'verified'
            AND is_physical = 1
            AND is_in_san_francisco = 1
            AND latitude IS NOT NULL
            AND longitude IS NOT NULL
            AND is_current = 1
        )
    )
) STRICT;

CREATE UNIQUE INDEX one_primary_map_location_per_company
    ON company_locations(company_id)
    WHERE is_primary_map_location = 1;

CREATE INDEX company_locations_eligibility_idx
    ON company_locations(
        company_id,
        validation_status,
        is_physical,
        is_in_san_francisco,
        is_current
    );

CREATE TABLE company_location_evidence (
    evidence_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    location_id TEXT REFERENCES company_locations(location_id),
    evidence_type TEXT NOT NULL CHECK (length(trim(evidence_type)) > 0),
    url TEXT,
    label TEXT,
    finding TEXT NOT NULL CHECK (length(trim(finding)) > 0),
    observed_at TEXT,
    disposition TEXT NOT NULL CHECK (disposition IN ('supports', 'conflicts', 'context')),
    source_system TEXT NOT NULL CHECK (source_system IN ('v3_archive', 'v4', 'v5')),
    source_record_id TEXT NOT NULL,
    payload_json TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),
    last_import_run_id TEXT NOT NULL REFERENCES import_runs(import_run_id),
    UNIQUE (source_system, source_record_id)
) STRICT;

CREATE INDEX company_location_evidence_company_idx
    ON company_location_evidence(company_id, location_id);

CREATE TABLE company_research_records (
    research_record_id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies(company_id),
    referenced_company_id TEXT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('v3_archive', 'v4_company_result')),
    operation TEXT NOT NULL CHECK (length(trim(operation)) > 0),
    outcome TEXT,
    reason_code TEXT,
    occurred_at TEXT,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    last_import_run_id TEXT NOT NULL REFERENCES import_runs(import_run_id)
) STRICT;

CREATE INDEX company_research_company_idx
    ON company_research_records(company_id, source_kind);

CREATE TABLE career_sources (
    source_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    careers_url TEXT,
    listings_url TEXT,
    provider TEXT NOT NULL CHECK (length(trim(provider)) > 0),
    board_id TEXT,
    access_mode TEXT,
    scope TEXT NOT NULL CHECK (length(trim(scope)) > 0),
    instructions TEXT NOT NULL CHECK (length(trim(instructions)) > 0),
    complete_enumeration INTEGER NOT NULL CHECK (complete_enumeration IN (0, 1)),
    stable_job_identity INTEGER NOT NULL CHECK (stable_job_identity IN (0, 1)),
    structured_data INTEGER NOT NULL CHECK (structured_data IN (0, 1)),
    status TEXT NOT NULL
        CHECK (status IN ('ready', 'no_careers_found', 'blocked', 'unsupported', 'stale')),
    official_relationship_url TEXT,
    checked_at TEXT NOT NULL,
    last_success_at TEXT,
    source_record_revision INTEGER NOT NULL CHECK (source_record_revision >= 1),
    source_added_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN ('v4', 'v5')),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    last_import_run_id TEXT NOT NULL REFERENCES import_runs(import_run_id)
) STRICT;

CREATE INDEX career_sources_company_idx
    ON career_sources(company_id, status, is_current);

CREATE INDEX career_sources_provider_idx
    ON career_sources(provider, board_id);

CREATE VIEW eligible_company_locations AS
SELECT
    location_id,
    company_id,
    street,
    city,
    region,
    postal_code,
    country,
    latitude,
    longitude,
    is_primary_map_location,
    verified_at
FROM company_locations
WHERE is_current = 1
  AND validation_status = 'verified'
  AND is_physical = 1
  AND is_in_san_francisco = 1
  AND latitude IS NOT NULL
  AND longitude IS NOT NULL;

CREATE VIEW eligible_companies AS
SELECT c.*
FROM companies AS c
WHERE c.is_current = 1
  AND c.sf_validation_status = 'verified'
  AND EXISTS (
      SELECT 1
      FROM eligible_company_locations AS l
      WHERE l.company_id = c.company_id
  );

CREATE VIEW collectible_sources AS
SELECT s.*
FROM career_sources AS s
JOIN eligible_companies AS c ON c.company_id = s.company_id
WHERE s.is_current = 1
  AND s.status = 'ready';
