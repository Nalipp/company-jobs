(() => {
  "use strict";

  const snapshot = JSON.parse(document.getElementById("jobs-data").textContent);
  const companies = snapshot.companies;
  const mapCompanies = companies.filter(company => company.jobs.length > 0);
  const byId = new Map(mapCompanies.map(company => [company.id, company]));
  const countFormat = new Intl.NumberFormat();
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const state = {
    query: "",
    selectedId: null,
    sort: "name",
    direction: 1,
    expanded: new Map(),
    mapVisibleIds: null
  };
  const search = document.getElementById("search");
  const list = document.getElementById("companies");
  const panel = document.getElementById("company-list");
  const summary = document.getElementById("summary");
  const mapError = document.getElementById("map-error");
  const searchIndexes = new Map(mapCompanies.map(company => [company.id, {
    company: [company.name, company.street, company.workplace].join(" ").toLowerCase(),
    jobs: company.jobs.map(job => [job.title, job.compensation || ""].join(" ").toLowerCase())
  }]));
  let companyMap = null;
  let activePopup = null;
  let popupCloseTimer = null;
  const markerButtons = new Map();

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch {
      return null;
    }
  }

  function visibleCompanies() {
    const terms = state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const result = [];
    for (const company of mapCompanies) {
      if (state.mapVisibleIds && !state.mapVisibleIds.has(company.id)) continue;
      if (state.selectedId && company.id !== state.selectedId) continue;
      const index = searchIndexes.get(company.id);
      let jobs = company.jobs;
      if (terms.length) {
        const matchesCompany = terms.every(term => index.company.includes(term));
        if (!matchesCompany) {
          jobs = company.jobs.filter((job, position) => terms.every(term => `${index.company} ${index.jobs[position]}`.includes(term)));
          if (!jobs.length) continue;
        }
      }
      result.push({ ...company, jobs });
    }
    return result.sort((a, b) => state.direction * collator.compare(a[state.sort], b[state.sort]) || collator.compare(a.name, b.name) || collator.compare(a.id, b.id));
  }

  function isExpanded(id) {
    // A map click and a search result open by default; the landing/reset view is collapsed.
    return state.expanded.has(id) ? state.expanded.get(id) : Boolean(state.selectedId || state.query.trim());
  }

  function makeSpan(className, text) {
    const span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  function jobDetails(company, id) {
    const details = document.createElement("div");
    details.id = id;
    details.className = "job-details";
    details.hidden = !isExpanded(company.id);
    const table = document.createElement("table");
    table.className = "job-table";
    table.setAttribute("aria-label", `${company.name} jobs`);
    const cols = document.createElement("colgroup");
    cols.append(document.createElement("col"), document.createElement("col"));
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    for (const label of ["Job title", "Compensation"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.append(th);
    }
    head.append(header);
    const body = document.createElement("tbody");
    for (const job of company.jobs) {
      const row = document.createElement("tr");
      row.dataset.jobId = job.id;
      const title = document.createElement("td");
      const pay = document.createElement("td");
      pay.className = "job-pay";
      const url = safeUrl(job.url);
      if (url) {
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = job.title;
        title.append(link);
      } else {
        title.textContent = job.title;
      }
      pay.textContent = job.compensation || "—";
      if (!job.compensation) pay.title = "Compensation not supplied by the source";
      row.append(title, pay);
      body.append(row);
    }
    table.append(cols, head, body);
    details.append(table);
    return details;
  }

  function render() {
    const visible = visibleCompanies();
    const fragment = document.createDocumentFragment();
    for (const company of visible) {
      const article = document.createElement("article");
      article.className = "company";
      article.dataset.companyId = company.id;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "company-row";
      const detailsId = `jobs-${company.id}`;
      button.setAttribute("aria-expanded", String(isExpanded(company.id)));
      button.setAttribute("aria-controls", detailsId);
      const name = makeSpan("company-name", "");
      const chevron = makeSpan("chevron", isExpanded(company.id) ? "⌄" : "›");
      chevron.setAttribute("aria-hidden", "true");
      const label = makeSpan("name-label", company.name);
      label.title = company.name;
      const count = makeSpan("company-count", countFormat.format(company.jobs.length));
      count.setAttribute("aria-label", `${company.jobs.length} jobs`);
      name.append(chevron, label, count);
      const address = makeSpan("company-address", company.street || "Street not supplied");
      address.title = company.street || "Street not supplied";
      const workplace = makeSpan("company-workplace", company.workplace);
      workplace.title = "Most common workplace arrangement across the company's active postings";
      button.append(name, address, workplace);
      const details = jobDetails(company, detailsId);
      button.addEventListener("click", () => {
        const expanded = !isExpanded(company.id);
        state.expanded.set(company.id, expanded);
        button.setAttribute("aria-expanded", String(expanded));
        details.hidden = !expanded;
        chevron.textContent = expanded ? "⌄" : "›";
      });
      article.append(button, details);
      fragment.append(article);
    }
    list.replaceChildren(fragment);
    document.getElementById("empty").hidden = visible.length > 0;
    const jobs = visible.reduce((total, company) => total + company.jobs.length, 0);
    summary.textContent = `${countFormat.format(visible.length)} ${visible.length === 1 ? "company" : "companies"} · ${countFormat.format(jobs)} ${jobs === 1 ? "job" : "jobs"}`;
    summary.title = "Engineering/technical · Sales/business development · Product · Design/UX";
    for (const button of document.querySelectorAll("[data-sort]")) {
      const active = button.dataset.sort === state.sort;
      button.querySelector(".sort-arrow").textContent = active ? state.direction === 1 ? "↑" : "↓" : "";
      button.setAttribute("aria-label", `Sort by ${button.dataset.sort === "name" ? "company" : button.dataset.sort}${active ? state.direction === 1 ? ", ascending" : ", descending" : ""}`);
    }
    for (const [id, button] of markerButtons) button.setAttribute("aria-pressed", String(id === state.selectedId));
  }

  function closeCompanyPopup() {
    clearTimeout(popupCloseTimer);
    popupCloseTimer = null;
    if (activePopup) {
      activePopup.remove();
      activePopup = null;
    }
  }

  function scheduleClosePopup() {
    clearTimeout(popupCloseTimer);
    popupCloseTimer = setTimeout(closeCompanyPopup, 220);
  }

  function openCompanyPopup(company) {
    if (!companyMap) return;
    clearTimeout(popupCloseTimer);
    if (activePopup && activePopup.companyId === company.id) return;
    closeCompanyPopup();
    const card = document.createElement("button");
    card.type = "button";
    card.className = "company-popup";
    card.setAttribute("aria-label", `Open ${company.name}, ${company.jobs.length} jobs`);
    const name = document.createElement("span");
    name.className = "company-popup-name";
    name.textContent = company.name;
    const count = document.createElement("span");
    count.className = "company-popup-count";
    count.textContent = `${countFormat.format(company.jobs.length)} ${company.jobs.length === 1 ? "active job" : "active jobs"}`;
    card.append(name, count);
    card.addEventListener("click", event => {
      event.stopPropagation();
      selectCompany(company.id);
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

  function selectCompany(id) {
    const company = byId.get(id);
    if (!company) return;
    state.selectedId = id;
    state.query = "";
    search.value = "";
    state.expanded.clear();
    state.expanded.set(id, true);
    render();
    panel.scrollTop = 0;
  }

  function idsInCurrentBounds() {
    if (!companyMap) return new Set(mapCompanies.map(company => company.id));
    const bounds = companyMap.getBounds();
    return new Set(mapCompanies.filter(company => bounds.contains([company.longitude, company.latitude])).map(company => company.id));
  }

  function resetToMapBounds() {
    state.selectedId = null;
    state.query = "";
    search.value = "";
    state.expanded.clear();
    state.mapVisibleIds = idsInCurrentBounds();
    closeCompanyPopup();
    render();
    panel.scrollTop = 0;
  }

  function fitCompanies() {
    if (!companyMap || !mapCompanies.length) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const company of mapCompanies) bounds.extend([company.longitude, company.latitude]);
    companyMap.fitBounds(bounds, { padding: 40, maxZoom: 13, duration: 0 });
  }

  function initializeMap() {
    try {
      if (!window.maplibregl) throw new Error("Map runtime unavailable");
      companyMap = new maplibregl.Map({ container: "jobs-map", style: "https://tiles.openfreemap.org/styles/liberty", center: [-122.4194, 37.7749], zoom: 11, attributionControl: true });
      companyMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      companyMap.on("error", () => {
        mapError.hidden = false;
        mapError.textContent = "Map tiles could not load. Check your connection; the company list is still available.";
      });
      companyMap.on("idle", () => { if (companyMap.areTilesLoaded()) mapError.hidden = true; });
      companyMap.on("movestart", closeCompanyPopup);
      companyMap.on("moveend", resetToMapBounds);
      for (const company of mapCompanies) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "map-marker";
        button.dataset.companyId = company.id;
        button.textContent = String(company.jobs.length);
        button.setAttribute("aria-pressed", "false");
        button.title = `${company.name} · ${company.jobs.length} active jobs · ${company.street}\nVerified company office; individual jobs may have other workplaces.`;
        button.setAttribute("aria-label", `Show ${company.name}, ${company.jobs.length} jobs`);
        button.addEventListener("mouseenter", () => openCompanyPopup(company));
        button.addEventListener("mouseleave", scheduleClosePopup);
        button.addEventListener("focus", () => openCompanyPopup(company));
        button.addEventListener("blur", scheduleClosePopup);
        button.addEventListener("click", event => {
          event.stopPropagation();
          selectCompany(company.id);
        });
        markerButtons.set(company.id, button);
        new maplibregl.Marker({ element: button, anchor: "center" }).setLngLat([company.longitude, company.latitude]).addTo(companyMap);
      }
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
    state.selectedId = null;
    state.sort = "name";
    state.direction = 1;
    state.expanded.clear();
    state.mapVisibleIds = null;
    search.value = "";
    closeCompanyPopup();
    render();
    panel.scrollTop = 0;
    fitCompanies();
  });
  search.addEventListener("input", () => {
    state.query = search.value;
    state.selectedId = null;
    state.expanded.clear();
    render();
    panel.scrollTop = 0;
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") resetToMapBounds();
  });
  for (const button of document.querySelectorAll("[data-sort]")) button.addEventListener("click", () => {
    state.direction = state.sort === button.dataset.sort ? -state.direction : 1;
    state.sort = button.dataset.sort;
    render();
    panel.scrollTop = 0;
  });

  render();
  initializeMap();
})();
