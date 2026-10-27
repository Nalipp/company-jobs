import { access, readdir } from "node:fs/promises";
import path from "node:path";
import {
  candidateKeyFor,
  clone,
  identityFor,
  jobHashes,
  normalizeUrl,
  readJson,
  slugify,
  unique,
} from "./core.mjs";
import { assertAdmissionGate } from "./profile-gate.mjs";

export const loadCompanies = async (companiesDirectory) => {
  const files = (await readdir(companiesDirectory))
    .filter((name) => name.endsWith(".json"))
    .sort();
  return Promise.all(files.map(async (name) => ({
    file: path.join(companiesDirectory, name),
    value: await readJson(path.join(companiesDirectory, name)),
  })));
};

export const catalogIndex = (companyRecords) => {
  const byCompanyId = new Map();
  const byCompanyName = new Map();
  const byUrl = new Map();
  const bySourceIdentity = new Map();
  for (const record of companyRecords) {
    const company = record.value ?? record;
    byCompanyId.set(company.company_id, company);
    byCompanyName.set(company.company.name.toLowerCase(), company);
    for (const [jobId, job] of Object.entries(company.jobs)) {
      byUrl.set(normalizeUrl(job.canonical_url), { company, jobId, job });
      const source = company.sources.find((item) => item.source_id === job.source_id);
      if (source?.provider && source.board_id && job.source_job_id) {
        bySourceIdentity.set(`${source.provider}:${source.board_id.toLowerCase()}:${job.source_job_id}`, { company, jobId, job });
      }
    }
  }
  return { byCompanyId, byCompanyName, byUrl, bySourceIdentity };
};

export const findCatalogJob = (index, candidate) => {
  const identity = candidate.identity ?? identityFor(candidate);
  if (identity.provider !== "unknown" && identity.board_id && identity.source_job_id) {
    const key = `${identity.provider}:${identity.board_id.toLowerCase()}:${identity.source_job_id}`;
    if (index.bySourceIdentity.has(key)) return index.bySourceIdentity.get(key);
  }
  const url = normalizeUrl(candidate.canonical_url ?? candidate.canonicalUrl);
  return url ? index.byUrl.get(url) ?? null : null;
};

const newCompany = (admission, now) => ({
  schema_version: 1,
  company_id: admission.company.company_id,
  company: {
    name: admission.company.name,
    aliases: admission.company.aliases,
    website_url: admission.company.website_url,
    primary_domain: admission.company.primary_domain,
    description: null,
    industries: [],
    funding_stage: null,
    employee_count_range: null,
    evidence: [],
  },
  status: "active",
  sources: [],
  offices: [{
    office_id: "san-francisco",
    label: "San Francisco office",
    location_text: "San Francisco, CA",
    address: null,
    latitude: null,
    longitude: null,
    address_verified: null,
    evidence: [],
    last_checked_at: null,
  }],
  jobs: {},
  refresh: {
    last_attempted_at: null,
    last_successful_at: null,
    last_status: "not_attempted",
    consecutive_failures: 0,
    last_error: null,
  },
  created_at: now,
  updated_at: now,
});

export const planAdmission = ({ existingCompany = null, admission, profile, profileHash, now }) => {
  assertAdmissionGate(admission.job, admission.evaluation, profile);
  const company = existingCompany ? clone(existingCompany) : newCompany(admission, now);
  if (company.company_id !== admission.company.company_id) throw new Error("Admission company_id does not match existing company");
  let source = company.sources.find((item) => item.source_id === admission.source.source_id);
  if (!source) {
    source = {
      ...admission.source,
      status: "active",
      discovered_from: unique(admission.discovered_from),
      last_checked_at: now,
      last_successful_at: now,
    };
    company.sources.push(source);
  } else {
    const identityChanged = source.provider !== admission.source.provider
      || source.board_id !== admission.source.board_id
      || normalizeUrl(source.url) !== normalizeUrl(admission.source.url);
    if (identityChanged) throw new Error(`Source identity conflict for ${source.source_id}`);
    source.status = "active";
    source.discovered_from = unique([...source.discovered_from, ...admission.discovered_from]);
    source.last_checked_at = now;
    source.last_successful_at = now;
  }
  const normalizedJob = {
    provider: admission.source.provider,
    boardId: admission.source.board_id,
    sourceJobId: admission.job.source_job_id,
    canonicalUrl: admission.job.canonical_url,
    title: admission.job.title,
    locationText: admission.job.location_text,
    workplaceArrangement: admission.job.workplace_arrangement,
    employmentType: admission.job.employment_type,
    seniority: admission.job.seniority,
    compensation: admission.job.compensation,
    descriptionText: admission.job.description_text,
    descriptionCompleteness: "full",
  };
  const identity = identityFor(normalizedJob);
  const hashes = jobHashes(normalizedJob);
  const existingJob = Object.values(company.jobs).find((job) =>
    (admission.job.source_job_id && job.source_job_id === admission.job.source_job_id)
    || normalizeUrl(job.canonical_url) === normalizeUrl(admission.job.canonical_url)
  );
  const derivedJobId = slugify(admission.job.source_job_id || candidateKeyFor(normalizedJob));
  let jobId = existingJob?.job_id || derivedJobId || `job-${identity.fingerprint.slice(0, 24)}`;
  if (!existingJob && company.jobs[jobId]) jobId = `job-${identity.fingerprint.slice(0, 24)}`;
  const evaluation = {
    profile_name: profile.profile_name,
    profile_updated_at: profile.updated_at,
    profile_hash: profileHash,
    job_content_hash: hashes.content_hash,
    evaluated_at: admission.evaluation.evaluated_at,
    outcome: "included",
    match_score: admission.evaluation.match_score,
    reason_codes: admission.evaluation.reason_codes,
    evidence_snippets: admission.evaluation.evidence_snippets,
  };
  const priorEvaluations = existingJob?.evaluations ?? [];
  const evaluations = [
    ...priorEvaluations.filter((item) => !(item.profile_hash === profileHash && item.job_content_hash === hashes.content_hash)),
    evaluation,
  ];
  company.jobs[jobId] = {
    job_id: jobId,
    source_id: admission.source.source_id,
    source_job_id: admission.job.source_job_id,
    canonical_url: normalizeUrl(admission.job.canonical_url),
    activity: { status: "active", missing_from_source_count: 0, inactive_confirmed_at: null },
    raw: {
      title: admission.job.title,
      location_text: admission.job.location_text,
      workplace_arrangement: admission.job.workplace_arrangement,
      employment_type: admission.job.employment_type,
      seniority: admission.job.seniority,
      compensation: admission.job.compensation,
      posted_at: admission.job.posted_at,
      description_text: admission.job.description_text,
      description_completeness: "full",
    },
    hashes,
    discovered_from: unique([...(existingJob?.discovered_from ?? []), ...admission.discovered_from]),
    evaluations,
    first_seen_at: existingJob?.first_seen_at ?? now,
    last_seen_at: now,
    last_checked_at: now,
  };
  company.status = "active";
  company.updated_at = now;
  return { company, jobId, identity, hashes, created: !existingCompany };
};

export const companyPath = (companiesDirectory, companyId) => path.join(companiesDirectory, `${companyId}.json`);

export const companyExists = async (file) => {
  try { await access(file); return true; } catch { return false; }
};
