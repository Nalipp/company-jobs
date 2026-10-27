import {
  htmlToText,
  normalizeEmployment,
  normalizeText,
  normalizeUrl,
  normalizeWorkplace,
  toDateOnly,
  unique,
} from "./core.mjs";

export const supportedProviders = new Set(["ashby", "greenhouse", "lever"]);

const normalizePeriod = (value) => {
  const text = String(value ?? "").toLowerCase();
  if (/year|annual|12 month/.test(text)) return "year";
  if (/month/.test(text)) return "month";
  if (/hour/.test(text)) return "hour";
  return "unknown";
};

const ashbyCompensation = (compensation) => {
  if (!compensation) return null;
  const components = [
    ...(compensation.summaryComponents ?? []),
    ...(compensation.compensationTiers ?? []).flatMap((tier) => tier.components ?? []),
  ];
  const salary = components.find((component) => String(component.compensationType).toLowerCase() === "salary");
  const sourceText = compensation.scrapeableCompensationSalarySummary
    ?? salary?.summary
    ?? compensation.compensationTierSummary
    ?? null;
  if (!salary && !sourceText) return null;
  return {
    minimum: salary?.minValue ?? null,
    maximum: salary?.maxValue ?? null,
    currency: salary?.currencyCode ?? (/\$/.test(sourceText ?? "") ? "USD" : null),
    basis: "base",
    period: normalizePeriod(salary?.interval ?? sourceText),
    source_text: sourceText,
  };
};

const greenhouseCompensation = (job) => {
  const ranges = job.pay_input_ranges ?? [];
  const usd = ranges.find((range) => range.currency_type === "USD") ?? ranges[0];
  if (!usd) return null;
  return {
    minimum: typeof usd.min_cents === "number" ? usd.min_cents / 100 : null,
    maximum: typeof usd.max_cents === "number" ? usd.max_cents / 100 : null,
    currency: usd.currency_type ?? null,
    basis: /base/i.test(`${usd.title ?? ""} ${usd.blurb ?? ""}`) ? "base" : "unknown",
    period: /hour/i.test(`${usd.title ?? ""} ${usd.blurb ?? ""}`) ? "hour" : "year",
    source_text: normalizeText(`${usd.title ?? ""} ${htmlToText(usd.blurb ?? "")}`) || null,
  };
};

const leverCompensation = (job) => {
  if (!job.salaryRange) return null;
  const description = normalizeText(job.salaryDescriptionPlain ?? htmlToText(job.salaryDescription ?? ""));
  return {
    minimum: job.salaryRange.min ?? null,
    maximum: job.salaryRange.max ?? null,
    currency: job.salaryRange.currency ?? null,
    basis: /base/i.test(description) ? "base" : "unknown",
    period: normalizePeriod(job.salaryRange.interval),
    source_text: description || `${job.salaryRange.min ?? "?"}-${job.salaryRange.max ?? "?"} ${job.salaryRange.currency ?? ""} ${job.salaryRange.interval ?? ""}`.trim(),
  };
};

const ashbyDescription = (job) => normalizeText(job.descriptionPlain ?? htmlToText(job.descriptionHtml ?? ""));

const leverDescription = (job) => normalizeText(job.descriptionPlain
  ?? [job.openingPlain, job.descriptionBodyPlain, job.additionalPlain].filter(Boolean).join("\n\n")
  ?? htmlToText(job.description ?? ""));

const greenhouseDescription = (job) => htmlToText(job.content ?? "");

export const normalizeAshbyJob = (job, source) => {
  const locations = unique([job.location, ...(job.secondaryLocations ?? []).map((location) => location.location)]);
  const description = ashbyDescription(job);
  return {
    provider: "ashby",
    boardId: source.board_id,
    sourceJobId: String(job.id),
    canonicalUrl: normalizeUrl(job.jobUrl ?? job.applyUrl),
    applyUrl: normalizeUrl(job.applyUrl ?? job.jobUrl),
    title: normalizeText(job.title),
    locationText: locations.join(" | ") || null,
    workplaceArrangement: normalizeWorkplace(job.workplaceType, `${job.isRemote ? "remote" : ""} ${locations.join(" ")}`),
    employmentType: normalizeEmployment(job.employmentType, description.slice(0, 1000)),
    seniority: null,
    compensation: ashbyCompensation(job.compensation),
    postedDate: toDateOnly(job.publishedAt),
    descriptionText: description || null,
    descriptionCompleteness: description ? "full" : "none",
  };
};

export const normalizeGreenhouseJob = (job, source) => {
  const description = greenhouseDescription(job);
  const locations = unique([job.location?.name, ...(job.offices ?? []).map((office) => office.location || office.name)]);
  return {
    provider: "greenhouse",
    boardId: source.board_id,
    sourceJobId: String(job.id),
    canonicalUrl: normalizeUrl(job.absolute_url),
    applyUrl: normalizeUrl(job.absolute_url),
    title: normalizeText(job.title),
    locationText: locations.join(" | ") || null,
    workplaceArrangement: normalizeWorkplace(null, `${locations.join(" ")} ${description.slice(0, 1500)}`),
    employmentType: normalizeEmployment(null, description.slice(0, 1500)),
    seniority: null,
    compensation: greenhouseCompensation(job),
    postedDate: toDateOnly(job.updated_at),
    descriptionText: description || null,
    descriptionCompleteness: description ? "full" : "none",
  };
};

export const normalizeLeverJob = (job, source) => {
  const description = leverDescription(job);
  const locations = unique([job.categories?.location, ...(job.categories?.allLocations ?? [])]);
  return {
    provider: "lever",
    boardId: source.board_id,
    sourceJobId: String(job.id),
    canonicalUrl: normalizeUrl(job.hostedUrl ?? job.applyUrl),
    applyUrl: normalizeUrl(job.applyUrl ?? job.hostedUrl),
    title: normalizeText(job.text),
    locationText: locations.join(" | ") || null,
    workplaceArrangement: normalizeWorkplace(job.workplaceType, locations.join(" ")),
    employmentType: normalizeEmployment(job.categories?.commitment, description.slice(0, 1000)),
    seniority: job.categories?.level ?? null,
    compensation: leverCompensation(job),
    postedDate: null,
    descriptionText: description || null,
    descriptionCompleteness: description ? "full" : "none",
  };
};

export const requestForSource = (source) => {
  if (!supportedProviders.has(source.provider)) {
    throw new Error(`Unsupported provider: ${source.provider}`);
  }
  if (!source.board_id) throw new Error(`Missing board_id for ${source.provider} source`);
  const board = encodeURIComponent(source.board_id);
  if (source.provider === "ashby") {
    return `https://api.ashbyhq.com/posting-api/job-board/${board}?includeCompensation=true`;
  }
  if (source.provider === "greenhouse") {
    return `https://boards-api.greenhouse.io/v1/boards/${board}/jobs?content=true`;
  }
  const eu = /jobs\.eu\.lever\.co/i.test(source.url);
  return `https://${eu ? "api.eu.lever.co" : "api.lever.co"}/v0/postings/${board}?mode=json`;
};

export const collectSource = async (source, { fetchImpl = globalThis.fetch } = {}) => {
  const requestUrl = requestForSource(source);
  const response = await fetchImpl(requestUrl, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${source.provider} returned HTTP ${response.status}`);
  const data = await response.json();
  const rawJobs = source.provider === "lever" ? data : data.jobs;
  if (!Array.isArray(rawJobs)) throw new Error(`${source.provider} response did not contain a jobs array`);
  const normalizer = {
    ashby: normalizeAshbyJob,
    greenhouse: normalizeGreenhouseJob,
    lever: normalizeLeverJob,
  }[source.provider];
  const jobs = rawJobs
    .filter((job) => source.provider !== "ashby" || job.isListed !== false)
    .map((job) => normalizer(job, source))
    .filter((job) => job.canonicalUrl && job.title);
  return { requestUrl, jobs, complete: true };
};

