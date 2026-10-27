(() => {
  "use strict";

  const snapshot = JSON.parse(document.getElementById("jobs-data").textContent);
  const companies = snapshot.companies;
  const JEV_SF_FEATURE = "job.location.san_francisco";
  const JEV_FILTERS = Array.isArray(snapshot.jevFilters)
    ? snapshot.jevFilters
    : [{ key: JEV_SF_FEATURE, label: "San Francisco", threshold: 0.8, default: true }];
  const JEV_SF_FILTER = JEV_FILTERS.find(filter => filter.key === JEV_SF_FEATURE);
  const JEV_SF_THRESHOLD = Number(JEV_SF_FILTER?.threshold ?? 0.8);
  const DEFAULT_JEV_FILTER_KEYS = new Set(
    JEV_FILTERS.filter(filter => filter.default).map(filter => filter.key)
  );
  const listCompanies = companies.filter(company => company.activeJobCount > 0);
  const mapCompanies = listCompanies;
  const byId = new Map(listCompanies.map(company => [company.id, company]));
  const countFormat = new Intl.NumberFormat();
  const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
  const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const state = {
    query: "",
    detailId: null,
    highlightedId: null,
    sort: "name",
    direction: 1,
    mapVisibleIds: null,
    listScrollTop: 0,
    jevFilterKeys: new Set(DEFAULT_JEV_FILTER_KEYS),
    detailFilters: { category: new Set(), workplace: new Set(), location: new Set() }
  };

  const search = document.getElementById("search");
  const list = document.getElementById("companies");
  const browser = document.getElementById("company-browser");
  const detail = document.getElementById("company-detail");
  const panel = document.getElementById("company-list");
  const summary = document.getElementById("summary");
  const jevFilters = document.getElementById("jev-filters");
  const mapError = document.getElementById("map-error");
  const markerButtons = new Map();
  const listButtons = new Map();
  const globalFilterButtons = new Map();
  const searchIndexes = new Map(listCompanies.map(company => [company.id, {
    company: [
      company.name,
      company.street,
      company.workplace,
      company.websiteUrl,
      ...company.careerSources.flatMap(source => [source.provider, source.careersUrl, source.listingsUrl])
    ].join(" ").toLowerCase(),
    jobs: company.jobs.map(job => [
      job.title,
      job.category,
      job.compensation || "",
      job.employment || "",
      job.workplace || "",
      ...(job.locations || [])
    ].join(" ").toLowerCase())
  }]));

  let companyMap = null;
  let activePopup = null;
  let popupCloseTimer = null;

  function selectedJevFilters() {
    return JEV_FILTERS.filter(filter => state.jevFilterKeys.has(filter.key));
  }

  function jobPassesJevFilter(job, filter) {
    const probability = Number(job.jev?.[filter.key]);
    if (!Number.isFinite(probability)) return filter.mode === "exclude";
    return filter.mode === "exclude"
      ? probability < Number(filter.threshold)
      : probability >= Number(filter.threshold);
  }

  function jobMatchesGlobalFilters(job) {
    const filterGroups = new Map();
    for (const filter of selectedJevFilters()) {
      const group = filter.matchGroup || filter.key;
      if (!filterGroups.has(group)) filterGroups.set(group, []);
      filterGroups.get(group).push(filter);
    }
    return [...filterGroups.values()].every(group =>
      group.some(filter => jobPassesJevFilter(job, filter))
    );
  }

  function matchingJobs(company) {
    return company.jobs.filter(jobMatchesGlobalFilters);
  }

  function matchingJobCount(company) {
    return matchingJobs(company).length;
  }

  function activeFilterDescription() {
    const labels = selectedJevFilters().map(filter => filter.label);
    return labels.length ? labels.join(" + ") : "all jobs";
  }

  function companyPassesGlobalFilters(company) {
    return matchingJobCount(company) > 0;
  }

  function renderGlobalFilters() {
    const fragment = document.createDocumentFragment();
    globalFilterButtons.clear();
    for (const filter of JEV_FILTERS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "global-filter";
      button.dataset.featureKey = filter.key;
      button.setAttribute("aria-pressed", "false");
      button.append(
        makeSpan("global-filter-label", filter.label),
        makeSpan("global-filter-count", `(${countFormat.format(filter.count || 0)})`)
      );
      const action = filter.mode === "exclude" ? "Exclude jobs flagged for" : "Require";
      button.title = `${action} ${filter.label} at a Jev probability of at least ${Math.round(Number(filter.threshold) * 100)}%.`;
      button.addEventListener("click", () => {
        if (state.jevFilterKeys.has(filter.key)) state.jevFilterKeys.delete(filter.key);
        else state.jevFilterKeys.add(filter.key);
        state.mapVisibleIds = null;
        if (state.detailId) closeCompanyDetail();
        syncGlobalFilters();
        renderList();
        panel.scrollTop = 0;
        fitCompanies();
      });
      globalFilterButtons.set(filter.key, button);
      fragment.append(button);
    }
    jevFilters.replaceChildren(fragment);
    syncGlobalFilters();
  }

  function syncGlobalFilters() {
    for (const filter of JEV_FILTERS) {
      const button = globalFilterButtons.get(filter.key);
      if (!button) continue;
      const selected = state.jevFilterKeys.has(filter.key);
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    for (const company of mapCompanies) {
      const button = markerButtons.get(company.id);
      if (!button) continue;
      const count = matchingJobCount(company);
      button.hidden = count === 0;
      button.textContent = String(count);
      button.title = `${company.name} · ${count} matching jobs (${activeFilterDescription()}) · ${company.activeJobCount} active jobs company-wide · ${company.street}\nVerified company office; individual jobs may have other workplaces.`;
      button.setAttribute("aria-label", `Open ${company.name}, ${count} matching jobs`);
    }
  }

  function safeUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return null;
    try {
      const candidate = /^[a-z][a-z0-9+.-]*:/i.test(raw) ? raw : `https://${raw}`;
      const url = new URL(candidate);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  }

  function parseDate(value) {
    if (value === null || value === undefined || value === "") return null;
    const raw = String(value);
    const milliseconds = /^\d+(\.\d+)?$/.test(raw)
      ? (Number(raw) < 100000000000 ? Number(raw) * 1000 : Number(raw))
      : Date.parse(raw);
    return Number.isFinite(milliseconds) ? new Date(milliseconds) : null;
  }

  function formatDate(value) {
    const date = parseDate(value);
    return date ? dateFormat.format(date) : "Not supplied";
  }

  function formatDateTime(value) {
    const date = parseDate(value);
    return date ? dateTimeFormat.format(date) : "Not supplied";
  }

  function formatAddress(company) {
    const address = company.address || {};
    return [address.street, address.city, address.region, address.postalCode]
      .filter(Boolean)
      .join(", ") || "Address not supplied";
  }

  function providerLabel(value) {
    const raw = String(value || "").trim();
    return raw ? raw.replace(/(^|[-_\s])\w/g, match => match.toUpperCase()).replace(/[-_]/g, " ") : "Not supplied";
  }

  function visibleCompanies() {
    const terms = state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const result = [];
    for (const company of listCompanies) {
      if (!companyPassesGlobalFilters(company)) continue;
      if (state.mapVisibleIds && !state.mapVisibleIds.has(company.id)) continue;
      const index = searchIndexes.get(company.id);
      if (terms.length) {
        const companyMatches = terms.every(term => index.company.includes(term));
        const jobMatches = companyMatches || index.jobs.some(job => terms.every(term => `${index.company} ${job}`.includes(term)));
        if (!jobMatches) continue;
      }
      result.push(company);
    }
    return result.sort((a, b) => {
      const left = a[state.sort] || "";
      const right = b[state.sort] || "";
      return state.direction * collator.compare(left, right)
        || collator.compare(a.name, b.name)
        || collator.compare(a.id, b.id);
    });
  }

  function makeSpan(className, text) {
    const span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  function syncMarkerStates() {
    for (const [id, button] of markerButtons) {
      const selected = id === state.detailId;
      const highlighted = selected || id === state.highlightedId;
      button.classList.toggle("is-highlighted", highlighted);
      button.setAttribute("aria-pressed", String(selected));
    }
    for (const [id, button] of listButtons) {
      button.classList.toggle("is-highlighted", id === state.highlightedId);
    }
  }

  function highlightCompany(id, showPopup = true) {
    if (!byId.has(id)) return;
    state.highlightedId = id;
    syncMarkerStates();
    if (showPopup && markerButtons.has(id)) openCompanyPopup(byId.get(id));
  }

  function clearCompanyHighlight(id) {
    if (state.highlightedId !== id || state.detailId === id) return;
    state.highlightedId = null;
    syncMarkerStates();
    scheduleClosePopup();
  }

  function renderList() {
    const visible = visibleCompanies();
    const fragment = document.createDocumentFragment();
    listButtons.clear();
    for (const company of visible) {
      const matchedJobs = matchingJobCount(company);
      const article = document.createElement("article");
      article.className = "company";
      article.dataset.companyId = company.id;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "company-row";
      button.setAttribute("aria-label", `Open ${company.name}, ${matchedJobs} matching jobs`);
      const name = makeSpan("company-name", "");
      const chevron = makeSpan("chevron", "›");
      chevron.setAttribute("aria-hidden", "true");
      const label = makeSpan("name-label", company.name);
      label.title = company.name;
      const count = makeSpan(
        "company-count",
        `${countFormat.format(matchedJobs)} match · ${countFormat.format(company.activeJobCount)} active`
      );
      count.title = `${matchedJobs} jobs match the selected Jev filters: ${activeFilterDescription()}.`;
      name.append(chevron, label, count);
      const address = makeSpan("company-address", company.street || "Street not supplied");
      address.title = formatAddress(company);
      const workplace = makeSpan("company-workplace", company.workplace);
      workplace.title = "Most common workplace arrangement across active postings";
      button.append(name, address, workplace);
      button.addEventListener("click", () => openCompanyDetail(company.id));
      button.addEventListener("mouseenter", () => highlightCompany(company.id));
      button.addEventListener("mouseleave", () => clearCompanyHighlight(company.id));
      button.addEventListener("focus", () => highlightCompany(company.id));
      button.addEventListener("blur", () => clearCompanyHighlight(company.id));
      listButtons.set(company.id, button);
      article.append(button);
      fragment.append(article);
    }
    list.replaceChildren(fragment);
    document.getElementById("empty").hidden = visible.length > 0;
    const jobs = visible.reduce((total, company) => total + matchingJobCount(company), 0);
    summary.textContent = `${countFormat.format(visible.length)} ${visible.length === 1 ? "company" : "companies"} · ${countFormat.format(jobs)} matching jobs`;
    summary.title = `Jobs matching: ${activeFilterDescription()}.`;
    syncGlobalFilters();
    for (const button of document.querySelectorAll("[data-sort]")) {
      const active = button.dataset.sort === state.sort;
      button.querySelector(".sort-arrow").textContent = active ? state.direction === 1 ? "↑" : "↓" : "";
      button.setAttribute("aria-label", `Sort by ${button.dataset.sort === "name" ? "company" : button.dataset.sort}${active ? state.direction === 1 ? ", ascending" : ", descending" : ""}`);
    }
    syncMarkerStates();
  }

  function externalLink(label, value, className = "") {
    const url = safeUrl(value);
    if (!url) return null;
    const link = document.createElement("a");
    link.className = className;
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = label;
    return link;
  }

  function workplaceSummary(company) {
    return ["Hybrid", "On-site", "Remote", "Not listed"]
      .filter(label => company.workplaceMix[label])
      .map(label => `${countFormat.format(company.workplaceMix[label])} ${label.toLowerCase()}`)
      .join(" · ") || "Not listed";
  }

  function isSanFranciscoLocation(value) {
    return /san francisco/i.test(String(value || "")) || /\bsf(?:,?\s*ca)?\b/i.test(String(value || ""));
  }

  function setDefaultDetailFilters() {
    state.detailFilters = { category: new Set(), workplace: new Set(), location: new Set() };
  }

  function infoItem(label, value, url = null) {
    const item = document.createElement("div");
    item.className = "detail-info-item";
    const valueNode = url
      ? externalLink(`${value} ↗`, url, "detail-info-value detail-info-link")
      : makeSpan("detail-info-value", value);
    item.append(makeSpan("detail-info-label", label), valueNode || makeSpan("detail-info-value", value));
    return item;
  }

  function selectedFilterCount() {
    return Object.values(state.detailFilters).reduce((total, values) => total + values.size, 0);
  }

  function jobMatchesFilters(job) {
    const filters = state.detailFilters;
    return jobMatchesGlobalFilters(job)
      && (!filters.category.size || filters.category.has(job.category))
      && (!filters.workplace.size || filters.workplace.has(job.workplace))
      && (!filters.location.size || [...filters.location].some(location =>
        location === "San Francisco"
          ? Number(job.jev?.[JEV_SF_FEATURE]) >= JEV_SF_THRESHOLD
          : (job.locations || []).includes(location)
      ));
  }

  function syncFilterControls() {
    for (const button of detail.querySelectorAll(".filter-chip")) {
      const selected = state.detailFilters[button.dataset.filterGroup].has(button.dataset.filterValue);
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    const reset = detail.querySelector(".filter-reset");
    if (reset) {
      const count = selectedFilterCount();
      reset.disabled = count === 0;
      reset.textContent = count ? `Reset filters (${count})` : "Reset filters";
    }
  }

  function renderCompanyJobs(company) {
    const current = detail.querySelector(".detail-jobs");
    const replacement = renderJobTable(company);
    if (current) current.replaceWith(replacement);
    syncFilterControls();
  }

  function filterChip(company, group, label, count) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter-chip";
    button.dataset.filterGroup = group;
    button.dataset.filterValue = label;
    button.setAttribute("aria-pressed", "false");
    button.append(makeSpan("filter-chip-label", label), makeSpan("filter-chip-count", countFormat.format(count)));
    button.addEventListener("click", () => {
      const values = state.detailFilters[group];
      if (values.has(label)) values.delete(label);
      else values.add(label);
      renderCompanyJobs(company);
    });
    return button;
  }

  function filterGroup(company, title, group, values) {
    const container = document.createElement("div");
    container.className = "filter-group";
    container.append(makeSpan("filter-group-label", title));
    for (const [label, count] of values) container.append(filterChip(company, group, label, count));
    return container;
  }

  function renderCompanyInfo(company) {
    const container = document.createElement("section");
    container.className = "detail-info";
    container.setAttribute("aria-label", "Company information and job filters");
    const sources = company.careerSources;
    const primarySource = sources[0] || {};
    const listingsUrl = primarySource.listingsUrl || primarySource.careersUrl;
    const careersUrl = primarySource.careersUrl || primarySource.listingsUrl;
    const provider = [...new Set(sources.map(source => providerLabel(source.provider)))].join(", ") || "Not supplied";
    const matchedJobs = matchingJobCount(company);
    const facts = document.createElement("div");
    facts.className = "detail-info-grid";
    facts.append(
      infoItem("Matching jobs", countFormat.format(matchedJobs), listingsUrl),
      infoItem("Active jobs", countFormat.format(company.activeJobCount)),
      infoItem("Workplace mix", workplaceSummary(company)),
      infoItem("Last collection", formatDateTime(company.lastCollectedAt)),
      infoItem("Career source", provider, careersUrl)
    );

    const filters = document.createElement("div");
    filters.className = "detail-filters";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "filter-reset";
    reset.textContent = "Reset filters";
    reset.disabled = true;
    reset.addEventListener("click", () => {
      state.detailFilters = { category: new Set(), workplace: new Set(), location: new Set() };
      renderCompanyJobs(company);
    });
    const scroller = document.createElement("div");
    scroller.className = "filter-scroller";
    const categoryValues = Object.entries(company.categoryCounts)
      .sort((a, b) => b[1] - a[1] || collator.compare(a[0], b[0]));
    const workplaceValues = ["Hybrid", "On-site", "Remote", "Not listed"]
      .filter(label => company.workplaceMix[label])
      .map(label => [label, company.workplaceMix[label]]);
    const locationValues = [];
    const sfMatches = company.jobs.filter(job => Number(job.jev?.[JEV_SF_FEATURE]) >= JEV_SF_THRESHOLD).length;
    if (sfMatches) locationValues.push(["San Francisco", sfMatches]);
    for (const location of company.topPostingLocations) {
      if (!isSanFranciscoLocation(location.name)) locationValues.push([location.name, location.jobs]);
    }
    if (categoryValues.length) scroller.append(filterGroup(company, "Role", "category", categoryValues));
    if (workplaceValues.length) scroller.append(filterGroup(company, "Workplace", "workplace", workplaceValues));
    if (locationValues.length) scroller.append(filterGroup(company, "Location", "location", locationValues));
    filters.append(reset, scroller);
    container.append(facts, filters);
    return container;
  }

  function renderJobTable(company) {
    const section = document.createElement("section");
    section.className = "detail-jobs";
    const heading = document.createElement("div");
    heading.className = "jobs-heading";
    const h3 = document.createElement("h3");
    h3.textContent = "Active jobs";
    const note = document.createElement("p");
    note.className = "jobs-count";
    const jobs = company.jobs
      .filter(jobMatchesFilters)
      .sort((a, b) => collator.compare(a.title, b.title) || collator.compare(a.id, b.id));
    const globalMatchCount = matchingJobCount(company);
    note.textContent = selectedFilterCount()
      ? `${countFormat.format(jobs.length)} of ${countFormat.format(globalMatchCount)} matching postings`
      : `${countFormat.format(globalMatchCount)} matching postings`;
    heading.append(h3, note);
    const wrap = document.createElement("div");
    wrap.className = "detail-job-table-wrap";
    if (!jobs.length) {
      const empty = document.createElement("p");
      empty.className = "jobs-empty";
      empty.textContent = "No jobs match the selected filters.";
      wrap.append(empty);
      section.append(heading, wrap);
      return section;
    }
    const table = document.createElement("table");
    table.className = "detail-job-table";
    table.setAttribute("aria-label", `${company.name} active jobs`);
    const cols = document.createElement("colgroup");
    for (let index = 0; index < 7; index += 1) cols.append(document.createElement("col"));
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    for (const label of ["Job title", "Posting location", "Workplace", "Employment", "Compensation", "Jev SF", "Collected"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.append(th);
    }
    head.append(header);
    const body = document.createElement("tbody");
    for (const job of jobs) {
      const row = document.createElement("tr");
      row.dataset.jobId = job.id;
      const title = document.createElement("td");
      const jobUrl = safeUrl(job.url);
      if (jobUrl) {
        const link = document.createElement("a");
        link.href = jobUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = job.title;
        link.title = job.title;
        title.append(link);
      } else title.textContent = job.title;
      const location = document.createElement("td");
      location.textContent = (job.locations || []).join("; ") || "Not listed";
      location.title = location.textContent;
      const workplace = document.createElement("td");
      workplace.textContent = job.workplace || "Not listed";
      const employment = document.createElement("td");
      employment.textContent = job.employment || "Not listed";
      employment.title = employment.textContent;
      const compensation = document.createElement("td");
      compensation.textContent = job.compensation || "Not listed";
      compensation.title = compensation.textContent;
      const jev = document.createElement("td");
      const jevProbability = Number(job.jev?.[JEV_SF_FEATURE]);
      jev.textContent = Number.isFinite(jevProbability)
        ? `${Math.round(jevProbability * 100)}%`
        : "Not evaluated";
      jev.title = Number.isFinite(jevProbability)
        ? `Jev probability that this posting is located in San Francisco: ${jev.textContent}`
        : "No Jev result is available for this job";
      const collected = document.createElement("td");
      collected.textContent = formatDate(job.lastSeen);
      collected.title = formatDateTime(job.lastSeen);
      row.append(title, location, workplace, employment, compensation, jev, collected);
      body.append(row);
    }
    table.append(cols, head, body);
    wrap.append(table);
    section.append(heading, wrap);
    return section;
  }

  function renderCompanyDetail(company) {
    const header = document.createElement("header");
    header.className = "detail-header";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "detail-close";
    const closeIcon = document.createElement("span");
    closeIcon.setAttribute("aria-hidden", "true");
    closeIcon.textContent = "×";
    close.append(closeIcon, makeSpan("sr-only", "Close company details"));
    close.addEventListener("click", closeCompanyDetail);
    const heading = document.createElement("div");
    heading.className = "detail-heading";
    const eyebrow = document.createElement("p");
    eyebrow.className = "detail-eyebrow";
    eyebrow.textContent = "Verified San Francisco company";
    const title = document.createElement("h2");
    title.className = "detail-title";
    const website = externalLink(`${company.name} ↗`, company.websiteUrl, "detail-title-link");
    if (website) title.append(website);
    else title.textContent = company.name;
    const address = document.createElement("p");
    address.className = "detail-address";
    address.textContent = formatAddress(company);
    const keyboardHint = document.createElement("p");
    keyboardHint.className = "detail-keyboard-hint";
    keyboardHint.textContent = "↑ / ↓ change company";
    heading.append(eyebrow, title, address);
    header.append(close, heading, keyboardHint);
    detail.replaceChildren(header, renderCompanyInfo(company), renderJobTable(company));
    syncFilterControls();
    requestAnimationFrame(() => close.focus());
  }

  function showCompanyDetail(id) {
    const company = byId.get(id);
    if (!company) return;
    state.detailId = id;
    state.highlightedId = id;
    setDefaultDetailFilters(company);
    browser.hidden = true;
    detail.hidden = false;
    panel.classList.add("showing-detail");
    renderCompanyDetail(company);
    panel.scrollTop = 0;
    syncMarkerStates();
    if (markerButtons.has(id)) openCompanyPopup(company);
    else closeCompanyPopup(true);
  }

  function openCompanyDetail(id) {
    const company = byId.get(id);
    if (!company) return;
    state.listScrollTop = panel.scrollTop;
    showCompanyDetail(id);
  }

  function navigateCompanyDetail(direction) {
    if (!state.detailId) return;
    const ordered = visibleCompanies();
    const current = byId.get(state.detailId);
    if (current && !ordered.some(company => company.id === current.id)) {
      ordered.push(current);
      ordered.sort((a, b) => {
        const left = a[state.sort] || "";
        const right = b[state.sort] || "";
        return state.direction * collator.compare(left, right)
          || collator.compare(a.name, b.name)
          || collator.compare(a.id, b.id);
      });
    }
    const index = ordered.findIndex(company => company.id === state.detailId);
    const next = ordered[index + direction];
    if (next) showCompanyDetail(next.id);
  }

  function closeCompanyDetail() {
    if (!state.detailId) return;
    const previousId = state.detailId;
    const savedScrollTop = state.listScrollTop;
    state.detailId = null;
    state.highlightedId = null;
    detail.hidden = true;
    detail.replaceChildren();
    browser.hidden = false;
    panel.classList.remove("showing-detail");
    closeCompanyPopup(true);
    renderList();
    requestAnimationFrame(() => {
      panel.scrollTop = savedScrollTop;
      const button = listButtons.get(previousId);
      if (button) button.focus({ preventScroll: true });
    });
  }

  function closeCompanyPopup(force = false) {
    clearTimeout(popupCloseTimer);
    popupCloseTimer = null;
    if (!force && activePopup && activePopup.companyId === state.detailId) return;
    if (activePopup) {
      activePopup.remove();
      activePopup = null;
    }
  }

  function scheduleClosePopup() {
    clearTimeout(popupCloseTimer);
    popupCloseTimer = setTimeout(() => closeCompanyPopup(), 220);
  }

  function openCompanyPopup(company) {
    if (!companyMap || !markerButtons.has(company.id)) return;
    clearTimeout(popupCloseTimer);
    if (activePopup && activePopup.companyId === company.id) return;
    closeCompanyPopup(true);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "company-popup";
    const matchedJobs = matchingJobCount(company);
    card.setAttribute("aria-label", `Open ${company.name}, ${matchedJobs} matching jobs`);
    const name = document.createElement("span");
    name.className = "company-popup-name";
    name.textContent = company.name;
    const count = document.createElement("span");
    count.className = "company-popup-count";
    count.textContent = `${countFormat.format(matchedJobs)} matching · ${countFormat.format(company.activeJobCount)} active`;
    card.append(name, count);
    card.addEventListener("click", event => {
      event.stopPropagation();
      openCompanyDetail(company.id);
    });
    card.addEventListener("mouseenter", () => clearTimeout(popupCloseTimer));
    card.addEventListener("mouseleave", scheduleClosePopup);
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 18, className: "luna-company-popup" })
      .setLngLat([company.longitude, company.latitude])
      .setDOMContent(card)
      .addTo(companyMap);
    popup.companyId = company.id;
    popup.on("close", () => {
      if (activePopup === popup) activePopup = null;
    });
    activePopup = popup;
  }

  function idsInCurrentBounds() {
    const visibleMapCompanies = mapCompanies.filter(companyPassesGlobalFilters);
    if (!companyMap) return new Set(visibleMapCompanies.map(company => company.id));
    const bounds = companyMap.getBounds();
    return new Set(visibleMapCompanies
      .filter(company => bounds.contains([company.longitude, company.latitude]))
      .map(company => company.id));
  }

  function updateMapBounds() {
    state.mapVisibleIds = idsInCurrentBounds();
    if (!state.detailId) {
      renderList();
      panel.scrollTop = 0;
    }
  }

  function fitCompanies() {
    const visibleMapCompanies = mapCompanies.filter(companyPassesGlobalFilters);
    if (!companyMap || !visibleMapCompanies.length) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const company of visibleMapCompanies) bounds.extend([company.longitude, company.latitude]);
    companyMap.fitBounds(bounds, { padding: 40, maxZoom: 13, duration: 0 });
  }

  function initializeMap() {
    try {
      if (!window.maplibregl) throw new Error("Map runtime unavailable");
      companyMap = new maplibregl.Map({
        container: "jobs-map",
        style: "https://tiles.openfreemap.org/styles/liberty",
        center: [-122.4194, 37.7749],
        zoom: 11,
        attributionControl: true
      });
      companyMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      companyMap.on("error", () => {
        mapError.hidden = false;
        mapError.textContent = "Map tiles could not load. Check your connection; the company list is still available.";
      });
      companyMap.on("idle", () => { if (companyMap.areTilesLoaded()) mapError.hidden = true; });
      companyMap.on("movestart", () => { if (!state.detailId) closeCompanyPopup(true); });
      companyMap.on("moveend", updateMapBounds);
      for (const company of mapCompanies) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "map-marker";
        button.dataset.companyId = company.id;
        button.textContent = String(matchingJobCount(company));
        button.setAttribute("aria-pressed", "false");
        button.title = `${company.name} · ${matchingJobCount(company)} matching jobs (${activeFilterDescription()}) · ${company.activeJobCount} active jobs company-wide · ${company.street}\nVerified company office; individual jobs may have other workplaces.`;
        button.setAttribute("aria-label", `Open ${company.name}, ${matchingJobCount(company)} matching jobs`);
        button.addEventListener("mouseenter", () => highlightCompany(company.id));
        button.addEventListener("mouseleave", () => clearCompanyHighlight(company.id));
        button.addEventListener("focus", () => highlightCompany(company.id));
        button.addEventListener("blur", () => clearCompanyHighlight(company.id));
        button.addEventListener("click", event => {
          event.stopPropagation();
          openCompanyDetail(company.id);
        });
        markerButtons.set(company.id, button);
        new maplibregl.Marker({ element: button, anchor: "center" })
          .setLngLat([company.longitude, company.latitude])
          .addTo(companyMap);
      }
      syncGlobalFilters();
      fitCompanies();
      new ResizeObserver(() => companyMap.resize()).observe(document.getElementById("jobs-map"));
    } catch (error) {
      mapError.hidden = false;
      mapError.textContent = "The map is unavailable in this browser. You can still browse companies and jobs.";
      console.warn("Luna map:", error.message);
    }
  }

  document.getElementById("reset").addEventListener("click", () => {
    state.query = "";
    state.detailId = null;
    state.highlightedId = null;
    state.sort = "name";
    state.direction = 1;
    state.mapVisibleIds = null;
    state.listScrollTop = 0;
    state.jevFilterKeys = new Set(DEFAULT_JEV_FILTER_KEYS);
    state.detailFilters = { category: new Set(), workplace: new Set(), location: new Set() };
    search.value = "";
    detail.hidden = true;
    detail.replaceChildren();
    browser.hidden = false;
    panel.classList.remove("showing-detail");
    closeCompanyPopup(true);
    syncGlobalFilters();
    renderList();
    panel.scrollTop = 0;
    fitCompanies();
  });

  search.addEventListener("input", () => {
    if (state.detailId) {
      state.detailId = null;
      state.highlightedId = null;
      detail.hidden = true;
      detail.replaceChildren();
      browser.hidden = false;
      panel.classList.remove("showing-detail");
      closeCompanyPopup(true);
    }
    state.query = search.value;
    renderList();
    panel.scrollTop = 0;
  });

  document.addEventListener("keydown", event => {
    if (!state.detailId) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeCompanyDetail();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const target = event.target;
    if (target instanceof HTMLElement && (
      target.isContentEditable || target.matches("input, textarea, select, [contenteditable='true']")
    )) return;
    event.preventDefault();
    navigateCompanyDetail(event.key === "ArrowDown" ? 1 : -1);
  });

  for (const button of document.querySelectorAll("[data-sort]")) {
    button.addEventListener("click", () => {
      state.direction = state.sort === button.dataset.sort ? -state.direction : 1;
      state.sort = button.dataset.sort;
      renderList();
      panel.scrollTop = 0;
    });
  }

  renderGlobalFilters();
  renderList();
  initializeMap();
})();
