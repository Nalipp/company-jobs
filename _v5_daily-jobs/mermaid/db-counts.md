# V5 SQLite data counts map

Generated from `data/v5.sqlite` at `2026-09-20T16:22:52+00:00`.
Each node shows the current row count. Views are included so the map distinguishes stored data from derived eligibility data.

```mermaid
flowchart LR
    careerSources["career_sources: 905 rows"]
    companies["companies: 3,036 rows"]
    companyLocationEvidence["company_location_evidence: 1,385 rows"]
    companyLocations["company_locations: 1,385 rows"]
    companyResearchRecords["company_research_records: 2,280 rows"]
    importRuns["import_runs: 1 row"]
    jobScans["job_scans: 606 rows"]
    jobs["jobs: 16,816 rows"]
    schemaMigrations["schema_migrations: 3 rows"]
    sourceCollectionRecipes["source_collection_recipes: 50 rows"]
    collectibleSources["collectible_sources view: 581 rows"]
    eligibleCompanies["eligible_companies view: 947 rows"]
    eligibleCompanyLocations["eligible_company_locations view: 947 rows"]

    importRuns --> careerSources
    companies --> careerSources
    importRuns --> companies
    importRuns --> companyLocationEvidence
    companyLocations --> companyLocationEvidence
    companies --> companyLocationEvidence
    importRuns --> companyLocations
    companies --> companyLocations
    importRuns --> companyResearchRecords
    companies --> companyResearchRecords
    careerSources --> jobScans
    careerSources --> jobs
    jobScans --> sourceCollectionRecipes
    careerSources --> sourceCollectionRecipes
    companyLocations -.-> eligibleCompanyLocations
    companies -.-> eligibleCompanies
    eligibleCompanyLocations -.-> eligibleCompanies
    careerSources -.-> collectibleSources
    eligibleCompanies -.-> collectibleSources
```

## Counts

- `career_sources`: **905** rows
- `companies`: **3,036** rows
- `company_location_evidence`: **1,385** rows
- `company_locations`: **1,385** rows
- `company_research_records`: **2,280** rows
- `import_runs`: **1** rows
- `job_scans`: **606** rows
- `jobs`: **16,816** rows
- `schema_migrations`: **3** rows
- `source_collection_recipes`: **50** rows
- `collectible_sources` (view): **581** rows
- `eligible_companies` (view): **947** rows
- `eligible_company_locations` (view): **947** rows
