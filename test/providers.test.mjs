import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  collectSource,
  normalizeAshbyJob,
  normalizeGreenhouseJob,
  normalizeLeverJob,
  requestForSource,
} from "../scripts/lib/providers.mjs";

const fixture = async (name) => JSON.parse(await readFile(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));

test("Ashby normalization uses structured salary and filters unlisted jobs", async () => {
  const data = await fixture("ashby-response.json");
  const source = { provider: "ashby", board_id: "fixture", url: "https://jobs.ashbyhq.com/fixture" };
  const job = normalizeAshbyJob(data.jobs[0], source);
  assert.equal(job.workplaceArrangement, "hybrid");
  assert.equal(job.employmentType, "full-time");
  assert.deepEqual(job.compensation, {
    minimum: 180000,
    maximum: 225000,
    currency: "USD",
    basis: "base",
    period: "year",
    source_text: "$180K - $225K",
  });
  const collected = await collectSource(source, {
    fetchImpl: async () => ({ ok: true, json: async () => data }),
  });
  assert.equal(collected.jobs.length, 3);
  assert.equal(collected.complete, true);
  assert.match(collected.requestUrl, /includeCompensation=true/);
});

test("Greenhouse normalization converts HTML and cents", async () => {
  const data = await fixture("greenhouse-response.json");
  const job = normalizeGreenhouseJob(data.jobs[0], {
    provider: "greenhouse",
    board_id: "fixture",
    url: "https://job-boards.greenhouse.io/fixture",
  });
  assert.equal(job.workplaceArrangement, "hybrid");
  assert.equal(job.employmentType, "full-time");
  assert.equal(job.compensation.maximum, 220000);
  assert.equal(job.compensation.basis, "base");
  assert.match(job.descriptionText, /Build AI-powered product experiences/);
});

test("Lever normalization retains full text and base salary", async () => {
  const data = await fixture("lever-response.json");
  const source = { provider: "lever", board_id: "fixture", url: "https://jobs.lever.co/fixture" };
  const job = normalizeLeverJob(data[0], source);
  assert.equal(job.workplaceArrangement, "hybrid");
  assert.equal(job.employmentType, "full-time");
  assert.equal(job.compensation.period, "year");
  assert.equal(job.compensation.basis, "base");
  assert.equal(requestForSource(source), "https://api.lever.co/v0/postings/fixture?mode=json");
});

test("unsupported providers fail before making a request", () => {
  assert.throws(() => requestForSource({ provider: "custom", board_id: null }), /Unsupported provider/);
});

