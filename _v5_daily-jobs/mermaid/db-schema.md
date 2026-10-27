# V5 SQLite table and relationship map

Generated from `data/v5.sqlite` at `2026-09-20T16:22:52+00:00`.
This diagram includes physical SQLite tables only. The eligibility views are documented in the counts map.

```mermaid
erDiagram
    direction LR

    careerSources["career_sources"] {
        string source_id PK
        string company_id FK
        string provider
        string board_id
        string instructions
        string status
        string last_import_run_id FK
    }

    companies["companies"] {
        string company_id PK
        string company_name
        string sf_validation_status
        int source_record_revision
        string last_import_run_id FK
        int is_current
    }

    companyLocationEvidence["company_location_evidence"] {
        string evidence_id PK
        string company_id FK
        string location_id FK
        string evidence_type
        string disposition
        string last_import_run_id FK
    }

    companyLocations["company_locations"] {
        string location_id PK
        string company_id FK
        string street
        string city
        float latitude
        float longitude
        string validation_status
        int is_primary_map_location
        int is_current
        string last_import_run_id FK
    }

    companyResearchRecords["company_research_records"] {
        string research_record_id PK
        string company_id FK
        string referenced_company_id
        string source_kind
        string outcome
        string last_import_run_id FK
    }

    importRuns["import_runs"] {
        string import_run_id PK
        string source_digest
        int source_revision
        string status
        int company_count
        int source_count
        int location_count
        int evidence_count
        int research_record_count
        string completed_at
    }

    jobScans["job_scans"] {
        string scan_id PK
        string source_id FK
        int source_record_revision
        string status
        int observed_jobs
        string finished_at
    }

    jobs["jobs"] {
        string job_id PK
        string source_id FK
        string source_job_key
        string title
        json posting_locations_json
        string workplace_text
        string compensation_text
        string status
        string last_scan_id
    }

    schemaMigrations["schema_migrations"] {
        int version PK
        string name
        string applied_at
    }

    sourceCollectionRecipes["source_collection_recipes"] {
        string source_id PK, FK
        int source_record_revision
        json recipe_json
        string validated_at
        string last_scan_id FK
    }

    importRuns ||--o{ careerSources : references
    companies ||--o{ careerSources : references
    importRuns ||--o{ companies : references
    importRuns ||--o{ companyLocationEvidence : references
    companyLocations ||--o{ companyLocationEvidence : references
    companies ||--o{ companyLocationEvidence : references
    importRuns ||--o{ companyLocations : references
    companies ||--o{ companyLocations : references
    importRuns ||--o{ companyResearchRecords : references
    companies ||--o{ companyResearchRecords : references
    careerSources ||--o{ jobScans : references
    careerSources ||--o{ jobs : references
    jobScans ||--o{ sourceCollectionRecipes : references
    careerSources ||--o{ sourceCollectionRecipes : references
```
