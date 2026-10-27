import { readdir } from "node:fs/promises";
import path from "node:path";
import { emptyMemory, upsertMemory } from "./candidate-memory.mjs";
import { identityFor, jobHashes, normalizeUrl, readJson, unique } from "./core.mjs";

const historicalCandidate = (candidate) => {
  const job = {
    provider: candidate.identity?.provider ?? "unknown",
    boardId: candidate.identity?.board_id ?? null,
    sourceJobId: candidate.identity?.source_job_id ?? null,
    canonicalUrl: candidate.canonical_url,
    company: candidate.company,
    title: candidate.title,
    locationText: candidate.location_text,
    workplaceArrangement: candidate.workplace_arrangement,
    employmentType: candidate.employment_type ?? null,
    seniority: candidate.seniority ?? null,
    compensation: candidate.compensation,
    descriptionText: candidate.description_text ?? null,
    descriptionCompleteness: candidate.description_completeness ?? "none",
  };
  return {
    identity: candidate.identity ?? identityFor(job),
    hashes: candidate.hashes ?? jobHashes(job),
    canonical_url: normalizeUrl(candidate.canonical_url),
    company: candidate.company,
    title: candidate.title,
    location_text: candidate.location_text,
    source_names: candidate.source_names,
  };
};

const outcomeFor = (status) => ({
  verified: "needs_review",
  excluded: "excluded",
  needs_review: "needs_review",
  error: "error",
  queued: "queued",
})[status];

export const rebuildMemory = async ({ discoveriesDirectory, companyRecords, profile, profileHash, now }) => {
  let memory = emptyMemory(now);
  const discoveryFiles = (await readdir(discoveriesDirectory))
    .filter((name) => name.endsWith(".json"))
    .sort();
  for (const name of discoveryFiles) {
    const checkpoint = await readJson(path.join(discoveriesDirectory, name));
    for (const rawCandidate of checkpoint.candidates) {
      const candidate = historicalCandidate(rawCandidate);
      const checkedAt = rawCandidate.verification.verified_at ?? rawCandidate.discovered_at ?? checkpoint.completed_at ?? now;
      memory = upsertMemory(memory, candidate, {
        now: checkedAt,
        profileName: checkpoint.profile_name,
        profileHash: checkpoint.profile_hash ?? null,
        outcome: outcomeFor(rawCandidate.verification.status),
        reasonCodes: unique([
          ...(rawCandidate.preliminary_concerns ?? []),
          ...(rawCandidate.verification.reason ? [rawCandidate.verification.reason] : []),
        ]),
      });
    }
  }
  for (const record of companyRecords) {
    const company = record.value ?? record;
    for (const job of Object.values(company.jobs)) {
      const source = company.sources.find((item) => item.source_id === job.source_id);
      const candidate = {
        identity: identityFor({
          provider: source?.provider ?? "unknown",
          boardId: source?.board_id ?? null,
          sourceJobId: job.source_job_id,
          canonicalUrl: job.canonical_url,
          company: company.company.name,
          title: job.raw.title,
          locationText: job.raw.location_text,
        }),
        hashes: job.hashes,
        canonical_url: job.canonical_url,
        company: company.company.name,
        title: job.raw.title,
        location_text: job.raw.location_text,
        source_names: [`catalog:${company.company_id}:${job.source_id}`],
      };
      const latestEvaluation = [...job.evaluations].sort((a, b) => a.evaluated_at.localeCompare(b.evaluated_at)).at(-1);
      memory = upsertMemory(memory, candidate, {
        now: job.last_checked_at,
        profileName: latestEvaluation?.profile_name ?? profile.profile_name,
        profileHash: latestEvaluation?.profile_hash ?? profileHash,
        outcome: "included",
        reasonCodes: latestEvaluation?.reason_codes ?? [],
      });
    }
  }
  memory.updated_at = now;
  return memory;
};
