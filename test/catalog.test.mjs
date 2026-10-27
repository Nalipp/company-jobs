import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { planAdmission } from "../scripts/lib/catalog.mjs";
import { sha256 } from "../scripts/lib/core.mjs";

const profileText = await readFile(new URL("../profiles/default.json", import.meta.url), "utf8");
const profile = JSON.parse(profileText);
const profileHash = sha256(profileText);
const admission = JSON.parse(await readFile(new URL("../examples/job-admission.example.json", import.meta.url), "utf8"));
const now = "2026-08-05T20:00:00Z";

test("admission creates a company with full job memory and unresolved address", () => {
  const result = planAdmission({ existingCompany: null, admission, profile, profileHash, now });
  assert.equal(result.created, true);
  assert.equal(result.company.offices[0].address_verified, null);
  assert.equal(result.company.jobs[result.jobId].raw.description_completeness, "full");
  assert.equal(result.company.jobs[result.jobId].evaluations[0].profile_hash, profileHash);
  assert.match(result.company.jobs[result.jobId].hashes.content_hash, /^[a-f0-9]{64}$/);
});

test("admission refuses jobs below the compensation threshold", () => {
  const low = structuredClone(admission);
  low.job.compensation.maximum = 199999;
  assert.throws(
    () => planAdmission({ existingCompany: null, admission: low, profile, profileHash, now }),
    /Admission gate failed/,
  );
});

test("admission requires verified role-specific AI evidence", () => {
  const missingEvidence = structuredClone(admission);
  missingEvidence.evaluation.reason_codes = missingEvidence.evaluation.reason_codes.filter((code) => code !== "role_specific_ai_work");
  assert.throws(
    () => planAdmission({ existingCompany: null, admission: missingEvidence, profile, profileHash, now }),
    /missing_verified_role_specific_ai_work/,
  );
});

test("admission requires an explicit acceptable-seniority decision", () => {
  const missingSeniority = structuredClone(admission);
  missingSeniority.evaluation.reason_codes = missingSeniority.evaluation.reason_codes.filter((code) => code !== "acceptable_seniority");
  assert.throws(
    () => planAdmission({ existingCompany: null, admission: missingSeniority, profile, profileHash, now }),
    /missing_verified_acceptable_seniority/,
  );
});
