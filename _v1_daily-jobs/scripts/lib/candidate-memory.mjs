import { identityFor, jobHashes, normalizeUrl, unique } from "./core.mjs";

export const emptyMemory = (now) => ({ schema_version: 1, updated_at: now, entries: {} });

export const memoryEntryFor = (candidate, {
  now,
  profileName,
  profileHash,
  outcome = "queued",
  reasonCodes = [],
} = {}) => {
  const identity = candidate.identity ?? identityFor(candidate);
  const hashes = candidate.hashes ?? jobHashes(candidate);
  return {
    fingerprint: identity.fingerprint,
    provider: identity.provider,
    board_id: identity.board_id,
    source_job_id: identity.source_job_id,
    canonical_url: normalizeUrl(candidate.canonical_url ?? candidate.canonicalUrl),
    company: candidate.company,
    title: candidate.title,
    location_text: candidate.location_text ?? candidate.locationText ?? null,
    metadata_hash: hashes.metadata_hash,
    content_hash: hashes.content_hash,
    profile_name: profileName,
    profile_hash: profileHash,
    outcome,
    reason_codes: unique(reasonCodes),
    source_names: unique(candidate.source_names ?? candidate.sourceNames ?? []),
    first_seen_at: now,
    last_seen_at: now,
    last_checked_at: now,
  };
};

export const findMemoryEntry = (memory, candidate) => {
  const identity = candidate.identity ?? identityFor(candidate);
  if (memory.entries[identity.fingerprint]) return memory.entries[identity.fingerprint];
  const canonicalUrl = normalizeUrl(candidate.canonical_url ?? candidate.canonicalUrl);
  return Object.values(memory.entries).find((entry) =>
    (canonicalUrl && entry.canonical_url === canonicalUrl)
    || (identity.provider !== "unknown"
      && entry.provider === identity.provider
      && entry.board_id === identity.board_id
      && entry.source_job_id === identity.source_job_id)
  ) ?? null;
};

export const upsertMemory = (memory, candidate, options) => {
  const next = structuredClone(memory);
  const entry = memoryEntryFor(candidate, options);
  const existing = findMemoryEntry(next, candidate);
  if (existing && existing.fingerprint !== entry.fingerprint) delete next.entries[existing.fingerprint];
  next.entries[entry.fingerprint] = {
    ...existing,
    ...entry,
    source_names: unique([...(existing?.source_names ?? []), ...entry.source_names]),
    first_seen_at: existing?.first_seen_at ?? entry.first_seen_at,
  };
  next.updated_at = options.now;
  return next;
};

export const shouldSkipFromMemory = (entry, candidate, profileHash, { retryReview = false } = {}) => {
  if (!entry || entry.profile_hash !== profileHash) return false;
  const hashes = candidate.hashes ?? jobHashes(candidate);
  if (entry.metadata_hash !== hashes.metadata_hash || entry.content_hash !== hashes.content_hash) return false;
  if (entry.outcome === "needs_review") return !retryReview;
  return ["queued", "included", "excluded"].includes(entry.outcome);
};

