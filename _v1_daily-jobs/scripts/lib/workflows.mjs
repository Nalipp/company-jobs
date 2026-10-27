import {
  candidateKeyFor,
  clone,
  identityFor,
  jobHashes,
  normalizeUrl,
  toDateOnly,
  unique,
} from "./core.mjs";
import { catalogIndex, findCatalogJob } from "./catalog.mjs";
import { collectSource, supportedProviders } from "./providers.mjs";
import { cheapScreen } from "./profile-gate.mjs";
import { findMemoryEntry, shouldSkipFromMemory, upsertMemory } from "./candidate-memory.mjs";

const verificationPending = () => ({ status: "queued", run_id: null, reason: null, verified_at: null });

const candidateFromJob = ({ job, company, source, now, changeState, screen }) => {
  const identity = identityFor(job);
  const hashes = jobHashes(job);
  return {
    candidate_key: candidateKeyFor(job),
    identity,
    title: job.title,
    company: company.company.name,
    discovery_url: job.applyUrl ?? job.canonicalUrl,
    canonical_url: normalizeUrl(job.canonicalUrl),
    source_names: [`${company.company_id}:${source.source_id}`],
    location_text: job.locationText,
    workplace_arrangement: job.workplaceArrangement,
    employment_type: job.employmentType,
    seniority: job.seniority,
    compensation: job.compensation,
    posted_date: job.postedDate,
    description_text: job.descriptionText,
    description_completeness: job.descriptionCompleteness,
    hashes,
    change_state: changeState,
    discovered_at: now,
    preliminary_fit: unique(screen.fits),
    preliminary_concerns: unique(screen.concerns),
    verification: verificationPending(),
  };
};

const existingJobFor = (company, source, fetched) => Object.values(company.jobs).find((job) =>
  (job.source_id === source.source_id && fetched.sourceJobId && job.source_job_id === fetched.sourceJobId)
  || normalizeUrl(job.canonical_url) === normalizeUrl(fetched.canonicalUrl)
);

const sourceCheckpoint = (company, source, values = {}) => ({
  name: `${company.company.name} ${source.provider} source`,
  kind: source.source_type === "ats" ? "ats" : "employer_careers",
  provider: supportedProviders.has(source.provider) ? source.provider : "custom",
  board_id: source.board_id,
  company_id: company.company_id,
  query: `poll ${source.url}`,
  url: source.url,
  status: "completed",
  fetched_count: 0,
  queued_count: 0,
  unchanged_count: 0,
  rejected_count: 0,
  memory_skipped_count: 0,
  notes: null,
  ...values,
});

const latestRefreshStart = (companies, now) => {
  const previous = companies
    .map((company) => company.refresh.last_successful_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  return toDateOnly(previous ?? now);
};

export const runRefresh = async ({
  companies,
  profile,
  profileHash,
  memory,
  now,
  previousDiscoveryId = null,
  collect = collectSource,
  candidateLimit = profile.search_behavior.discovery_candidate_limit,
}) => {
  const nextCompanies = companies.map(clone);
  let nextMemory = clone(memory);
  const candidates = [];
  const sources = [];
  const queries = [];
  let anyFailure = false;
  for (const company of nextCompanies) {
    let successes = 0;
    const errors = [];
    company.refresh.last_attempted_at = now;
    for (const source of company.sources) {
      queries.push(`poll ${source.url}`);
      if (!supportedProviders.has(source.provider) || !source.board_id) {
        anyFailure = true;
        errors.push(`${source.source_id}: unsupported structured source`);
        sources.push(sourceCheckpoint(company, source, {
          status: "unsupported",
          notes: "No structured collector is configured; no missing-job inference was made.",
        }));
        continue;
      }
      try {
        const result = await collect(source);
        successes += 1;
        source.status = "active";
        source.last_checked_at = now;
        source.last_successful_at = now;
        const seenJobIds = new Set();
        const counts = { fetched: result.jobs.length, queued: 0, unchanged: 0, rejected: 0, memorySkipped: 0 };
        for (const fetched of result.jobs) {
          const existing = existingJobFor(company, source, fetched);
          if (existing) {
            seenJobIds.add(existing.job_id);
            existing.activity.status = "active";
            existing.activity.missing_from_source_count = 0;
            existing.activity.inactive_confirmed_at = null;
            existing.last_seen_at = now;
            existing.last_checked_at = now;
          }
          const screen = cheapScreen(fetched, profile);
          const hashes = jobHashes(fetched);
          const unchanged = existing
            && existing.raw.description_completeness === "full"
            && existing.hashes.metadata_hash === hashes.metadata_hash
            && existing.hashes.content_hash === hashes.content_hash;
          if (unchanged) {
            counts.unchanged += 1;
            continue;
          }
          if (!existing && screen.status === "excluded") {
            const rejected = candidateFromJob({ job: fetched, company, source, now, changeState: "new", screen });
            nextMemory = upsertMemory(nextMemory, rejected, {
              now,
              profileName: profile.profile_name,
              profileHash,
              outcome: "excluded",
              reasonCodes: screen.exclusionReasons,
            });
            counts.rejected += 1;
            continue;
          }
          const changeState = existing?.raw.description_completeness === "evidence_only"
            ? "description_backfill"
            : existing ? "changed" : "new";
          const candidate = candidateFromJob({ job: fetched, company, source, now, changeState, screen });
          const remembered = findMemoryEntry(nextMemory, candidate);
          if (!existing && shouldSkipFromMemory(remembered, candidate, profileHash)) {
            counts.memorySkipped += 1;
            continue;
          }
          if (candidates.length < candidateLimit) {
            candidates.push(candidate);
            counts.queued += 1;
            nextMemory = upsertMemory(nextMemory, candidate, {
              now,
              profileName: profile.profile_name,
              profileHash,
              outcome: "queued",
              reasonCodes: candidate.preliminary_concerns,
            });
          }
        }
        if (result.complete) {
          for (const job of Object.values(company.jobs).filter((job) => job.source_id === source.source_id)) {
            if (seenJobIds.has(job.job_id)) continue;
            job.activity.missing_from_source_count += 1;
            job.last_checked_at = now;
            if (job.activity.missing_from_source_count >= 2) {
              job.activity.status = "inactive";
              job.activity.inactive_confirmed_at ??= now;
            }
          }
        }
        sources.push(sourceCheckpoint(company, source, {
          url: result.requestUrl,
          fetched_count: counts.fetched,
          queued_count: counts.queued,
          unchanged_count: counts.unchanged,
          rejected_count: counts.rejected,
          memory_skipped_count: counts.memorySkipped,
        }));
      } catch (error) {
        anyFailure = true;
        errors.push(`${source.source_id}: ${error.message}`);
        source.last_checked_at = now;
        source.status = "unavailable";
        sources.push(sourceCheckpoint(company, source, { status: "error", notes: error.message }));
      }
    }
    if (successes === company.sources.length) {
      company.refresh.last_status = "completed";
      company.refresh.last_successful_at = now;
      company.refresh.consecutive_failures = 0;
      company.refresh.last_error = null;
    } else if (successes > 0) {
      company.refresh.last_status = "partial";
      company.refresh.last_successful_at = now;
      company.refresh.consecutive_failures = 0;
      company.refresh.last_error = errors.join("; ") || null;
    } else {
      company.refresh.last_status = "failed";
      company.refresh.consecutive_failures += 1;
      company.refresh.last_error = errors.join("; ") || "No supported source completed";
    }
    company.updated_at = now;
  }
  const end = toDateOnly(now);
  const checkpoint = {
    schema_version: 2,
    collection_mode: "known_company_refresh",
    discovery_id: `${end}-refresh-${now.replace(/\D/g, "").slice(8, 14)}`,
    profile_name: profile.profile_name,
    profile_hash: profileHash,
    status: anyFailure ? "partial" : "completed",
    strategy: "incremental",
    previous_discovery_id: previousDiscoveryId,
    started_at: now,
    completed_at: now,
    date_range: { start: latestRefreshStart(companies, now), end, timezone: profile.timezone },
    query_matrix: unique(queries),
    coverage: { candidate_limit: candidateLimit, limit_reached: candidates.length >= candidateLimit },
    sources,
    candidates,
  };
  return { checkpoint, companies: nextCompanies, memory: nextMemory };
};

const expansionJob = (lead) => ({
  provider: lead.provider,
  boardId: lead.board_id,
  sourceJobId: lead.source_job_id,
  canonicalUrl: lead.canonical_url,
  applyUrl: lead.discovery_url,
  company: lead.company,
  title: lead.title,
  locationText: lead.location_text,
  workplaceArrangement: lead.workplace_arrangement,
  employmentType: lead.employment_type,
  seniority: lead.seniority,
  compensation: lead.compensation,
  postedDate: lead.posted_date,
  descriptionText: lead.description_text,
  descriptionCompleteness: lead.description_completeness,
});

export const runExpansion = ({
  input,
  companies,
  profile,
  profileHash,
  memory,
  now,
  previousDiscoveryId = null,
  retryReview = false,
  candidateLimit = profile.search_behavior.discovery_candidate_limit,
}) => {
  let nextMemory = clone(memory);
  const index = catalogIndex(companies);
  const candidates = [];
  const seen = new Set();
  const counts = new Map(input.sources.map((source) => [source.name, {
    fetched: 0, queued: 0, unchanged: 0, rejected: 0, memorySkipped: 0,
  }]));
  for (const lead of input.leads) {
    const job = expansionJob(lead);
    const screen = cheapScreen(job, profile);
    const shellCompany = { company_id: "expansion", company: { name: lead.company } };
    const shellSource = { source_id: lead.provider === "unknown" ? "web" : lead.provider };
    const catalogMatch = findCatalogJob(index, job);
    const catalogHashes = catalogMatch?.job.hashes;
    const hashes = jobHashes(job);
    const changeState = catalogMatch
      ? catalogHashes.metadata_hash === hashes.metadata_hash && catalogHashes.content_hash === hashes.content_hash
        ? "unknown"
        : "changed"
      : "new";
    const candidate = candidateFromJob({ job, company: shellCompany, source: shellSource, now, changeState, screen });
    candidate.source_names = lead.source_names;
    candidate.preliminary_fit = unique([...lead.preliminary_fit, ...candidate.preliminary_fit]);
    candidate.preliminary_concerns = unique([...lead.preliminary_concerns, ...candidate.preliminary_concerns]);
    for (const name of lead.source_names) counts.get(name) && (counts.get(name).fetched += 1);
    if (seen.has(candidate.identity.fingerprint)) continue;
    seen.add(candidate.identity.fingerprint);
    if (catalogMatch && changeState === "unknown") {
      for (const name of lead.source_names) counts.get(name) && (counts.get(name).unchanged += 1);
      continue;
    }
    if (!catalogMatch && screen.status === "excluded") {
      nextMemory = upsertMemory(nextMemory, candidate, {
        now,
        profileName: profile.profile_name,
        profileHash,
        outcome: "excluded",
        reasonCodes: screen.exclusionReasons,
      });
      for (const name of lead.source_names) counts.get(name) && (counts.get(name).rejected += 1);
      continue;
    }
    const remembered = findMemoryEntry(nextMemory, candidate);
    if (!catalogMatch && shouldSkipFromMemory(remembered, candidate, profileHash, { retryReview })) {
      for (const name of lead.source_names) counts.get(name) && (counts.get(name).memorySkipped += 1);
      continue;
    }
    if (remembered?.outcome === "needs_review") candidate.change_state = "known_review";
    if (candidates.length < candidateLimit) {
      candidates.push(candidate);
      nextMemory = upsertMemory(nextMemory, candidate, {
        now,
        profileName: profile.profile_name,
        profileHash,
        outcome: "queued",
        reasonCodes: candidate.preliminary_concerns,
      });
      for (const name of lead.source_names) counts.get(name) && (counts.get(name).queued += 1);
    }
  }
  const sourceRecords = input.sources.map((source) => {
    const sourceCounts = counts.get(source.name);
    return {
      name: source.name,
      kind: source.kind,
      provider: "unknown",
      board_id: null,
      company_id: null,
      query: source.query,
      url: source.url,
      status: source.status,
      fetched_count: sourceCounts.fetched,
      queued_count: sourceCounts.queued,
      unchanged_count: sourceCounts.unchanged,
      rejected_count: sourceCounts.rejected,
      memory_skipped_count: sourceCounts.memorySkipped,
      notes: source.notes,
    };
  });
  const checkpoint = {
    schema_version: 2,
    collection_mode: "catalog_expansion",
    discovery_id: input.collection_id,
    profile_name: profile.profile_name,
    profile_hash: profileHash,
    status: sourceRecords.some((source) => source.status !== "completed") ? "partial" : "completed",
    strategy: input.strategy,
    previous_discovery_id: previousDiscoveryId,
    started_at: input.started_at,
    completed_at: input.completed_at,
    date_range: input.date_range,
    query_matrix: input.query_matrix,
    coverage: { candidate_limit: candidateLimit, limit_reached: candidates.length >= candidateLimit },
    sources: sourceRecords,
    candidates,
  };
  return { checkpoint, memory: nextMemory };
};

export const syncMemoryFromCheckpoint = ({ memory, checkpoint, now }) => {
  let next = clone(memory);
  for (const candidate of checkpoint.candidates) {
    const status = candidate.verification.status;
    const outcome = status === "verified" ? "included" : status;
    next = upsertMemory(next, candidate, {
      now,
      profileName: checkpoint.profile_name,
      profileHash: checkpoint.profile_hash ?? null,
      outcome,
      reasonCodes: unique([
        ...candidate.preliminary_concerns,
        ...(candidate.verification.reason ? [candidate.verification.reason] : []),
      ]),
    });
  }
  return next;
};

