import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export const unique = (values) => [...new Set(values.filter((value) => value !== null && value !== undefined && value !== ""))];

export const sha256 = (value) => createHash("sha256").update(String(value)).digest("hex");

export const stableStringify = (value) => {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
};

export const slugify = (value) => String(value)
  .toLowerCase()
  .normalize("NFKD")
  .replace(/[^a-z0-9]+/g, "-")
  .replace(/^-|-$/g, "");

export const normalizeText = (value) => String(value ?? "")
  .replace(/\r\n?/g, "\n")
  .replace(/[ \t]+/g, " ")
  .replace(/ *\n */g, "\n")
  .replace(/\n{3,}/g, "\n\n")
  .trim();

const entityMap = {
  amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: "\"",
  ndash: "–", mdash: "—", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“",
};

export const htmlToText = (value) => normalizeText(String(value ?? "")
  .replace(/<(br|\/p|\/li|\/div|\/h[1-6])\b[^>]*>/gi, "\n")
  .replace(/<li\b[^>]*>/gi, "- ")
  .replace(/<[^>]+>/g, " ")
  .replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (_, entity) => {
    if (entity[0] === "#") {
      const hex = entity[1]?.toLowerCase() === "x";
      const number = Number.parseInt(entity.slice(hex ? 2 : 1), hex ? 16 : 10);
      return Number.isFinite(number) ? String.fromCodePoint(number) : `&${entity};`;
    }
    return entityMap[entity.toLowerCase()] ?? `&${entity};`;
  }));

export const normalizeUrl = (value) => {
  if (!value) return null;
  const url = new URL(value);
  url.hash = "";
  for (const key of [...url.searchParams.keys()]) {
    if (/^(utm_|gh_src$|lever-source$|source$)/i.test(key)) url.searchParams.delete(key);
  }
  if (url.pathname !== "/") url.pathname = url.pathname.replace(/\/+$/, "");
  return url.toString();
};

export const toDateOnly = (value) => {
  if (!value) return null;
  const match = String(value).match(/^(\d{4}-\d{2}-\d{2})/);
  if (match) return match[1];
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date.toISOString().slice(0, 10);
};

export const dateInTimeZone = (iso, timeZone) => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(iso));
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
};

export const normalizeWorkplace = (value, fallbackText = "") => {
  const text = `${value ?? ""} ${fallbackText}`.toLowerCase();
  if (/\bhybrid\b/.test(text)) return "hybrid";
  if (/\bon[ -]?site\b|\bin[ -]?office\b|\boffice[ -]?based\b/.test(text)) return "on-site";
  if (/\bremote\b/.test(text)) return "remote";
  return "unknown";
};

export const normalizeEmployment = (value, fallbackText = "") => {
  const text = `${value ?? ""} ${fallbackText}`.toLowerCase().replace(/[_-]+/g, " ");
  if (/\bfull\s*time\b|\bfulltime\b/.test(text)) return "full-time";
  if (/\bpart\s*time\b|\bparttime\b/.test(text)) return "part-time";
  if (/\bcontract(or)?\b/.test(text)) return "contract";
  if (/\bintern(ship)?\b/.test(text)) return "internship";
  return value ? normalizeText(value).toLowerCase() : null;
};

export const jobMetadata = (job) => ({
  title: normalizeText(job.title),
  location: normalizeText(job.locationText ?? job.location_text) || null,
  workplace: job.workplaceArrangement ?? job.workplace_arrangement ?? "unknown",
  employment: job.employmentType ?? job.employment_type ?? null,
  seniority: job.seniority ?? null,
  compensation: job.compensation ?? null,
  canonical_url: normalizeUrl(job.canonicalUrl ?? job.canonical_url),
});

export const jobHashes = (job) => {
  const description = job.descriptionText ?? job.description_text ?? null;
  const completeness = job.descriptionCompleteness ?? job.description_completeness ?? "none";
  return {
    metadata_hash: sha256(stableStringify(jobMetadata(job))),
    content_hash: completeness === "none" || !description ? null : sha256(normalizeText(description)),
    hash_algorithm: "sha256",
  };
};

export const identityFor = (job) => {
  const provider = job.provider ?? "unknown";
  const boardId = job.boardId ?? job.board_id ?? null;
  const sourceJobId = job.sourceJobId ?? job.source_job_id ?? null;
  const canonicalUrl = normalizeUrl(job.canonicalUrl ?? job.canonical_url);
  let identity;
  if (provider !== "unknown" && boardId && sourceJobId) {
    identity = `ats:${provider}:${String(boardId).toLowerCase()}:${sourceJobId}`;
  } else if (canonicalUrl) {
    identity = `url:${canonicalUrl.toLowerCase()}`;
  } else {
    identity = `fallback:${normalizeText(job.company).toLowerCase()}|${normalizeText(job.title).toLowerCase()}|${normalizeText(job.locationText ?? job.location_text).toLowerCase()}`;
  }
  return {
    provider,
    board_id: boardId,
    source_job_id: sourceJobId,
    fingerprint: sha256(identity),
  };
};

export const candidateKeyFor = (job) => `job-${identityFor(job).fingerprint.slice(0, 24)}`;

export const readJson = async (file) => JSON.parse(await readFile(file, "utf8"));

export const readJsonWithText = async (file) => {
  const text = await readFile(file, "utf8");
  return { value: JSON.parse(text), text };
};

export const writeJsonAtomic = async (file, value) => {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.tmp-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, file);
};

export const clone = (value) => structuredClone(value);

