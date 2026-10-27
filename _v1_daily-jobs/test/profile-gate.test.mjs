import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { aiEvidencePresent, cheapScreen, locationPasses, titlePasses } from "../scripts/lib/profile-gate.mjs";

const profile = JSON.parse(await readFile(new URL("../profiles/default.json", import.meta.url), "utf8"));

const job = {
  title: "Software Engineer, Product",
  locationText: "San Francisco, CA",
  workplaceArrangement: "hybrid",
  employmentType: "full-time",
  compensation: {
    minimum: 180000,
    maximum: 220000,
    currency: "USD",
    basis: "base",
    period: "year",
    source_text: "$180,000-$220,000 base",
  },
  descriptionText: "Build AI-powered product workflows with designers.",
};

test("approved title exceptions remain narrow", () => {
  assert.equal(titlePasses("Frontend Engineer", profile), true);
  assert.equal(titlePasses("Forward Deployed Engineer", profile), true);
  assert.equal(titlePasses("Forward Deployed AI Engineer", profile), true);
  assert.equal(titlePasses("Software Engineer, Product", profile), true);
  assert.equal(titlePasses("Software Engineer", profile), false);
  assert.equal(titlePasses("Platform Engineer", profile), false);
  assert.equal(titlePasses("Product Engineer, Platform", profile), false);
});

test("San Francisco city passes while broad Bay Area wording does not", () => {
  assert.equal(locationPasses("San Francisco, CA"), true);
  assert.equal(locationPasses("San Francisco Bay Area"), false);
  assert.equal(locationPasses("Oakland, CA"), false);
});

test("AI employer language alone is not role responsibility evidence", () => {
  assert.equal(aiEvidencePresent("Frontend Engineer", "We are an AI company."), false);
  assert.equal(aiEvidencePresent("Frontend Engineer", "Build LLM product experiences for customers."), true);
});

test("cheap screen separates hard exclusions from reviewable unknowns", () => {
  assert.equal(cheapScreen(job, profile).status, "plausible");
  assert.equal(cheapScreen({ ...job, compensation: null }, profile).status, "needs_review");
  assert.equal(cheapScreen({ ...job, locationText: "Oakland, CA" }, profile).status, "excluded");
});
