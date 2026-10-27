#!/usr/bin/env node
import { readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  catalogIndex,
  companyExists,
  companyPath,
  loadCompanies,
  planAdmission,
} from "./lib/catalog.mjs";
import { emptyMemory, upsertMemory } from "./lib/candidate-memory.mjs";
import {
  dateInTimeZone,
  identityFor,
  jobHashes,
  readJson,
  readJsonWithText,
  sha256,
  writeJsonAtomic,
} from "./lib/core.mjs";
import { rebuildMemory } from "./lib/history-memory.mjs";
import { assertValid, compileSchema } from "./lib/validation.mjs";
import { runExpansion, runRefresh, syncMemoryFromCheckpoint } from "./lib/workflows.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const companiesDirectory = path.join(root, "catalog/companies");
const discoveriesDirectory = path.join(root, "discoveries");
const memoryFile = path.join(root, "catalog/candidate-memory.json");
const profileFile = path.join(root, "profiles/default.json");

const usage = `Usage:
  node scripts/daily-jobs.mjs refresh [--company ID] [--write] [--now ISO]
  node scripts/daily-jobs.mjs expand --input FILE [--retry-review] [--write] [--now ISO]
  node scripts/daily-jobs.mjs admit --input FILE [--write] [--now ISO]
  node scripts/daily-jobs.mjs sync-memory --discovery FILE [--write] [--now ISO]
  node scripts/daily-jobs.mjs rebuild-memory [--write] [--now ISO]

Commands are dry-run by default. Pass --write to persist their planned changes.`;

const parseArgs = (args) => {
  const options = { _: [] };
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (!value.startsWith("--")) { options._.push(value); continue; }
    const key = value.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (["write", "retryReview"].includes(key)) options[key] = true;
    else options[key] = args[++index];
  }
  return options;
};

const loadState = async (now) => {
  const { value: profile, text: profileText } = await readJsonWithText(profileFile);
  if (profile.status !== "approved") throw new Error("The active profile is not approved");
  const companyRecords = await loadCompanies(companiesDirectory);
  let memory;
  try { memory = await readJson(memoryFile); } catch (error) {
    if (error.code !== "ENOENT") throw error;
    memory = emptyMemory(now);
  }
  return { profile, profileHash: sha256(profileText), companyRecords, memory };
};

const latestDiscoveryId = async () => {
  const files = (await readdir(discoveriesDirectory)).filter((name) => /^\d{4}-\d{2}-\d{2}.*\.json$/.test(name)).sort();
  if (!files.length) return null;
  return (await readJson(path.join(discoveriesDirectory, files.at(-1)))).discovery_id;
};

const nextDiscoveryPath = async (now, timeZone) => {
  const date = dateInTimeZone(now, timeZone);
  const files = new Set(await readdir(discoveriesDirectory));
  if (!files.has(`${date}.json`)) return path.join(discoveriesDirectory, `${date}.json`);
  let suffix = 2;
  while (files.has(`${date}-${suffix}.json`)) suffix += 1;
  return path.join(discoveriesDirectory, `${date}-${suffix}.json`);
};

const validators = new Map();
const validatorFor = async (schemaName) => {
  if (!validators.has(schemaName)) validators.set(schemaName, await compileSchema(path.join(root, `schemas/${schemaName}`)));
  return validators.get(schemaName);
};

const validate = async (schemaName, value, label) => assertValid(await validatorFor(schemaName), value, label);

const output = (value) => process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);

const writeRefresh = async ({ result, selectedRecords, checkpointFile }) => {
  await validate("discovery-run.schema.json", result.checkpoint, "refresh checkpoint");
  await validate("candidate-memory.schema.json", result.memory, "candidate memory");
  for (let index = 0; index < result.companies.length; index += 1) {
    await validate("company-catalog.schema.json", result.companies[index], `company ${result.companies[index].company_id}`);
    await writeJsonAtomic(selectedRecords[index].file, result.companies[index]);
  }
  await writeJsonAtomic(checkpointFile, result.checkpoint);
  await writeJsonAtomic(memoryFile, result.memory);
};

const commandRefresh = async (options, now) => {
  const state = await loadState(now);
  const selectedRecords = options.company
    ? state.companyRecords.filter((record) => record.value.company_id === options.company)
    : state.companyRecords;
  if (!selectedRecords.length) throw new Error(`Unknown company: ${options.company}`);
  const result = await runRefresh({
    companies: selectedRecords.map((record) => record.value),
    profile: state.profile,
    profileHash: state.profileHash,
    memory: state.memory,
    now,
    previousDiscoveryId: await latestDiscoveryId(),
  });
  const checkpointFile = await nextDiscoveryPath(now, state.profile.timezone);
  if (options.write) await writeRefresh({ result, selectedRecords, checkpointFile });
  output({
    command: "refresh",
    dry_run: !options.write,
    checkpoint: options.write ? path.relative(root, checkpointFile) : null,
    status: result.checkpoint.status,
    companies: selectedRecords.length,
    sources: result.checkpoint.sources.length,
    queued_candidates: result.checkpoint.candidates.length,
    source_results: result.checkpoint.sources.map((source) => ({
      company_id: source.company_id,
      provider: source.provider,
      status: source.status,
      fetched: source.fetched_count,
      queued: source.queued_count,
      unchanged: source.unchanged_count,
      rejected: source.rejected_count,
      memory_skipped: source.memory_skipped_count,
      notes: source.notes,
    })),
    candidate_results: result.checkpoint.candidates.map((candidate) => ({
      candidate_key: candidate.candidate_key,
      source_job_id: candidate.identity.source_job_id,
      title: candidate.title,
      canonical_url: candidate.canonical_url,
      change_state: candidate.change_state,
      preliminary_fit: candidate.preliminary_fit,
      preliminary_concerns: candidate.preliminary_concerns,
    })),
  });
};

const commandExpand = async (options, now) => {
  if (!options.input) throw new Error("expand requires --input FILE");
  const state = await loadState(now);
  const input = await readJson(path.resolve(options.input));
  await validate("expansion-input.schema.json", input, "expansion input");
  if (input.profile_name !== state.profile.profile_name) throw new Error("Expansion input profile_name does not match the active profile");
  const result = runExpansion({
    input,
    companies: state.companyRecords,
    profile: state.profile,
    profileHash: state.profileHash,
    memory: state.memory,
    now,
    previousDiscoveryId: await latestDiscoveryId(),
    retryReview: options.retryReview,
  });
  await validate("discovery-run.schema.json", result.checkpoint, "expansion checkpoint");
  await validate("candidate-memory.schema.json", result.memory, "candidate memory");
  const checkpointFile = await nextDiscoveryPath(now, state.profile.timezone);
  if (options.write) {
    await writeJsonAtomic(checkpointFile, result.checkpoint);
    await writeJsonAtomic(memoryFile, result.memory);
  }
  output({
    command: "expand",
    dry_run: !options.write,
    checkpoint: options.write ? path.relative(root, checkpointFile) : null,
    status: result.checkpoint.status,
    input_leads: input.leads.length,
    queued_candidates: result.checkpoint.candidates.length,
    candidate_results: result.checkpoint.candidates.map((candidate) => ({
      candidate_key: candidate.candidate_key,
      title: candidate.title,
      company: candidate.company,
      canonical_url: candidate.canonical_url,
      change_state: candidate.change_state,
      preliminary_concerns: candidate.preliminary_concerns,
    })),
  });
};

const commandAdmit = async (options, now) => {
  if (!options.input) throw new Error("admit requires --input FILE");
  const state = await loadState(now);
  const admission = await readJson(path.resolve(options.input));
  await validate("job-admission.schema.json", admission, "job admission");
  const index = catalogIndex(state.companyRecords);
  const existingCompany = index.byCompanyId.get(admission.company.company_id) ?? null;
  const result = planAdmission({ existingCompany, admission, profile: state.profile, profileHash: state.profileHash, now });
  await validate("company-catalog.schema.json", result.company, "planned company record");
  const memoryCandidate = {
    identity: result.identity,
    hashes: result.hashes,
    canonical_url: admission.job.canonical_url,
    company: admission.company.name,
    title: admission.job.title,
    location_text: admission.job.location_text,
    source_names: admission.discovered_from,
  };
  const memory = upsertMemory(state.memory, memoryCandidate, {
    now,
    profileName: state.profile.profile_name,
    profileHash: state.profileHash,
    outcome: "included",
    reasonCodes: admission.evaluation.reason_codes,
  });
  await validate("candidate-memory.schema.json", memory, "candidate memory");
  const file = companyPath(companiesDirectory, admission.company.company_id);
  if (options.write) {
    if (!existingCompany && await companyExists(file)) throw new Error(`Refusing to overwrite unexpected company file: ${file}`);
    await writeJsonAtomic(file, result.company);
    await writeJsonAtomic(memoryFile, memory);
  }
  output({ command: "admit", dry_run: !options.write, company: admission.company.company_id, job_id: result.jobId, created_company: result.created });
};

const commandSyncMemory = async (options, now) => {
  if (!options.discovery) throw new Error("sync-memory requires --discovery FILE");
  const state = await loadState(now);
  const checkpoint = await readJson(path.resolve(options.discovery));
  await validate("discovery-run.schema.json", checkpoint, "discovery checkpoint");
  if (checkpoint.schema_version !== 2) throw new Error("sync-memory accepts schema-version-2 checkpoints only");
  const memory = syncMemoryFromCheckpoint({ memory: state.memory, checkpoint, now });
  await validate("candidate-memory.schema.json", memory, "candidate memory");
  if (options.write) await writeJsonAtomic(memoryFile, memory);
  output({ command: "sync-memory", dry_run: !options.write, entries: Object.keys(memory.entries).length });
};

const commandRebuildMemory = async (options, now) => {
  const state = await loadState(now);
  const memory = await rebuildMemory({
    discoveriesDirectory,
    companyRecords: state.companyRecords,
    profile: state.profile,
    profileHash: state.profileHash,
    now,
  });
  await validate("candidate-memory.schema.json", memory, "rebuilt candidate memory");
  if (options.write) await writeJsonAtomic(memoryFile, memory);
  output({ command: "rebuild-memory", dry_run: !options.write, entries: Object.keys(memory.entries).length });
};

const main = async () => {
  const [command, ...rawOptions] = process.argv.slice(2);
  if (!command || command === "help" || command === "--help") { process.stdout.write(`${usage}\n`); return; }
  const options = parseArgs(rawOptions);
  const now = options.now ?? new Date().toISOString();
  if (Number.isNaN(new Date(now).valueOf())) throw new Error(`Invalid --now value: ${now}`);
  if (command === "refresh") return commandRefresh(options, now);
  if (command === "expand") return commandExpand(options, now);
  if (command === "admit") return commandAdmit(options, now);
  if (command === "sync-memory") return commandSyncMemory(options, now);
  if (command === "rebuild-memory") return commandRebuildMemory(options, now);
  throw new Error(`Unknown command: ${command}\n${usage}`);
};

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
