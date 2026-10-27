const normalized = (value) => String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

export const titlePasses = (title, profile) => {
  const value = normalized(title);
  const excluded = profile.target_roles.excluded_titles.some((item) => value.includes(normalized(item)))
    || (/\bplatform\b/.test(value) && /\bengineer\b/.test(value))
    || (/\binfrastructure\b/.test(value) && /\bengineer\b/.test(value));
  if (excluded) return false;
  const named = [...profile.target_roles.primary_titles, ...profile.target_roles.adjacent_titles]
    .some((item) => {
      const candidate = normalized(item).replace(/ building ai powered products$/, "");
      return candidate && (value === candidate || value.includes(candidate));
    });
  if (named) return true;
  if (/\bforward deployed ai engineer\b/.test(value)) return true;
  return /\bfront end\b|\bfull stack\b|\bproduct engineer\b|\bsoftware engineer product\b|\bsoftware engineer ai\b|\bforward deployed engineer\b|\bapplications engineer\b|\bapplied ai engineer\b|\bgrowth engineer\b/.test(value);
};

export const locationPasses = (locationText) => {
  const value = String(locationText ?? "").toLowerCase();
  if (!/\bsan francisco\b/.test(value)) return false;
  if (/\bsan francisco bay area\b/.test(value) && !/san francisco\s*,\s*(ca|california)/.test(value)) return false;
  return true;
};

export const aiEvidencePresent = (title, description) => {
  const titleText = normalized(title);
  if (/\b(ai|artificial intelligence|gen ai|generative ai|llm|machine learning)\b/.test(titleText)) return true;
  const text = String(description ?? "").toLowerCase();
  return /(build|building|develop|developing|ship|shipping|implement|integrat|design|creating|create)[^\n.]{0,120}\b(ai|artificial intelligence|generative ai|genai|llm|large language model|agent|copilot|machine learning|ml)\b/.test(text)
    || /\b(ai|artificial intelligence|generative ai|genai|llm|large language model|agent|copilot|machine learning|ml)[^\n.]{0,120}(feature|product|experience|workflow|application|integration)/.test(text);
};

const compensationDecision = (compensation, profile) => {
  const rule = profile.search_scope.compensation;
  if (!compensation || compensation.maximum === null) return { status: "unknown", reason: "compensation_unknown" };
  if (compensation.currency !== rule.currency) return { status: "fail", reason: "compensation_currency_mismatch" };
  if (compensation.basis !== rule.basis) return { status: "unknown", reason: "compensation_basis_unconfirmed" };
  if (compensation.period !== rule.period) return { status: "fail", reason: "compensation_period_mismatch" };
  if (rule.comparison === "published_maximum_at_least" && compensation.maximum < rule.threshold) {
    return { status: "fail", reason: "published_maximum_below_threshold" };
  }
  return { status: "pass", reason: "published_compensation_passes" };
};

export const cheapScreen = (job, profile) => {
  const fits = [];
  const concerns = [];
  const exclusions = [];
  if (titlePasses(job.title, profile)) fits.push("compatible_title_or_role_family");
  else exclusions.push("incompatible_title_screen");
  if (locationPasses(job.locationText)) fits.push("posting_identifies_san_francisco");
  else exclusions.push("posting_does_not_identify_san_francisco_city");
  if (["hybrid", "on-site"].includes(job.workplaceArrangement)) fits.push(job.workplaceArrangement.replace("-", "_"));
  else if (job.workplaceArrangement === "unknown") concerns.push("workplace_arrangement_unknown");
  else exclusions.push("incompatible_workplace_arrangement");
  if (/full[- ]?time/i.test(job.employmentType ?? "")) fits.push("full_time");
  else if (!job.employmentType) concerns.push("employment_type_unknown");
  else exclusions.push("not_full_time");
  const compensation = compensationDecision(job.compensation, profile);
  if (compensation.status === "pass") fits.push(compensation.reason);
  else if (compensation.status === "unknown") concerns.push(compensation.reason);
  else exclusions.push(compensation.reason);
  if (aiEvidencePresent(job.title, job.descriptionText)) fits.push("apparent_role_specific_ai_work");
  else concerns.push("ai_responsibility_requires_semantic_review");
  return {
    status: exclusions.length ? "excluded" : concerns.length ? "needs_review" : "plausible",
    fits,
    concerns: [...exclusions, ...concerns],
    exclusionReasons: exclusions,
  };
};

export const assertAdmissionGate = (job, evaluation, profile) => {
  const screen = cheapScreen({
    ...job,
    locationText: job.location_text,
    workplaceArrangement: job.workplace_arrangement,
    employmentType: job.employment_type,
    descriptionText: job.description_text,
  }, profile);
  const failures = [...screen.exclusionReasons];
  const compensation = compensationDecision(job.compensation, profile);
  if (compensation.status !== "pass") failures.push(compensation.reason);
  if (!/full[- ]?time/i.test(job.employment_type)) failures.push("not_full_time");
  const requiredVerificationCodes = [
    "active",
    "acceptable_seniority",
    "role_specific_ai_work",
    "all_profile_hard_constraints_passed",
  ];
  for (const code of requiredVerificationCodes) {
    if (!evaluation.reason_codes.includes(code)) failures.push(`missing_verified_${code}`);
  }
  if (failures.length) throw new Error(`Admission gate failed: ${[...new Set(failures)].join(", ")}`);
};
