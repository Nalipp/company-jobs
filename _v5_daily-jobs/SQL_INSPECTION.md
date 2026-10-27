# Inspecting the V5 SQLite database

Open a terminal:

```bash
cd /Users/natelipp/ai-code/_v5_daily-jobs
sqlite3 -readonly -header -column data/v5.sqlite
```

SQLite dot commands analogous to `psql`:

```text
.tables
.schema career_sources
.schema jobs
.quit
```

For the full schema, run `.schema` with no argument. `schema_migrations` is
created by the migration runner, and all other tables are in the SQL migration
files under `src/v5_daily_jobs/migrations/`.

## Aggregate counts

```sql
SELECT 'companies' AS item, COUNT(*) AS rows FROM companies WHERE is_current = 1
UNION ALL SELECT 'locations', COUNT(*) FROM company_locations WHERE is_current = 1
UNION ALL SELECT 'location evidence', COUNT(*) FROM company_location_evidence
UNION ALL SELECT 'research records', COUNT(*) FROM company_research_records
UNION ALL SELECT 'career sources', COUNT(*) FROM career_sources WHERE is_current = 1
UNION ALL SELECT 'eligible companies', COUNT(*) FROM eligible_companies
UNION ALL SELECT 'collectible sources', COUNT(*) FROM collectible_sources
UNION ALL SELECT 'jobs', COUNT(*) FROM jobs
UNION ALL SELECT 'job scans', COUNT(*) FROM job_scans
UNION ALL SELECT 'tested source recipes', COUNT(*) FROM source_collection_recipes;

SELECT sf_validation_status, COUNT(*) AS companies
FROM companies WHERE is_current = 1
GROUP BY sf_validation_status ORDER BY companies DESC;

SELECT status, COUNT(*) AS sources,
       SUM(length(trim(instructions)) > 0) AS with_instructions,
       SUM(listings_url IS NOT NULL) AS with_listings_url
FROM career_sources WHERE is_current = 1
GROUP BY status ORDER BY sources DESC;
```

This query counts *companies*, not sources, in each instruction-coverage stage:

```sql
SELECT CASE
         WHEN EXISTS (SELECT 1 FROM collectible_sources s
                      WHERE s.company_id = c.company_id)
           THEN 'collectible instruction'
         WHEN EXISTS (SELECT 1 FROM career_sources s
                      WHERE s.company_id = c.company_id AND s.is_current = 1)
           THEN 'source exists, none collectible'
         ELSE 'no career source'
       END AS instruction_stage,
       COUNT(*) AS companies
FROM eligible_companies c
GROUP BY instruction_stage ORDER BY companies DESC;
```

This shows which ready instructions can use a V4-style structured adapter and
which need a browser or a future adapter:

```sql
SELECT CASE
         WHEN provider IN ('ashby', 'greenhouse', 'lever')
              AND structured_data = 1 AND board_id IS NOT NULL
              AND access_mode = 'structured_feed'
           THEN 'existing V4 structured adapter'
         ELSE 'browser or new adapter'
       END AS collection_path,
       COUNT(*) AS sources,
       COUNT(DISTINCT company_id) AS companies
FROM collectible_sources
GROUP BY collection_path;

SELECT provider, access_mode, COUNT(*) AS sources
FROM collectible_sources
GROUP BY provider, access_mode
ORDER BY sources DESC, provider;
```

## Example row from every table

These read-only queries show concise examples rather than dumping long research
payloads. The `jobs` query returns no row until a collection run finds a job.

```sql
SELECT version, name, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 1;
SELECT import_run_id, source_revision, status, company_count, source_count
FROM import_runs ORDER BY started_at DESC LIMIT 1;
SELECT company_id, company_name, sf_validation_status, website_url
FROM companies WHERE company_id = 'openai' LIMIT 1;
SELECT location_id, company_id, street, city, latitude, longitude,
       validation_status, is_primary_map_location
FROM company_locations WHERE company_id = 'openai' AND is_current = 1 LIMIT 1;
SELECT evidence_id, company_id, location_id, evidence_type, label, url
FROM company_location_evidence WHERE company_id = 'openai' LIMIT 1;
SELECT research_record_id, company_id, source_kind, operation,
       substr(payload_json, 1, 120) AS payload_preview
FROM company_research_records WHERE company_id = 'openai' LIMIT 1;
SELECT source_id, company_id, provider, board_id, access_mode, listings_url,
       scope, instructions, status
FROM career_sources WHERE company_id = 'openai' AND is_current = 1 LIMIT 1;
SELECT job_id, source_id, source_job_key, title, canonical_url, status
FROM jobs ORDER BY job_id LIMIT 1;
SELECT scan_id, source_id, status, observed_jobs, started_at, finished_at
FROM job_scans ORDER BY started_at DESC LIMIT 1;
SELECT source_id, source_record_revision, recipe_json, validated_at
FROM source_collection_recipes ORDER BY validated_at DESC LIMIT 1;
```

To inspect a specific collectible company's source instructions:

```sql
SELECT c.company_name, s.source_id, s.provider, s.board_id, s.access_mode,
       s.listings_url, s.scope, s.instructions
FROM collectible_sources s
JOIN companies c ON c.company_id = s.company_id
WHERE c.company_name LIKE '%OpenAI%'
ORDER BY c.company_name, s.source_id;
```
