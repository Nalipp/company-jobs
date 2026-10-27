#!/usr/bin/env node
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { jobHashes, normalizeUrl } from "./lib/core.mjs";
import { assertValid, compileSchema } from "./lib/validation.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const json = async (file) => JSON.parse(await readFile(file, "utf8"));
const filesMatching = async (directory, pattern) => (await readdir(directory))
  .filter((name) => pattern.test(name))
  .sort()
  .map((name) => path.join(directory, name));

const schemas = {
  profile: await compileSchema(path.join(root, "schemas/profile.schema.json")),
  discovery: await compileSchema(path.join(root, "schemas/discovery-run.schema.json")),
  run: await compileSchema(path.join(root, "schemas/search-run.schema.json")),
  company: await compileSchema(path.join(root, "schemas/company-catalog.schema.json")),
  memory: await compileSchema(path.join(root, "schemas/candidate-memory.schema.json")),
  expansion: await compileSchema(path.join(root, "schemas/expansion-input.schema.json")),
  admission: await compileSchema(path.join(root, "schemas/job-admission.schema.json")),
};

const checks = [];
const validateFile = async (validator, file) => {
  const value = await json(file);
  assertValid(validator, value, path.relative(root, file));
  checks.push(path.relative(root, file));
  return value;
};

await validateFile(schemas.profile, path.join(root, "profiles/default.json"));
await validateFile(schemas.profile, path.join(root, "profiles/default.example.json"));
for (const file of await filesMatching(path.join(root, "discoveries"), /\.json$/)) await validateFile(schemas.discovery, file);
for (const file of await filesMatching(path.join(root, "runs"), /\.json$/)) await validateFile(schemas.run, file);

const canonicalUrls = new Set();
for (const file of await filesMatching(path.join(root, "catalog/companies"), /\.json$/)) {
  const company = await validateFile(schemas.company, file);
  if (`${company.company_id}.json` !== path.basename(file)) throw new Error(`${file}: filename does not match company_id`);
  const sourceIds = new Set(company.sources.map((source) => source.source_id));
  for (const [jobId, job] of Object.entries(company.jobs)) {
    if (jobId !== job.job_id) throw new Error(`${file}: jobs key does not match job_id ${jobId}`);
    if (!sourceIds.has(job.source_id)) throw new Error(`${file}: missing source ${job.source_id}`);
    const url = normalizeUrl(job.canonical_url);
    if (canonicalUrls.has(url)) throw new Error(`${file}: duplicate canonical URL ${url}`);
    canonicalUrls.add(url);
    if (job.raw.description_completeness === "full") {
      const hashes = jobHashes({
        canonicalUrl: job.canonical_url,
        title: job.raw.title,
        locationText: job.raw.location_text,
        workplaceArrangement: job.raw.workplace_arrangement,
        employmentType: job.raw.employment_type,
        seniority: job.raw.seniority,
        compensation: job.raw.compensation,
        descriptionText: job.raw.description_text,
        descriptionCompleteness: "full",
      });
      if (hashes.metadata_hash !== job.hashes.metadata_hash || hashes.content_hash !== job.hashes.content_hash) {
        throw new Error(`${file}: hashes do not match full job ${jobId}`);
      }
    }
  }
}
await validateFile(schemas.company, path.join(root, "catalog/company.example.json"));
await validateFile(schemas.memory, path.join(root, "catalog/candidate-memory.json"));
await validateFile(schemas.expansion, path.join(root, "examples/expansion-input.example.json"));
await validateFile(schemas.admission, path.join(root, "examples/job-admission.example.json"));

process.stdout.write(`Validated ${checks.length} data files and cross-file catalog invariants.\n`);

