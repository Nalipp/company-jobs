(() => {
  "use strict";

  const data = window.JOB_EXPLORER_DATA;
  if (!data) {
    document.body.textContent = "The results data could not be loaded.";
    return;
  }

  const companies = new Map(data.companies.map((company) => [company.company_id, company]));
  const jobs = new Map(data.jobs.map((job) => [job.stable_id, job]));

  function element(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text);
    Object.entries(options.attributes || {}).forEach(([name, value]) => {
      if (value !== null && value !== undefined) node.setAttribute(name, String(value));
    });
    children.filter(Boolean).forEach((child) => node.append(child));
    return node;
  }

  function link(label, href, className = "action-link") {
    return element("a", {
      className,
      text: label,
      attributes: { href, target: "_blank", rel: "noopener noreferrer" },
    });
  }

  function formatDate(value, style = "medium") {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return "";
    return new Intl.DateTimeFormat("en-US", {
      month: style === "short" ? "short" : "long",
      day: "numeric",
      year: "numeric",
    }).format(date);
  }

  function formatAddress(address) {
    if (!address) return "";
    const regionAndPostal = [address.region, address.postal_code].filter(Boolean).join(" ");
    const locality = [address.city, regionAndPostal].filter(Boolean).join(", ");
    return [address.street, locality, address.country && address.country !== "US" ? address.country : null]
      .filter(Boolean)
      .join(" · ");
  }

  function sentenceCase(value) {
    if (!value) return "";
    const text = String(value).replaceAll("_", " ").replaceAll("-", " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function compactLocation(job) {
    return (job.locations || []).filter(Boolean)[0] || "Location not listed";
  }

  function formatCompensation(job, compact = false) {
    const compensation = job.compensation;
    if (compensation && Number.isFinite(compensation.maximum)) {
      const currency = compensation.currency || "USD";
      const formatter = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: 0,
      });
      const maximum = formatter.format(compensation.maximum);
      if (compact) {
        const compactFormatter = new Intl.NumberFormat("en-US", {
          style: "currency",
          currency,
          notation: "compact",
          maximumFractionDigits: 0,
        });
        return `Up to ${compactFormatter.format(compensation.maximum)}`;
      }
      if (Number.isFinite(compensation.minimum)) {
        return `${formatter.format(compensation.minimum)}–${maximum} ${compensation.period === "year" ? "yearly base" : compensation.period || ""}`.trim();
      }
      return `${maximum} maximum`;
    }
    return compact ? "Matched" : (job.compensation_text || "");
  }

  function freshnessText(value) {
    const formatted = formatDate(value, "short");
    return formatted ? `Latest successful scan ${formatted}` : "Canonical results";
  }

  function renderList(container, items, itemView) {
    const fragment = document.createDocumentFragment();
    items.forEach((item, index) => {
      const view = itemView(item, index);
      const anchor = element("a", {
        className: "result-link",
        attributes: { href: view.href },
      }, [
        element("span", { className: "result-rank", text: String(index + 1).padStart(2, "0") }),
        element("span", { className: "result-main" }, [
          element("strong", { text: view.title }),
          element("small", { text: view.subtitle }),
        ]),
        element("span", { className: "count-chip", text: view.chip }),
        element("span", { className: "result-arrow", text: "→", attributes: { "aria-hidden": "true" } }),
      ]);
      fragment.append(anchor);
    });
    container.replaceChildren(fragment);
  }

  function renderFacts(container, facts) {
    const nodes = facts.filter((fact) => fact.value).map((fact) => element("div", {}, [
      element("dt", { text: fact.label }),
      element("dd", { text: fact.value }),
    ]));
    container.replaceChildren(...nodes);
    return nodes.length;
  }

  function showNotFound(message) {
    const container = document.querySelector("#not-found");
    if (!container) return;
    container.hidden = false;
    container.replaceChildren(
      element("p", { className: "eyebrow", text: "Nothing here" }),
      element("h1", { text: message }),
      element("a", { className: "action-link", text: "Browse all companies", attributes: { href: "index.html" } }),
    );
  }

  function companySubtitle(company) {
    const address = company.address;
    const location = address ? [address.city, address.region].filter(Boolean).join(", ") : "San Francisco search";
    const latest = company.sources.map((source) => source.last_scanned_at).filter(Boolean).sort().at(-1);
    return `${location || "San Francisco search"}${latest ? ` · Scanned ${formatDate(latest, "short")}` : ""}`;
  }

  function renderCompaniesPage() {
    document.querySelector("#profile-description").textContent = data.profile.description;
    document.querySelector("#company-total").textContent = data.summary.company_count.toLocaleString();
    document.querySelector("#job-total").textContent = data.summary.job_count.toLocaleString();
    document.querySelector("#header-context").textContent = `${data.summary.company_count} companies · ${data.summary.job_count} jobs`;
    document.querySelector("#data-freshness").textContent = freshnessText(data.generated_at);

    const search = document.querySelector("#company-search");
    const status = document.querySelector("#list-status");
    const empty = document.querySelector("#empty-state");
    const container = document.querySelector("#result-list");
    let sort = "matches";

    function update() {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = data.companies
        .filter((company) => company.company_name.toLocaleLowerCase().includes(query))
        .sort((a, b) => sort === "name"
          ? a.company_name.localeCompare(b.company_name)
          : b.job_count - a.job_count || a.company_name.localeCompare(b.company_name));
      renderList(container, filtered, (company) => ({
        href: `company.html?id=${encodeURIComponent(company.company_id)}`,
        title: company.company_name,
        subtitle: companySubtitle(company),
        chip: `${company.job_count} ${company.job_count === 1 ? "job" : "jobs"}`,
      }));
      status.textContent = `${filtered.length} ${filtered.length === 1 ? "company" : "companies"}`;
      empty.hidden = filtered.length !== 0;
      container.hidden = filtered.length === 0;
    }

    search.addEventListener("input", update);
    document.querySelectorAll("[data-sort]").forEach((button) => button.addEventListener("click", () => {
      sort = button.dataset.sort;
      document.querySelectorAll("[data-sort]").forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-pressed", String(active));
      });
      update();
    }));
    update();
  }

  function renderSourceDetails(company) {
    const container = document.querySelector("#source-list");
    const details = document.querySelector("#source-details");
    const sources = company.sources || [];
    document.querySelector("#source-count").textContent = `${sources.length} ${sources.length === 1 ? "source" : "sources"}`;
    if (!sources.length) {
      details.hidden = true;
      return;
    }
    container.replaceChildren(...sources.map((source) => {
      const sourceLink = source.listings_url || source.careers_url;
      const meta = [sentenceCase(source.provider), source.scope, source.last_scanned_at ? `Scanned ${formatDate(source.last_scanned_at, "short")}` : null]
        .filter(Boolean)
        .join(" · ");
      return element("div", { className: "source-row" }, [
        element("div", {}, [
          element("strong", { text: source.scope || "Company careers" }),
          element("small", { text: meta }),
        ]),
        sourceLink ? link("Open source ↗", sourceLink, "text-link") : null,
      ]);
    }));
  }

  function renderCompanyPage() {
    const id = new URLSearchParams(window.location.search).get("id");
    const company = companies.get(id);
    if (!company) {
      showNotFound("Company not found");
      return;
    }

    document.title = `${company.company_name} — V4 Daily Jobs`;
    document.querySelector("#company-name").textContent = company.company_name;
    document.querySelector("#company-location").textContent = formatAddress(company.address) || "San Francisco company";
    document.querySelector("#company-job-count").textContent = company.job_count;
    document.querySelector("#company-job-label").textContent = company.job_count === 1 ? "job" : "jobs";
    document.querySelector("#company-summary").textContent = `${company.company_name} has ${company.job_count} active ${company.job_count === 1 ? "role" : "roles"} matching the ${data.profile.profile_name} search profile.`;

    const actions = document.querySelector("#company-actions");
    if (company.website_url) actions.append(link("Visit company ↗", company.website_url));
    const careersUrl = company.sources.map((source) => source.careers_url || source.listings_url).find(Boolean);
    if (careersUrl) actions.append(link("Open careers ↗", careersUrl, "action-link secondary-action"));
    if (company.address_source_url) actions.append(link("Address evidence ↗", company.address_source_url, "action-link secondary-action"));

    const sourceNames = [...new Set(company.sources.map((source) => sentenceCase(source.provider)).filter(Boolean))].join(", ");
    const latestScan = company.sources.map((source) => source.last_scanned_at).filter(Boolean).sort().at(-1);
    const factCount = renderFacts(document.querySelector("#company-facts"), [
      { label: "Headquarters", value: formatAddress(company.address) },
      { label: "Address status", value: company.address_status === "verified" ? "Verified" : sentenceCase(company.address_status) },
      { label: "Job sources", value: sourceNames || `${company.sources.length} tracked` },
      { label: "Last job scan", value: formatDate(latestScan) },
      { label: "Company verified", value: formatDate(company.verified_date) },
    ]);
    document.querySelector("#company-facts-card").hidden = factCount === 0;

    const companyJobs = company.job_ids.map((jobId) => jobs.get(jobId)).filter(Boolean).sort((a, b) => {
      const maxA = a.compensation?.maximum || 0;
      const maxB = b.compensation?.maximum || 0;
      return maxB - maxA || a.title.localeCompare(b.title);
    });
    document.querySelector("#jobs-status").textContent = `${companyJobs.length} active`;
    renderList(document.querySelector("#result-list"), companyJobs, (job) => ({
      href: `job.html?id=${encodeURIComponent(job.stable_id)}`,
      title: job.title,
      subtitle: `${compactLocation(job)} · ${sentenceCase(job.workplace_arrangement || "workplace unspecified")}`,
      chip: formatCompensation(job, true),
    }));
    renderSourceDetails(company);
    document.querySelector("#company-page").hidden = false;
  }

  function evidenceEntries(job) {
    const entries = [];
    const semantic = job.semantic_evidence;
    if (typeof semantic === "string") entries.push({ label: "Match", value: semantic });
    else if (Array.isArray(semantic)) semantic.filter(Boolean).forEach((value) => entries.push({ label: "Match", value }));
    else if (semantic && typeof semantic === "object") {
      Object.entries(semantic).forEach(([label, value]) => {
        if (typeof value === "string" && value) entries.push({ label: sentenceCase(label), value });
      });
    }
    if (job.role_fit_evidence) entries.push({ label: "Role fit", value: job.role_fit_evidence });
    if (job.ai_involvement_evidence) entries.push({ label: "AI involvement", value: job.ai_involvement_evidence });
    if (job.review_notes) entries.push({ label: "Review notes", value: job.review_notes });
    return entries;
  }

  function renderDescription(container, description) {
    const normalized = String(description || "").replace(/\r/g, "").trim();
    if (!normalized) return 0;
    const blocks = normalized.split(/\n{2,}/).map((block) => block.replace(/\n+/g, " ").trim()).filter(Boolean);
    const paragraphs = blocks.length > 1 ? blocks : normalized.match(/.{1,900}(?:\s|$)/g)?.map((block) => block.trim()).filter(Boolean) || [normalized];
    container.replaceChildren(...paragraphs.map((paragraph) => element("p", { text: paragraph })));
    return paragraphs.length;
  }

  function renderJobPage() {
    const id = new URLSearchParams(window.location.search).get("id");
    const job = jobs.get(id);
    const company = job ? companies.get(job.company_id) : null;
    if (!job || !company) {
      showNotFound("Job not found");
      return;
    }

    document.title = `${job.title} at ${company.company_name} — V4 Daily Jobs`;
    const companyHref = `company.html?id=${encodeURIComponent(company.company_id)}`;
    const companyBack = document.querySelector("#company-back");
    companyBack.href = companyHref;
    companyBack.textContent = `← ${company.company_name}`;
    document.querySelector("#job-title").textContent = job.title;
    const companyLink = document.querySelector("#job-company");
    companyLink.href = companyHref;
    companyLink.textContent = company.company_name;

    const tags = [job.workplace_arrangement, job.employment_type, job.department || job.team].filter(Boolean);
    document.querySelector("#job-tags").replaceChildren(...tags.map((tag) => element("span", { className: "tag", text: sentenceCase(tag) })));

    const evidence = evidenceEntries(job);
    const summaryContainer = document.querySelector("#match-summary");
    if (evidence.length) {
      summaryContainer.append(element("p", { text: evidence[0].value }));
    } else {
      summaryContainer.append(element("p", { text: `This active role meets the location, role, AI involvement, and compensation requirements in the ${data.profile.profile_name} search.` }));
    }
    if (job.canonical_url) document.querySelector("#job-actions").append(link("Open official posting ↗", job.canonical_url));

    const location = (job.locations || []).filter(Boolean).join(" · ");
    const factCount = renderFacts(document.querySelector("#job-facts"), [
      { label: "Compensation", value: formatCompensation(job) },
      { label: "Location", value: location },
      { label: "Workplace", value: sentenceCase(job.workplace_arrangement) || job.workplace_text },
      { label: "Employment", value: sentenceCase(job.employment_type) || job.employment_text },
      { label: "Team", value: job.team || job.department },
      { label: "Posted", value: formatDate(job.posted_at) },
    ]);
    document.querySelector("#job-facts-card").hidden = factCount === 0;

    if (evidence.length > 1) {
      const card = document.querySelector("#evidence-card");
      const remaining = evidence.slice(1);
      document.querySelector("#evidence-list").replaceChildren(...remaining.map((entry) => element("div", { className: "evidence-row" }, [
        element("strong", { text: entry.label }),
        element("p", { text: entry.value }),
      ])));
      card.hidden = false;
    }

    if (renderDescription(document.querySelector("#job-description"), job.description_text)) {
      document.querySelector("#description-card").hidden = false;
    }
    const lastSeen = formatDate(job.last_seen_at, "short");
    const firstSeen = formatDate(job.first_seen_at, "short");
    document.querySelector("#job-freshness").textContent = [
      lastSeen ? `Last confirmed ${lastSeen}` : null,
      firstSeen ? `First found ${firstSeen}` : null,
    ].filter(Boolean).join(" · ");
    document.querySelector("#job-page").hidden = false;
  }

  const page = document.body.dataset.page;
  if (page === "companies") renderCompaniesPage();
  if (page === "company") renderCompanyPage();
  if (page === "job") renderJobPage();
})();
