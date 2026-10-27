import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const migrationTimestamp = "2026-08-05T10:36:12-07:00";
const snapshotPath = "legacy-old/2026-08-05-pre-catalog";
const stagingRoot = path.join(root, "catalog/migration-staging");
const companiesRoot = path.join(stagingRoot, "companies");

const json = async (relativePath) =>
  JSON.parse(await readFile(path.join(root, relativePath), "utf8"));
const sha256 = (value) =>
  createHash("sha256").update(value).digest("hex");
const unique = (values) => [...new Set(values.filter(Boolean))];
const canonicalKey = (url) => url.replace(/\/+$/, "");
const slug = (value) =>
  value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

const profileText = await readFile(path.join(root, "profiles/default.json"), "utf8");
const profile = JSON.parse(profileText);
const profileHash = sha256(profileText);
const employers = (await json(`${snapshotPath}/sources/employers.json`)).employers;
const employerByName = new Map(employers.map((employer) => [employer.company, employer]));

const runFiles = (await readdir(path.join(root, "runs")))
  .filter((name) => /^\d{4}-\d{2}-\d{2}(?:-\d+)?\.json$/.test(name))
  .sort();

const occurrences = [];
for (const file of runFiles) {
  const run = await json(path.join("runs", file));
  for (const job of run.jobs) {
    occurrences.push({
      file: `runs/${file}`,
      runId: run.run_id,
      runStartedAt: run.started_at,
      runCompletedAt: run.completed_at,
      job,
    });
  }
}

const groups = new Map();
for (const occurrence of occurrences) {
  const key = canonicalKey(occurrence.job.canonical_url);
  if (!groups.has(key)) groups.set(key, []);
  groups.get(key).push(occurrence);
}

const titlePasses = (title) => {
  const normalized = title.toLowerCase();
  if (/devops|site reliability|\bsre\b|platform engineer|infrastructure engineer/.test(normalized)) {
    return false;
  }
  return /front[ -]?end|product engineer|software engineer.*\b(?:product|ai|applied ai)\b|forward deployed engineer|full[ -]?stack|growth engineer|growth engineering|applications engineer|applied ai engineer|ai product engineer/.test(normalized);
};

const eligibility = (occurrenceGroup) => {
  const latest = [...occurrenceGroup].sort((a, b) =>
    a.runCompletedAt.localeCompare(b.runCompletedAt)
  ).at(-1);
  const allText = occurrenceGroup
    .flatMap(({ job }) => [
      job.title,
      ...(job.why_it_fits ?? []),
      ...(job.evidence ?? []).flatMap((item) => [item.claim, item.source_text]),
    ])
    .filter(Boolean)
    .join("\n");
  const reasons = [];
  const job = latest.job;

  if (!job.canonical_url) reasons.push("missing_canonical_url");
  if (!/san francisco/i.test(job.location ?? "")) reasons.push("posting_does_not_identify_san_francisco");
  if (!["hybrid", "on-site"].includes(job.workplace_arrangement)) reasons.push("incompatible_workplace_arrangement");
  if (!/full[- ]?time/i.test(job.employment_type ?? "")) reasons.push("not_confirmed_full_time");
  if (!titlePasses(job.title)) reasons.push("incompatible_title_screen");
  if (!/\b(ai|llm|agent|copilot|machine learning|ml)\b/i.test(allText)) reasons.push("missing_role_specific_ai_evidence");
  const compensation = job.compensation;
  if (!compensation) {
    reasons.push("missing_published_compensation");
  } else {
    if (compensation.currency !== "USD") reasons.push("compensation_not_usd");
    if (compensation.basis !== "base") reasons.push("compensation_not_base");
    if (compensation.period !== "year") reasons.push("compensation_not_annual");
    if (!(compensation.maximum >= 200000)) reasons.push("published_base_maximum_below_200000");
  }
  if (!employerByName.has(job.company)) reasons.push("missing_employer_source");

  return { eligible: reasons.length === 0, reasons, latest };
};

const qualifying = [];
const withheld = [];
for (const [canonicalUrl, occurrenceGroup] of groups) {
  const decision = eligibility(occurrenceGroup);
  const record = {
    canonical_url: canonicalUrl,
    company: decision.latest.job.company,
    title: decision.latest.job.title,
    occurrence_count: occurrenceGroup.length,
    source_runs: unique(occurrenceGroup.map((item) => item.file)),
    reasons: decision.reasons,
  };
  if (decision.eligible) qualifying.push({ canonicalUrl, occurrenceGroup, decision, record });
  else withheld.push(record);
}

const byCompany = new Map();
for (const item of qualifying) {
  const company = item.decision.latest.job.company;
  if (!byCompany.has(company)) byCompany.set(company, []);
  byCompany.get(company).push(item);
}

const evidenceSourceType = (sourceType) => {
  if (sourceType === "ats") return "ats";
  if (sourceType === "job_posting") return "job_posting";
  if (sourceType === "employer_location_page") return "company_website";
  return "other";
};

const isCompanyDomain = (url) => {
  const host = new URL(url).hostname.replace(/^www\./, "");
  return ![
    "jobs.ashbyhq.com",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "ycombinator.com",
    "www.ycombinator.com",
    "ats.rippling.com",
  ].includes(host);
};

const sourceFor = (employer) => ({
  source_id: "primary-careers",
  source_type: ["ashby", "greenhouse", "lever", "workday"].includes(employer.ats_provider)
    ? "ats"
    : "custom_careers",
  provider: employer.ats_provider,
  url: employer.careers_url,
  board_id: employer.board_id,
  status: employer.status,
  discovered_from: unique(employer.discovered_from),
  last_checked_at: employer.last_checked_at,
  last_successful_at: employer.last_checked_at,
});

const buildOffice = (items) => {
  const sites = items.flatMap(({ occurrenceGroup }) =>
    occurrenceGroup.map(({ job, runCompletedAt }) => ({ site: job.job_site, runCompletedAt }))
  );
  const ranked = [...sites].sort((a, b) => {
    const score = (entry) => entry.site?.validation_status === "verified" ? 2 : entry.site?.raw_address ? 1 : 0;
    return score(a) - score(b) || a.runCompletedAt.localeCompare(b.runCompletedAt);
  });
  const selected = ranked.at(-1)?.site ?? null;
  const verified = selected?.validation_status === "verified"
    && typeof selected.latitude === "number"
    && typeof selected.longitude === "number"
    && Boolean(selected.formatted_address);
  const rawAddress = selected?.formatted_address ?? selected?.raw_address ?? null;
  const address = rawAddress && /\d/.test(rawAddress) ? rawAddress : null;
  const checkedAt = selected?.validated_at ?? null;
  const officeEvidence = verified ? [{
    source_type: evidenceSourceType(selected.source_type),
    source_name: selected.source_type ?? "legacy verification run",
    url: selected.source_url,
    text: selected.source_text,
    checked_at: checkedAt,
  }] : [];

  return {
    office_id: "san-francisco",
    label: "San Francisco office",
    location_text: "San Francisco, CA",
    address,
    latitude: verified ? selected.latitude : null,
    longitude: verified ? selected.longitude : null,
    address_verified: verified ? true : null,
    evidence: officeEvidence,
    last_checked_at: checkedAt,
  };
};

const buildDescription = (occurrenceGroup) => {
  const lines = [];
  for (const { job } of occurrenceGroup) {
    for (const text of job.why_it_fits ?? []) lines.push(`Fit: ${text}`);
    for (const text of job.concerns ?? []) lines.push(`Concern: ${text}`);
    for (const item of job.evidence ?? []) {
      if (item.source_text) lines.push(`Evidence (${item.claim}): ${item.source_text}`);
    }
  }
  return unique(lines).join("\n");
};

const firstDate = (occurrenceGroup) =>
  [...occurrenceGroup].map((item) => item.runStartedAt).sort().at(0);
const lastDate = (occurrenceGroup) =>
  [...occurrenceGroup].map((item) => item.runCompletedAt).sort().at(-1);

const postedAt = (occurrenceGroup) => {
  for (const { job } of [...occurrenceGroup].reverse()) {
    const dateEvidence = (job.evidence ?? []).find((item) => item.claim === "posted_date" || item.claim === "Active and date");
    const value = typeof dateEvidence?.value === "string"
      ? dateEvidence.value
      : dateEvidence?.value?.published_date;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value ?? "")) return `${value}T00:00:00Z`;
  }
  return null;
};

const jobRecord = (item) => {
  const { occurrenceGroup, decision } = item;
  const latestJob = decision.latest.job;
  const descriptionText = buildDescription(occurrenceGroup);
  const metadataForHash = {
    title: latestJob.title,
    location: latestJob.location,
    workplace: latestJob.workplace_arrangement,
    employment: latestJob.employment_type,
    seniority: latestJob.seniority,
    compensation: latestJob.compensation,
    canonical_url: latestJob.canonical_url,
  };
  const metadataHash = sha256(JSON.stringify(metadataForHash));
  const contentHash = sha256(descriptionText);
  const jobId = slug(latestJob.job_key);
  const sourceJobId = (() => {
    const part = new URL(latestJob.canonical_url).pathname.split("/").filter(Boolean).at(-1);
    return part && part !== "careers" ? part : latestJob.job_key;
  })();
  const evidenceSnippets = unique([
    ...(latestJob.why_it_fits ?? []),
    ...(latestJob.evidence ?? []).map((item) => item.source_text),
  ]);

  return [jobId, {
    job_id: jobId,
    source_id: "primary-careers",
    source_job_id: sourceJobId,
    canonical_url: latestJob.canonical_url,
    activity: {
      status: "active",
      missing_from_source_count: 0,
      inactive_confirmed_at: null,
    },
    raw: {
      title: latestJob.title,
      location_text: latestJob.location,
      workplace_arrangement: latestJob.workplace_arrangement,
      employment_type: latestJob.employment_type,
      seniority: latestJob.seniority,
      compensation: latestJob.compensation,
      posted_at: postedAt(occurrenceGroup),
      description_text: descriptionText,
      description_completeness: "evidence_only",
    },
    hashes: {
      metadata_hash: metadataHash,
      content_hash: contentHash,
      hash_algorithm: "sha256",
    },
    discovered_from: unique(occurrenceGroup.map((entry) => `legacy:${entry.file}`)),
    evaluations: [{
      profile_name: profile.profile_name,
      profile_updated_at: profile.updated_at,
      profile_hash: profileHash,
      job_content_hash: contentHash,
      evaluated_at: migrationTimestamp,
      outcome: "included",
      match_score: Math.max(...occurrenceGroup.map(({ job }) => job.match_score)),
      reason_codes: [
        "active_at_legacy_verification",
        "san_francisco_posting_text",
        latestJob.workplace_arrangement.replace("-", "_"),
        "full_time",
        "compatible_title_or_role_family",
        "role_specific_ai_work",
        "published_base_maximum_at_least_200000",
        "migrated_evidence_only_description",
      ],
      evidence_snippets: evidenceSnippets.length ? evidenceSnippets : ["Migrated from a verified legacy run."],
    }],
    first_seen_at: firstDate(occurrenceGroup),
    last_seen_at: lastDate(occurrenceGroup),
    last_checked_at: lastDate(occurrenceGroup),
  }];
};

await mkdir(stagingRoot, { recursive: false });
await mkdir(companiesRoot, { recursive: false });
const companySummaries = [];
for (const [companyName, items] of [...byCompany.entries()].sort(([a], [b]) => a.localeCompare(b))) {
  const employer = employerByName.get(companyName);
  const companyId = slug(companyName.replace(/,?\s+inc\.?$/i, ""));
  const companyDomain = isCompanyDomain(employer.careers_url)
    ? new URL(employer.careers_url).hostname.replace(/^www\./, "")
    : null;
  const jobs = Object.fromEntries(items.map(jobRecord));
  const allDates = items.flatMap(({ occurrenceGroup }) => occurrenceGroup.flatMap((entry) => [entry.runStartedAt, entry.runCompletedAt])).sort();
  const office = buildOffice(items);
  const companyRecord = {
    schema_version: 1,
    company_id: companyId,
    company: {
      name: companyName,
      aliases: [],
      website_url: companyDomain ? new URL(employer.careers_url).origin + "/" : null,
      primary_domain: companyDomain,
      description: null,
      industries: [],
      funding_stage: null,
      employee_count_range: null,
      evidence: [],
    },
    status: "active",
    sources: [sourceFor(employer)],
    offices: [office],
    jobs,
    refresh: {
      last_attempted_at: employer.last_checked_at,
      last_successful_at: employer.last_checked_at,
      last_status: "completed",
      consecutive_failures: 0,
      last_error: null,
    },
    created_at: allDates.at(0),
    updated_at: migrationTimestamp,
  };
  await writeFile(
    path.join(companiesRoot, `${companyId}.json`),
    JSON.stringify(companyRecord, null, 2) + "\n",
  );
  companySummaries.push({
    company_id: companyId,
    company: companyName,
    job_count: Object.keys(jobs).length,
    address_verified: office.address_verified,
  });
}

const admittedCompanyNames = new Set(companySummaries.map((item) => item.company));
const sourceOnlyEmployers = employers
  .filter((employer) => !admittedCompanyNames.has(employer.company))
  .map((employer) => ({
    company: employer.company,
    careers_url: employer.careers_url,
    reason: "no_qualifying_historical_job_migrated",
  }));

const report = {
  schema_version: 1,
  migration_id: "2026-08-05-pre-catalog-to-company-catalog-v1",
  status: "staged",
  created_at: migrationTimestamp,
  profile_name: profile.profile_name,
  profile_updated_at: profile.updated_at,
  profile_hash: profileHash,
  legacy_snapshot: snapshotPath,
  summary: {
    legacy_employer_sources: employers.length,
    legacy_verified_job_records: occurrences.length,
    unique_verified_jobs: groups.size,
    qualifying_jobs_migrated: qualifying.length,
    jobs_withheld: withheld.length,
    duplicate_job_records_merged: occurrences.length - groups.size,
    company_files_created: companySummaries.length,
    source_only_employers_not_admitted: sourceOnlyEmployers.length,
    verified_addresses_imported: companySummaries.filter((item) => item.address_verified === true).length,
    evidence_only_descriptions: qualifying.length,
  },
  companies: companySummaries,
  withheld_jobs: withheld,
  source_only_employers: sourceOnlyEmployers,
  limitations: [
    "Legacy runs did not persist every complete canonical job description.",
    "Migrated descriptions are marked evidence_only and must be replaced on the next successful refresh.",
    "No new web requests or address searches were performed during migration.",
    "Active status reflects the latest legacy verification and requires refresh.",
  ],
};

await writeFile(
  path.join(stagingRoot, "migration-report.json"),
  JSON.stringify(report, null, 2) + "\n",
);

process.stdout.write(JSON.stringify(report.summary, null, 2) + "\n");
