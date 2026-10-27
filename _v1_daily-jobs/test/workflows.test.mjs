import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { emptyMemory, upsertMemory } from "../scripts/lib/candidate-memory.mjs";
import { identityFor, jobHashes, sha256 } from "../scripts/lib/core.mjs";
import { normalizeAshbyJob } from "../scripts/lib/providers.mjs";
import { runExpansion, runRefresh } from "../scripts/lib/workflows.mjs";

const profileText = await readFile(new URL("../profiles/default.json", import.meta.url), "utf8");
const profile = JSON.parse(profileText);
const profileHash = sha256(profileText);
const rawAshby = JSON.parse(await readFile(new URL("./fixtures/ashby-response.json", import.meta.url), "utf8"));
const now = "2026-08-05T20:00:00Z";

const source = {
  source_id: "primary-careers",
  source_type: "ats",
  provider: "ashby",
  url: "https://jobs.ashbyhq.com/fixture",
  board_id: "fixture",
  status: "active",
  discovered_from: ["test"],
  last_checked_at: null,
  last_successful_at: null,
};

const rememberedJob = (id, url, missing = 0) => ({
  job_id: id,
  source_id: "primary-careers",
  source_job_id: id,
  canonical_url: url,
  activity: { status: "active", missing_from_source_count: missing, inactive_confirmed_at: null },
  raw: {
    title: "Frontend Engineer",
    location_text: "San Francisco, CA",
    workplace_arrangement: "hybrid",
    employment_type: "full-time",
    seniority: "senior",
    compensation: { minimum: 180000, maximum: 225000, currency: "USD", basis: "base", period: "year", source_text: "$180K-$225K" },
    posted_at: null,
    description_text: "Legacy evidence",
    description_completeness: "evidence_only",
  },
  hashes: { metadata_hash: "legacy-metadata", content_hash: "legacy-content", hash_algorithm: "sha256" },
  discovered_from: ["legacy"],
  evaluations: [{
    profile_name: "default", profile_updated_at: profile.updated_at, profile_hash: "legacy-profile",
    job_content_hash: "legacy-content", evaluated_at: "2026-08-03T10:00:00Z", outcome: "included",
    match_score: 90, reason_codes: ["legacy"], evidence_snippets: ["legacy"],
  }],
  first_seen_at: "2026-08-03T10:00:00Z",
  last_seen_at: "2026-08-03T10:00:00Z",
  last_checked_at: "2026-08-03T10:00:00Z",
});

const company = () => ({
  schema_version: 1,
  company_id: "fixture",
  company: { name: "Fixture", aliases: [], website_url: null, primary_domain: null, description: null, industries: [], funding_stage: null, employee_count_range: null, evidence: [] },
  status: "active",
  sources: [structuredClone(source)],
  offices: [{ office_id: "san-francisco", label: null, location_text: "San Francisco, CA", address: null, latitude: null, longitude: null, address_verified: null, evidence: [], last_checked_at: null }],
  jobs: {
    "existing-1": rememberedJob("existing-1", "https://jobs.ashbyhq.com/fixture/existing-1"),
    "missing-1": rememberedJob("missing-1", "https://jobs.ashbyhq.com/fixture/missing-1"),
  },
  refresh: { last_attempted_at: null, last_successful_at: null, last_status: "not_attempted", consecutive_failures: 0, last_error: null },
  created_at: "2026-08-03T10:00:00Z",
  updated_at: "2026-08-03T10:00:00Z",
});

const collectedJobs = rawAshby.jobs
  .filter((job) => job.isListed !== false)
  .map((job) => normalizeAshbyJob(job, source));

test("refresh queues new and evidence-only jobs and uses safe missing counts", async () => {
  const result = await runRefresh({
    companies: [company()],
    profile,
    profileHash,
    memory: emptyMemory(now),
    now,
    collect: async () => ({ requestUrl: "https://api.ashbyhq.com/fixture", jobs: collectedJobs, complete: true }),
  });
  assert.deepEqual(result.checkpoint.candidates.map((candidate) => candidate.change_state).sort(), ["description_backfill", "new"]);
  assert.equal(result.checkpoint.sources[0].rejected_count, 1);
  assert.equal(result.companies[0].jobs["missing-1"].activity.missing_from_source_count, 1);
  assert.equal(result.companies[0].jobs["missing-1"].activity.status, "active");
  assert.equal(Object.values(result.memory.entries).some((entry) => entry.title === "Account Executive" && entry.outcome === "excluded"), true);
});

test("failed refresh never increments missing counts", async () => {
  const original = company();
  const result = await runRefresh({
    companies: [original],
    profile,
    profileHash,
    memory: emptyMemory(now),
    now,
    collect: async () => { throw new Error("network unavailable"); },
  });
  assert.equal(result.companies[0].jobs["missing-1"].activity.missing_from_source_count, 0);
  assert.equal(result.companies[0].refresh.last_status, "failed");
  assert.equal(result.checkpoint.status, "partial");
});

const expansionInput = (leads) => ({
  schema_version: 1,
  collection_id: "test-expansion",
  profile_name: "default",
  strategy: "incremental",
  started_at: now,
  completed_at: now,
  date_range: { start: "2026-08-04", end: "2026-08-05", timezone: profile.timezone },
  query_matrix: ["test query"],
  sources: [{ name: "test source", kind: "web_search", query: "test query", url: "https://example.com/search", status: "completed", notes: null }],
  leads,
});

const lead = (overrides = {}) => ({
  title: "Frontend Engineer",
  company: "New Company",
  discovery_url: "https://jobs.example.com/frontend-1",
  canonical_url: "https://jobs.example.com/frontend-1",
  provider: "custom",
  board_id: null,
  source_job_id: "frontend-1",
  source_names: ["test source"],
  location_text: "San Francisco, CA",
  workplace_arrangement: "hybrid",
  employment_type: "full-time",
  seniority: "senior",
  compensation: { minimum: 180000, maximum: 220000, currency: "USD", basis: "base", period: "year", source_text: "$180K-$220K base" },
  posted_date: "2026-08-05",
  description_text: "Build AI-powered frontend experiences.",
  description_completeness: "full",
  preliminary_fit: [],
  preliminary_concerns: [],
  ...overrides,
});

test("expansion skips unchanged rejection memory and queues only new plausible work", () => {
  const rejectedLead = lead({ canonical_url: "https://jobs.example.com/rejected", discovery_url: "https://jobs.example.com/rejected", source_job_id: "rejected" });
  const rejectedJob = {
    provider: rejectedLead.provider, boardId: rejectedLead.board_id, sourceJobId: rejectedLead.source_job_id,
    canonicalUrl: rejectedLead.canonical_url, company: rejectedLead.company, title: rejectedLead.title,
    locationText: rejectedLead.location_text, workplaceArrangement: rejectedLead.workplace_arrangement,
    employmentType: rejectedLead.employment_type, seniority: rejectedLead.seniority,
    compensation: rejectedLead.compensation, descriptionText: rejectedLead.description_text,
    descriptionCompleteness: rejectedLead.description_completeness,
  };
  let memory = emptyMemory(now);
  memory = upsertMemory(memory, {
    identity: identityFor(rejectedJob), hashes: jobHashes(rejectedJob), canonical_url: rejectedLead.canonical_url,
    company: rejectedLead.company, title: rejectedLead.title, location_text: rejectedLead.location_text, source_names: ["test source"],
  }, { now, profileName: "default", profileHash, outcome: "excluded", reasonCodes: ["prior_rejection"] });
  const result = runExpansion({
    input: expansionInput([rejectedLead, lead()]),
    companies: [], profile, profileHash, memory, now,
  });
  assert.equal(result.checkpoint.candidates.length, 1);
  assert.equal(result.checkpoint.candidates[0].canonical_url, "https://jobs.example.com/frontend-1");
  assert.equal(result.checkpoint.sources[0].memory_skipped_count, 1);
});

