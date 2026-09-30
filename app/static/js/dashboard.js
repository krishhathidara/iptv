(() => {
  "use strict";

  const state = {
    view: "live",
    facets: null,
    live: {
      page: 1,
      pageSize: 16,
      search: "",
      language: "",
      country: "",
      category: "",
      sortBy: "name",
      sortDirection: "asc",
      requestId: 0,
    },
    vod: {
      page: 1,
      pageSize: 15,
      search: "",
      language: "",
      country: "",
      mediaType: "",
      sortBy: "title",
      sortDirection: "asc",
      requestId: 0,
    },
    vidsrc: { page: 1, pageSize: 15, mediaType: "", requestId: 0, items: [] },
    imdb: { page: 1, pageSize: 15, matchedOnly: true, requestId: 0, items: [] },
    importMode: "file",
    player: {
      hls: null,
      item: null,
      kind: null,
      sources: [],
      sourceIndex: 0,
      playbackToken: 0,
      startupTimer: null,
      failedToken: null,
      previousFocus: null,
      attemptFailures: new Set(),
    },
  };

  const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
  const regionNames = new Intl.DisplayNames(["en"], { type: "region" });

  const elements = {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function cacheElements() {
    [
      "sidebar", "sidebarBackdrop", "mobileMenuButton", "pageEyebrow", "pageTitle",
      "globalSearch", "demoPill", "navLiveCount", "navIndiaCount", "navVodCount", "liveTotalMetric",
      "liveLanguageMetric", "liveCountryMetric", "liveCategoryMetric", "vodTotalMetric",
      "vodLanguageMetric", "vodCountryMetric", "vodTypeMetric", "liveLanguageFacets", "liveCategoryFacets", "liveCollectionTitle", "playlistExportLink",
      "vodLanguageFacets", "vodCountryFacets", "vodTypeFacets", "liveLanguageFilter", "liveCountryFilter",
      "liveCategoryFilter", "liveSort", "vodLanguageFilter", "vodCountryFilter",
      "vodTypeFilter", "vodSort", "liveGrid", "vodGrid", "liveResultCount",
      "vodResultCount", "livePagination", "vodPagination", "refreshLiveButton",
      "refreshVodButton", "importForm", "playlistFile", "selectedFileName", "dropzone",
      "playerCloseButton", "sourceUrl", "rawM3u", "sourceName", "defaultLanguage", "defaultCountry",
      "defaultCategory", "validateUrls", "saveRemoteSource", "replaceMissing", "importButton",
      "importResult", "sourceRegistry", "refreshSourcesButton", "playerModal",
      "videoPlayer", "videoPlaceholder", "playerStatus", "resumePlayerButton", "playerKindBadge", "playerTitle",
      "playerMeta", "playerDescription", "copyStreamButton", "fullscreenButton", "sourceSelectorWrap",
      "sourceSelector", "toastStack", "indiaNotice", "liveHeading", "liveSubtitle",
      "vodImportForm", "vodImportSource", "vodImportItems", "vodImportButton", "vodImportResult",
      "vidsrcResultCount", "vidsrcTypeFilter", "syncVidsrcButton", "vidsrcGrid", "vidsrcPagination", "embedPlayer",
      "imdbResultCount", "imdbAvailabilityFilter", "imdbGrid", "imdbPagination",
    ].forEach((id) => { elements[id] = document.getElementById(id); });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function debounce(callback, delay = 300) {
    let timer;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback(...args), delay);
    };
  }

  function displayLanguage(code) {
    if (!code || code === "und") return "Unspecified";
    try { return languageNames.of(code) || code.toUpperCase(); }
    catch { return code.toUpperCase(); }
  }

  function displayCountry(code) {
    if (!code || code === "ZZ") return "International";
    try { return regionNames.of(code) || code; }
    catch { return code; }
  }

  function displayMediaType(value) {
    return value === "tv" ? "TV show" : value === "movie" ? "Movie" : value;
  }

  function initials(value) {
    return String(value || "TV")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
  }

  function formatCount(value) {
    return new Intl.NumberFormat("en", { notation: value >= 10000 ? "compact" : "standard" }).format(value || 0);
  }

  function showToast(title, message, error = false) {
    const toast = document.createElement("div");
    toast.className = `toast${error ? " is-error" : ""}`;
    toast.innerHTML = `<div><strong>${escapeHtml(title)}</strong>${escapeHtml(message)}</div>`;
    elements.toastStack.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  async function apiFetch(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      let message = `Request failed with HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
      } catch { /* Keep fallback. */ }
      throw new Error(message);
    }
    return response.json();
  }

  function switchView(view) {
    if (!["live", "india", "vod", "import"].includes(view)) return;
    const focusContent = window.matchMedia("(max-width: 980px)").matches && elements.sidebar.contains(document.activeElement);
    if (view !== state.view && (view === "india" || state.view === "india")) {
      state.live.country = view === "india" ? "IN" : "";
      state.live.language = "";
      state.live.category = "";
      state.live.search = "";
      state.live.page = 1;
    }
    state.view = view;
    $$(".workspace").forEach((workspace) => workspace.classList.toggle("is-active", workspace.dataset.view === (view === "india" ? "live" : view)));
    $$("[data-view-target]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.viewTarget === view);
      button.setAttribute("aria-current", button.dataset.viewTarget === view ? "page" : "false");
    });
    const config = {
      live: ["CATALOG", "Live TV", "Search live channels"],
      india: ["INDIA", "Indian TV", "Search Indian live channels"],
      vod: ["CATALOG", "VOD Library", "Search movies and shows"],
      import: ["MANAGE", "Import Sources", "Search is available in Live TV and VOD"],
    }[view];
    elements.pageEyebrow.textContent = config[0];
    elements.pageTitle.textContent = config[1];
    elements.globalSearch.placeholder = config[2];
    elements.globalSearch.disabled = view === "import";
    elements.globalSearch.value = ["live", "india"].includes(view) ? state.live.search : view === "vod" ? state.vod.search : "";
    elements.indiaNotice.hidden = view !== "india";
    elements.liveHeading.textContent = view === "india" ? "Indian live channels" : "Live channel library";
    elements.liveSubtitle.textContent = view === "india"
      ? "Browse available Indian streams by language and category. Select a channel to start playback."
      : "Browse and play available streams by language, country, and category.";
    elements.liveCountryFilter.disabled = view === "india";
    closeSidebar();
    if (focusContent) {
      const target = $(".workspace.is-active button:not(:disabled)");
      target?.focus();
      target?.scrollIntoView({ block: "nearest" });
    }
    window.history.replaceState(null, "", view === "live" ? "/" : `/#${view}`);
    if (view === "live" || view === "india") {
      syncFilterControls();
      loadFacets();
      loadLive();
    } else if (view !== "import") loadFacets();
    if (view === "vod") { loadVod(); loadVidsrc(); loadImdb(); }
    if (view === "import") loadSources();
  }

  function openSidebar() {
    elements.sidebar.classList.add("is-open");
    elements.sidebarBackdrop.classList.add("is-open");
    if (document.body.classList.contains("tv-remote")) $(".nav-item.is-active", elements.sidebar)?.focus();
  }

  function closeSidebar() {
    elements.sidebar.classList.remove("is-open");
    elements.sidebarBackdrop.classList.remove("is-open");
  }

  function buildFacetButton(item, type, activeValue, valueFormatter) {
    const label = valueFormatter(item.value);
    return `
      <button class="facet-button${activeValue === item.value ? " is-active" : ""}" type="button" data-facet-type="${escapeHtml(type)}" data-facet-value="${escapeHtml(item.value)}">
        <span class="facet-symbol">${escapeHtml(item.value.slice(0, 3))}</span>
        <span class="facet-copy"><strong>${escapeHtml(label)}</strong><small>${formatCount(item.count)} title${item.count === 1 ? "" : "s"}</small></span>
      </button>`;
  }

  function buildCategoryButton(item) {
    const active = state.live.category === item.value;
    return `<button type="button" class="category-button${active ? " is-active" : ""}" data-facet-type="live-category" data-facet-value="${escapeHtml(item.value)}" aria-pressed="${active}"><strong>${escapeHtml(item.value)}</strong><span>${formatCount(item.count)} live</span></button>`;
  }

  function sortCategories(items) {
    const featured = ["News", "Sports", "Kids", "Movies", "Entertainment", "Music", "Documentary", "Education"];
    return [...items].sort((a, b) => {
      const aIndex = featured.indexOf(a.value);
      const bIndex = featured.indexOf(b.value);
      if (aIndex !== -1 || bIndex !== -1) return (aIndex === -1 ? featured.length : aIndex) - (bIndex === -1 ? featured.length : bIndex);
      return b.count - a.count || a.value.localeCompare(b.value);
    });
  }

  function updateExportLink() {
    const params = new URLSearchParams();
    if (state.live.language) params.set("lang", state.live.language);
    if (state.live.country) params.set("country", state.live.country);
    if (state.live.category) params.set("category", state.live.category);
    elements.playlistExportLink.href = `/playlist.m3u${params.size ? `?${params}` : ""}`;
    elements.liveCollectionTitle.textContent = state.live.category ? `${state.live.category} channels` : state.view === "india" ? "Indian live channels" : "All live channels";
  }

  function fillSelect(select, items, placeholder, formatter) {
    const current = select.value;
    select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>${items.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(formatter(item.value))} (${formatCount(item.count)})</option>`).join("")}`;
    select.value = current;
  }

  async function loadFacets() {
    try {
      const requestedView = state.view;
      const facets = await apiFetch(requestedView === "india" ? "/catalog/facets?country=IN" : "/catalog/facets");
      if (requestedView !== state.view) return;
      state.facets = facets;
      const selectedCategory = document.activeElement?.dataset?.facetType === "live-category"
        ? document.activeElement.dataset.facetValue : null;
      const { live, vod, demo_mode: demoMode } = state.facets;
      elements.demoPill.hidden = !demoMode;
      if (requestedView !== "india") elements.navLiveCount.textContent = formatCount(live.total);
      elements.navIndiaCount.textContent = formatCount(requestedView === "india"
        ? live.total : (live.countries.find((item) => item.value === "IN")?.count || 0));
      elements.navVodCount.textContent = formatCount(vod.total);
      elements.liveTotalMetric.textContent = formatCount(live.total);
      elements.liveLanguageMetric.textContent = formatCount(live.languages.length);
      elements.liveCountryMetric.textContent = formatCount(live.countries.length);
      elements.liveCategoryMetric.textContent = formatCount(live.categories.length);
      elements.vodTotalMetric.textContent = formatCount(vod.total);
      elements.vodLanguageMetric.textContent = formatCount(vod.languages.length);
      elements.vodCountryMetric.textContent = formatCount(vod.countries.length);
      elements.vodTypeMetric.textContent = formatCount(vod.media_types.length);

      elements.liveLanguageFacets.innerHTML = live.languages.length
        ? live.languages.map((item) => buildFacetButton(item, "live-language", state.live.language, displayLanguage)).join("")
        : '<p class="channel-category">No language groups yet.</p>';
      elements.liveCategoryFacets.innerHTML = live.categories.length
        ? sortCategories(live.categories).map(buildCategoryButton).join("")
        : '<p class="channel-category">No categories available yet.</p>';
      if (selectedCategory) {
        [...elements.liveCategoryFacets.querySelectorAll("[data-facet-value]")]
          .find((button) => button.dataset.facetValue === selectedCategory)?.focus();
      }
      updateExportLink();
      elements.vodLanguageFacets.innerHTML = vod.languages.length
        ? vod.languages.map((item) => buildFacetButton(item, "vod-language", state.vod.language, displayLanguage)).join("")
        : '<p class="channel-category">No VOD languages imported yet.</p>';
      elements.vodCountryFacets.innerHTML = vod.countries.length
        ? vod.countries.map((item) => buildFacetButton(item, "vod-country", state.vod.country, displayCountry)).join("")
        : '<p class="channel-category">No country groups yet.</p>';
      elements.vodTypeFacets.innerHTML = ["movie", "tv"].map((type) => buildFacetButton(
        vod.media_types.find((item) => item.value === type) || { value: type, count: 0 },
        "vod-type", state.vod.mediaType, displayMediaType,
      )).join("");

      fillSelect(elements.liveLanguageFilter, live.languages, "All languages", displayLanguage);
      fillSelect(elements.liveCountryFilter, live.countries, "All countries", displayCountry);
      fillSelect(elements.liveCategoryFilter, live.categories, "All categories", (value) => value);
      fillSelect(elements.vodLanguageFilter, vod.languages, "All languages", displayLanguage);
      fillSelect(elements.vodCountryFilter, vod.countries, "All countries", displayCountry);
      fillSelect(elements.vodTypeFilter, ["movie", "tv"].map((type) =>
        vod.media_types.find((item) => item.value === type) || { value: type, count: 0 },
      ), "Movies & shows", displayMediaType);
      syncFilterControls();
    } catch (error) {
      showToast("Catalog summary unavailable", error.message, true);
    }
  }

  function syncFilterControls() {
    elements.liveLanguageFilter.value = state.live.language;
    elements.liveCountryFilter.value = state.live.country;
    elements.liveCategoryFilter.value = state.live.category;
    updateExportLink();
    elements.vodLanguageFilter.value = state.vod.language;
    elements.vodCountryFilter.value = state.vod.country;
    elements.vodTypeFilter.value = state.vod.mediaType;
  }

  function renderLoading(grid, count) {
    grid.innerHTML = `<div class="loading-grid">${Array.from({ length: count }, () => '<div class="skeleton"></div>').join("")}</div>`;
  }

  function renderEmpty(grid, title, message) {
    grid.innerHTML = `<div class="empty-state"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></div>`;
  }

  function renderError(grid, error) {
    grid.innerHTML = `<div class="empty-state"><svg viewBox="0 0 24 24"><path d="M12 8v5m0 3h.01"/><circle cx="12" cy="12" r="9"/></svg><h3>Could not load the catalog</h3><p>${escapeHtml(error.message)}</p></div>`;
  }

  function renderChannelCard(item) {
    const logo = item.logo
      ? `<img src="${escapeHtml(item.logo)}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='<span class=&quot;logo-fallback&quot;>${escapeHtml(initials(item.name))}</span>'">`
      : `<span class="logo-fallback">${escapeHtml(initials(item.name))}</span>`;
    return `
      <article class="channel-card">
        <div class="card-top"><div class="channel-logo">${logo}</div><span class="live-tag">LIVE</span></div>
        <h3 title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</h3>
        <p class="channel-category">${escapeHtml(item.category || "Uncategorized")}</p>
        <div class="meta-chips"><span class="meta-chip">${escapeHtml(displayLanguage(item.language_code))}</span><span class="meta-chip">${escapeHtml(displayCountry(item.country_code))}</span></div>
        <div class="card-action-row"><span class="source-copy" title="${escapeHtml(item.source_name)}">${escapeHtml(item.source_name)}</span><button class="play-button" type="button" data-play-channel="${item.id}"><svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z"/></svg>Play</button></div>
      </article>`;
  }

  function posterUrl(path) {
    if (!path) return "/static/img/posters/default.svg";
    if (/^https?:\/\//i.test(path)) return path;
    if (path.startsWith("/")) return path;
    return `https://image.tmdb.org/t/p/w500${path}`;
  }

  function renderVodCard(item) {
    return `
      <article class="vod-card">
        <div class="vod-poster">
          <img src="${escapeHtml(posterUrl(item.poster_path))}" alt="${escapeHtml(item.title)} poster" loading="lazy" onerror="this.src='/static/img/posters/default.svg'">
          <span class="vod-type">${escapeHtml(displayMediaType(item.media_type))}</span>
          <button class="vod-play-overlay" type="button" data-play-vod="${item.id}" aria-label="Play ${escapeHtml(item.title)}"><svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z"/></svg></button>
        </div>
        <div class="vod-copy">
          <h3 title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</h3>
          <div class="vod-meta"><span>${escapeHtml(item.release_year || "Year unknown")}</span><span>${escapeHtml(displayLanguage(item.language_code))}</span><span>${escapeHtml(displayCountry(item.country_code))}</span></div>
          <p class="vod-synopsis">${escapeHtml(item.synopsis || "No synopsis is available for this title.")}</p>
        </div>
      </article>`;
  }

  function renderVidsrcCard(item) {
    return `<article class="vod-card vidsrc-card">
      <div class="vod-poster"><img src="/static/img/posters/default.svg" alt="" loading="lazy"><span class="vod-type">${escapeHtml(displayMediaType(item.media_type))}</span></div>
      <div class="vod-copy"><h3>${escapeHtml(item.title)}</h3>
        <p class="vod-synopsis">VidSrc-hosted iframe. Playback and rights are not verified by this app.</p>
        <button class="primary-button vidsrc-play" type="button" data-play-vidsrc="${item.id}">Watch here</button>
      </div></article>`;
  }

  function renderImdbCard(item) {
    return `<article class="vod-card imdb-card">
      <div class="vod-poster"><img src="/static/img/posters/default.svg" alt="" loading="lazy"><span class="vod-type">MOVIE</span></div>
      <div class="vod-copy"><h3>${escapeHtml(item.title)}</h3>
        <div class="vod-meta"><span>${escapeHtml(item.release_year || "Year unknown")}</span><span>${escapeHtml(item.imdb_id)}</span></div>
        <p class="vod-synopsis">${item.embed_url ? "VidSrc ID match · playback and rights unverified." : "No matching VidSrc ID; no playback source."}</p>
        ${item.embed_url ? `<button class="primary-button vidsrc-play" type="button" data-play-imdb="${escapeHtml(item.imdb_id)}">Watch via VidSrc</button>` : ""}
      </div></article>`;
  }

  function paginationMarkup(page, pageSize, total, target) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (pages <= 1) return "";
    const candidates = new Set([1, pages, page - 1, page, page + 1]);
    const visible = [...candidates].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b);
    let last = 0;
    const buttons = [];
    for (const value of visible) {
      if (last && value - last > 1) buttons.push('<span class="page-button">…</span>');
      buttons.push(`<button class="page-button${value === page ? " is-active" : ""}" type="button" data-page-target="${target}" data-page="${value}">${value}</button>`);
      last = value;
    }
    return `<button class="page-button" type="button" data-page-target="${target}" data-page="${page - 1}" ${page === 1 ? "disabled" : ""}>←</button>${buttons.join("")}<button class="page-button" type="button" data-page-target="${target}" data-page="${page + 1}" ${page === pages ? "disabled" : ""}>→</button>`;
  }

  async function loadLive() {
    const requestId = ++state.live.requestId;
    renderLoading(elements.liveGrid, 8);
    const params = new URLSearchParams({
      page: state.live.page,
      page_size: state.live.pageSize,
      sort_by: state.live.sortBy,
      sort_direction: state.live.sortDirection,
    });
    if (state.live.search) params.set("search", state.live.search);
    if (state.live.language) params.set("lang", state.live.language);
    if (state.live.country) params.set("country", state.live.country);
    if (state.live.category) params.set("category", state.live.category);
    try {
      const payload = await apiFetch(`/channels?${params}`);
      if (requestId !== state.live.requestId) return;
      elements.liveResultCount.textContent = `${formatCount(payload.total)} result${payload.total === 1 ? "" : "s"}`;
      elements.liveGrid.innerHTML = payload.items.length ? payload.items.map(renderChannelCard).join("") : "";
      if (!payload.items.length) renderEmpty(elements.liveGrid, "No channels found", "Try changing the language, country, category, or search term.");
      elements.livePagination.innerHTML = paginationMarkup(payload.page, payload.page_size, payload.total, "live");
      state.live.items = payload.items;
    } catch (error) {
      if (requestId === state.live.requestId) renderError(elements.liveGrid, error);
    }
  }

  async function loadVod() {
    const requestId = ++state.vod.requestId;
    renderLoading(elements.vodGrid, 10);
    const params = new URLSearchParams({
      page: state.vod.page,
      page_size: state.vod.pageSize,
      sort_by: state.vod.sortBy,
      sort_direction: state.vod.sortDirection,
    });
    if (state.vod.search) params.set("q", state.vod.search);
    if (state.vod.language) params.set("lang", state.vod.language);
    if (state.vod.country) params.set("country", state.vod.country);
    if (state.vod.mediaType) params.set("media_type", state.vod.mediaType);
    try {
      const payload = await apiFetch(`/vod/search?${params}`);
      if (requestId !== state.vod.requestId) return;
      elements.vodResultCount.textContent = `${formatCount(payload.total)} result${payload.total === 1 ? "" : "s"}`;
      elements.vodGrid.innerHTML = payload.items.length ? payload.items.map(renderVodCard).join("") : "";
      if (!payload.items.length) renderEmpty(elements.vodGrid, "No VOD titles found", "Add authorized direct movie or TV episode links below, or change the language, type, or search term.");
      elements.vodPagination.innerHTML = paginationMarkup(payload.page, payload.page_size, payload.total, "vod");
      state.vod.items = payload.items;
    } catch (error) {
      if (requestId === state.vod.requestId) renderError(elements.vodGrid, error);
    }
  }

  async function loadVidsrc() {
    const requestId = ++state.vidsrc.requestId;
    renderLoading(elements.vidsrcGrid, 5);
    const params = new URLSearchParams({ page: state.vidsrc.page, page_size: state.vidsrc.pageSize });
    if (state.vod.search) params.set("q", state.vod.search);
    if (state.vidsrc.mediaType) params.set("media_type", state.vidsrc.mediaType);
    try {
      const data = await apiFetch(`/vod/vidsrc/search?${params}`);
      if (requestId !== state.vidsrc.requestId) return;
      state.vidsrc.items = data.items;
      elements.vidsrcResultCount.textContent = `${formatCount(data.total)} indexed ID${data.total === 1 ? "" : "s"}`;
      elements.vidsrcGrid.innerHTML = data.items.length ? data.items.map(renderVidsrcCard).join("") : "";
      if (!data.items.length) renderEmpty(elements.vidsrcGrid, "No matching VidSrc names", "Some indexed IDs have no searchable name yet. Run the optional IMDb title enrichment for personal, non-commercial use, or search by IMDb ID.");
      elements.vidsrcPagination.innerHTML = paginationMarkup(data.page, data.page_size, data.total, "vidsrc");
      const unnamed = data.items.filter((item) => /^(Movie|Tv) · tt\d+$/.test(item.title));
      if (unnamed.length) {
        try {
          const resolved = await apiFetch("/vod/vidsrc/resolve", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids: unnamed.map((item) => item.id) }),
          });
          if (requestId === state.vidsrc.requestId) {
            const names = new Map(resolved.map((item) => [item.id, item]));
            state.vidsrc.items = data.items.map((item) => names.get(item.id) || item);
            elements.vidsrcGrid.innerHTML = state.vidsrc.items.map(renderVidsrcCard).join("");
          }
        } catch (error) {
          // The catalog and its watch buttons remain usable if metadata is unavailable.
          if (requestId === state.vidsrc.requestId) showToast("VidSrc names unavailable", error.message, true);
        }
      }
    } catch (error) {
      if (requestId === state.vidsrc.requestId) renderError(elements.vidsrcGrid, error);
    }
  }

  async function loadImdb() {
    const requestId = ++state.imdb.requestId;
    renderLoading(elements.imdbGrid, 5);
    const params = new URLSearchParams({ page: state.imdb.page, page_size: state.imdb.pageSize, vidsrc_only: state.imdb.matchedOnly });
    if (state.vod.search) params.set("q", state.vod.search);
    try {
      const data = await apiFetch(`/vod/imdb/search?${params}`);
      if (requestId !== state.imdb.requestId) return;
      state.imdb.items = data.items;
      elements.imdbResultCount.textContent = `${formatCount(data.total)} movie ID${data.total === 1 ? "" : "s"}`;
      elements.imdbGrid.innerHTML = data.items.length ? data.items.map(renderImdbCard).join("") : "";
      if (!data.items.length) renderEmpty(elements.imdbGrid, "No IMDb movies found", "Import the local IMDb movie subset, or change the search or ID-match filter.");
      elements.imdbPagination.innerHTML = paginationMarkup(data.page, data.page_size, data.total, "imdb");
    } catch (error) {
      if (requestId === state.imdb.requestId) renderError(elements.imdbGrid, error);
    }
  }

  async function syncVidsrc() {
    const button = elements.syncVidsrcButton;
    button.disabled = true;
    button.textContent = "Syncing ID lists and feed pages…";
    try {
      const result = await apiFetch("/vod/vidsrc/sync", { method: "POST" });
      showToast("VidSrc catalog updated", `${result.total} movie and TV entries indexed; video is provided by the iframe host.`);
      await Promise.all([loadVidsrc(), loadImdb()]);
    } catch (error) {
      showToast("VidSrc sync failed", error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "Sync VidSrc catalog";
    }
  }

  function updateLiveFilters() {
    state.live.language = elements.liveLanguageFilter.value;
    state.live.country = state.view === "india" ? "IN" : elements.liveCountryFilter.value;
    state.live.category = elements.liveCategoryFilter.value;
    [state.live.sortBy, state.live.sortDirection] = elements.liveSort.value.split(":");
    state.live.page = 1;
    updateExportLink();
    loadFacets();
    loadLive();
  }

  function updateVodFilters() {
    state.vod.language = elements.vodLanguageFilter.value;
    state.vod.country = elements.vodCountryFilter.value;
    state.vod.mediaType = elements.vodTypeFilter.value;
    [state.vod.sortBy, state.vod.sortDirection] = elements.vodSort.value.split(":");
    state.vod.page = 1;
    loadFacets();
    loadVod();
  }

  async function submitVodImport(event) {
    event.preventDefault();
    const button = elements.vodImportButton;
    button.disabled = true;
    try {
      const items = JSON.parse(elements.vodImportItems.value);
      if (!Array.isArray(items) || !items.length) throw new Error("Paste a nonempty JSON array of VOD items.");
      const payload = await apiFetch("/vod/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_name: elements.vodImportSource.value.trim(), items }),
      });
      elements.vodImportResult.textContent = `${payload.inserted_count} new and ${payload.updated_count} updated VOD items. URLs are not verified before playback.`;
      await loadFacets();
      await loadVod();
    } catch (error) {
      elements.vodImportResult.textContent = `Import failed: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }

  function destroyPlayer() {
    state.player.playbackToken += 1;
    elements.embedPlayer.removeAttribute("src");
    elements.embedPlayer.hidden = true;
    if (state.player.startupTimer) {
      window.clearTimeout(state.player.startupTimer);
      state.player.startupTimer = null;
    }
    if (state.player.hls) {
      state.player.hls.destroy();
      state.player.hls = null;
    }
    elements.videoPlayer.onloadeddata = null;
    elements.videoPlayer.onplaying = null;
    elements.videoPlayer.onplay = null;
    elements.videoPlayer.onerror = null;
    elements.videoPlayer.pause();
    elements.videoPlayer.removeAttribute("src");
    elements.videoPlayer.load();
  }

  function showPlayerStatus(message, loading = true) {
    elements.videoPlaceholder.hidden = false;
    elements.resumePlayerButton.hidden = true;
    $(".player-spinner", elements.videoPlaceholder).style.display = loading ? "block" : "none";
    $("strong", elements.videoPlaceholder).textContent = loading ? "Preparing stream" : "Playback unavailable";
    elements.playerStatus.textContent = message;
  }

  function hidePlayerStatus() {
    elements.videoPlaceholder.hidden = true;
    elements.resumePlayerButton.hidden = true;
  }

  function showManualPlay() {
    if (!elements.playerModal.classList.contains("is-open")) return;
    showPlayerStatus("Your TV or browser requires you to press Play to start the video.", false);
    elements.resumePlayerButton.hidden = false;
    elements.resumePlayerButton.focus();
  }

  function currentSourceLabel(index) {
    return index === 0 ? "primary source" : `alternative source ${index}`;
  }

  function failOrTryNext(message, token) {
    if (token !== state.player.playbackToken || token === state.player.failedToken) return;
    state.player.failedToken = token;
    if (state.player.startupTimer) {
      window.clearTimeout(state.player.startupTimer);
      state.player.startupTimer = null;
    }
    // A TV/browser failure alone does not prove a provider is down for
    // everybody. Revalidate upstream only after local alternatives fail.
    state.player.attemptFailures.add(state.player.sources[state.player.sourceIndex]);
    const nextIndex = state.player.sourceIndex + 1;
    if (nextIndex < state.player.sources.length) {
      showPlayerStatus(`${message} Trying ${currentSourceLabel(nextIndex)}…`);
      window.setTimeout(() => {
        if (token === state.player.playbackToken && elements.playerModal.classList.contains("is-open")) startPlayback(nextIndex);
      }, 150);
      return;
    }
    showPlayerStatus(`${message} ${state.player.kind === "live" ? "No other matching live source is available." : "No other playback source is available."}`, false);
    if (state.player.kind === "live" && state.player.item?.id) {
      const channelId = state.player.item.id;
      apiFetch(`/channels/${channelId}/playback-sources`, { method: "POST" })
        .then((health) => {
          loadFacets(); loadLive();
          if (token !== state.player.playbackToken || !elements.playerModal.classList.contains("is-open")) return;
          const newlyAvailable = health.sources.filter((url) => !state.player.attemptFailures.has(url));
          if (newlyAvailable.length) {
            state.player.sources = newlyAvailable;
            renderPlayerSources();
            startPlayback(0);
          } else {
            showPlayerStatus(`${message} The source is reachable but this TV/browser could not play it. Try a different IPTV player or channel.`, false);
          }
        })
        .catch((error) => {
          loadFacets(); loadLive();
          if (token === state.player.playbackToken && elements.playerModal.classList.contains("is-open")) showPlayerStatus(error.message, false);
        });
    }
  }

  function startPlayback(sourceIndex = 0) {
    destroyPlayer();
    state.player.failedToken = null;
    state.player.sourceIndex = sourceIndex;
    const url = state.player.sources[sourceIndex];
    const token = state.player.playbackToken;
    elements.sourceSelector.value = url;
    showPlayerStatus(`Connecting to ${currentSourceLabel(sourceIndex)}…`);
    const video = elements.videoPlayer;
    const nativeHls = video.canPlayType("application/vnd.apple.mpegurl");
    const hlsJsSupported = Boolean(window.Hls && window.Hls.isSupported());
    const mediaReady = () => {
      if (token !== state.player.playbackToken) return;
      if (state.player.startupTimer) {
        window.clearTimeout(state.player.startupTimer);
        state.player.startupTimer = null;
      }
      hidePlayerStatus();
    };
    const armStartupTimer = () => {
      if (token !== state.player.playbackToken || state.player.startupTimer) return;
      state.player.startupTimer = window.setTimeout(() => {
        failOrTryNext("The source connected but did not deliver playable video in time.", token);
      }, 15000);
    };
    video.onloadeddata = mediaReady;
    video.onplaying = mediaReady;
    video.onplay = armStartupTimer;
    if (nativeHls || hlsJsSupported || state.player.kind === "vod") armStartupTimer();

    // Direct on-demand files use the browser's native media player, not hls.js.
    // Page/embed URLs cannot be used as media sources.
    if (state.player.kind === "vod" && /\.(mp4|webm)(?:[?#]|$)/i.test(url)) {
      video.src = url;
      video.onerror = () => failOrTryNext("The browser could not play this VOD file or the provider rejected it.", token);
      video.play().catch(() => {
        if (token !== state.player.playbackToken) return;
        if (state.player.startupTimer) {
          window.clearTimeout(state.player.startupTimer);
          state.player.startupTimer = null;
        }
        showManualPlay();
      });
      return;
    }

    if (nativeHls) {
      video.src = url;
      video.onerror = () => {
        failOrTryNext("The browser could not play this source.", token);
      };
      video.play().catch(() => {
        if (token !== state.player.playbackToken) return;
        if (state.player.startupTimer) {
          window.clearTimeout(state.player.startupTimer);
          state.player.startupTimer = null;
        }
        showManualPlay();
      });
      return;
    }

    if (hlsJsSupported) {
      const hls = new window.Hls({ enableWorker: true, lowLatencyMode: true, backBufferLength: 20 });
      let mediaRecoveryAttempted = false;
      state.player.hls = hls;
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        if (token !== state.player.playbackToken) return;
        showPlayerStatus(state.player.kind === "live" ? "Waiting for live video…" : "Waiting for video…");
        video.play().catch(() => {
          if (token !== state.player.playbackToken) return;
          if (state.player.startupTimer) {
            window.clearTimeout(state.player.startupTimer);
            state.player.startupTimer = null;
          }
          showManualPlay();
        });
      });
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        if (token !== state.player.playbackToken || !data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
          failOrTryNext("The stream server could not be reached, is geo-blocked, or rejected browser CORS.", token);
        } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
          if (!mediaRecoveryAttempted) {
            mediaRecoveryAttempted = true;
            try { hls.recoverMediaError(); }
            catch { failOrTryNext("The media format is not supported by this browser.", token); }
          } else {
            failOrTryNext("The media format is not supported by this browser.", token);
          }
        } else {
          failOrTryNext("The stream could not be played.", token);
        }
      });
      return;
    }

    if (state.player.startupTimer) {
      window.clearTimeout(state.player.startupTimer);
      state.player.startupTimer = null;
    }
    showPlayerStatus(state.player.kind === "live"
      ? "This TV browser does not support HLS. Try a compatible IPTV player using the M3U playlist."
      : "This TV browser does not support HLS. Try a supported browser or a direct MP4/WebM source.", false);
  }

  function renderPlayerSources() {
    elements.sourceSelector.innerHTML = state.player.sources.map((url, index) => `<option value="${escapeHtml(url)}">${index === 0 ? "Primary source" : `Alternative source ${index}`} — ${escapeHtml(new URL(url.split("|", 1)[0]).hostname)}</option>`).join("");
    elements.sourceSelectorWrap.hidden = state.player.sources.length <= 1;
  }

  async function openPlayer(item, kind) {
    destroyPlayer();
    elements.videoPlayer.hidden = false;
    elements.copyStreamButton.hidden = false;
    state.player.attemptFailures.clear();
    state.player.previousFocus = document.activeElement;
    state.player.item = item;
    state.player.kind = kind;
    state.player.sources = [...new Set([item.stream_url, ...(item.alternative_stream_urls || [])].filter(Boolean))];
    state.player.sourceIndex = 0;
    elements.playerKindBadge.textContent = kind === "live" ? "LIVE" : displayMediaType(item.media_type).toUpperCase();
    elements.playerKindBadge.classList.toggle("is-vod", kind !== "live");
    elements.playerTitle.textContent = kind === "live" ? item.name : item.title;
    elements.playerMeta.textContent = [
      kind === "live" ? item.category : item.release_year,
      displayLanguage(item.language_code),
      displayCountry(item.country_code),
    ].filter(Boolean).join(" • ");
    elements.playerDescription.textContent = kind === "live"
      ? `Source: ${item.source_name}. Starting live broadcast…`
      : item.synopsis || `Source: ${item.source_name}.`;

    renderPlayerSources();
    elements.playerModal.classList.add("is-open");
    elements.playerModal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    elements.playerCloseButton.focus();
    startPlayback(0);
  }

  function openVidsrcPlayer(item) {
    // Only construct an embed from the exact provider path returned by our API.
    if (!/^https:\/\/vidsrc\.sh\/embed\/(movie|tv)\/tt\d{4,16}$/.test(item.embed_url)) return;
    destroyPlayer();
    state.player.previousFocus = document.activeElement;
    state.player.kind = "vidsrc";
    elements.videoPlayer.hidden = true;
    elements.videoPlaceholder.hidden = true;
    elements.embedPlayer.hidden = false;
    elements.playerTitle.textContent = item.title;
    elements.playerKindBadge.textContent = displayMediaType(item.media_type).toUpperCase();
    elements.playerKindBadge.classList.add("is-vod");
    elements.playerMeta.textContent = "Hosted by VidSrc";
    elements.playerDescription.textContent = "Provider-hosted player; playback availability and rights are not verified. For shows, select a season and episode inside the player.";
    elements.sourceSelectorWrap.hidden = true;
    elements.copyStreamButton.hidden = true;
    elements.playerModal.classList.add("is-open");
    elements.playerModal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    elements.playerCloseButton.focus();
    elements.embedPlayer.src = item.embed_url;
  }

  function closePlayer() {
    destroyPlayer();
    elements.copyStreamButton.hidden = false;
    elements.videoPlayer.hidden = false;
    elements.playerModal.classList.remove("is-open");
    elements.playerModal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    state.player.previousFocus?.focus?.();
  }

  function switchImportMode(mode) {
    state.importMode = mode;
    $$("[data-import-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.importMode === mode));
    $$("[data-import-panel]").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.importPanel === mode));
  }

  function renderImportResult(payload) {
    const hasErrors = payload.failure_count > 0;
    elements.importResult.innerHTML = `
      <div class="result-banner${hasErrors ? " has-errors" : ""}">
        <strong>${hasErrors ? "Import completed with warnings" : "Import completed successfully"}</strong>
        <p>${escapeHtml(payload.source_name)} was processed and the catalog cache was refreshed.</p>
      </div>
      <div class="result-stats">
        <div class="result-stat"><strong>${formatCount(payload.parsed_count)}</strong><span>Parsed</span></div>
        <div class="result-stat"><strong>${formatCount(payload.imported_count)}</strong><span>Saved</span></div>
        <div class="result-stat"><strong>${formatCount(payload.inserted_count)}</strong><span>New</span></div>
        <div class="result-stat"><strong>${formatCount(payload.failure_count)}</strong><span>Failures</span></div>
      </div>
      ${payload.deactivated_count ? `<p class="result-note">${formatCount(payload.deactivated_count)} stale channel record${payload.deactivated_count === 1 ? " was" : "s were"} deactivated.</p>` : ""}
      ${payload.failures.length ? `<div class="failure-list">${payload.failures.map((failure) => `<div class="failure-item"><strong>${escapeHtml(failure.name || failure.stream_url || `Line ${failure.line_number || "unknown"}`)}</strong><span>${escapeHtml(failure.reason)}</span></div>`).join("")}</div>` : ""}`;
  }

  function sourceStatusLabel(status) {
    return { never: "Not synced", running: "Syncing", success: "Healthy", warning: "Warnings", error: "Failed" }[status] || status;
  }

  function formatDate(value) {
    if (!value) return "Never synced";
    try { return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
    catch { return value; }
  }

  function renderSourceCard(source) {
    const status = escapeHtml(source.last_sync_status);
    return `
      <article class="source-card">
        <div class="source-card-main">
          <div class="source-card-heading"><span class="source-status status-${status}"></span><div><h3>${escapeHtml(source.name)}</h3><p title="${escapeHtml(source.url_preview)}">${escapeHtml(source.url_preview)}</p></div></div>
          <div class="source-card-stats">
            <span><strong>${formatCount(source.active_channel_count)}</strong> active</span>
            <span><strong>${formatCount(source.last_parsed_count)}</strong> parsed</span>
            <span><strong>${formatCount(source.last_failure_count)}</strong> ${source.validate_urls ? "rejected" : "failures"}</span>
          </div>
          <div class="source-card-meta"><span class="status-chip status-${status}">${escapeHtml(sourceStatusLabel(source.last_sync_status))}</span><span>${escapeHtml(formatDate(source.last_synced_at))}</span><span>${source.replace_missing ? "Snapshot sync" : "Additive sync"}</span></div>
          ${source.last_error ? `<p class="source-error">${escapeHtml(source.last_error)}</p>` : ""}
        </div>
        <div class="source-actions">
          <button class="secondary-button compact-button" type="button" data-sync-source="${source.id}">Sync now</button>
          <button class="danger-button" type="button" data-delete-source="${source.id}" data-source-name="${escapeHtml(source.name)}">Remove</button>
        </div>
      </article>`;
  }

  async function loadSources() {
    if (!elements.sourceRegistry) return;
    elements.sourceRegistry.innerHTML = '<div class="source-registry-empty">Loading providers…</div>';
    try {
      const sources = await apiFetch("/sources");
      elements.sourceRegistry.innerHTML = sources.length
        ? sources.map(renderSourceCard).join("")
        : '<div class="source-registry-empty">No managed providers yet. Choose Remote URL above, keep “Save as a managed provider” enabled, and import an authorized M3U feed.</div>';
    } catch (error) {
      elements.sourceRegistry.innerHTML = `<div class="source-registry-empty is-error">${escapeHtml(error.message)}</div>`;
    }
  }

  async function syncSource(sourceId, button) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Syncing…";
    try {
      const payload = await apiFetch(`/sources/${sourceId}/sync`, { method: "POST" });
      renderImportResult(payload.result);
      showToast("Provider synchronized", `${payload.result.imported_count} records were saved and ${payload.result.deactivated_count} were deactivated.`);
      await Promise.all([loadSources(), loadFacets()]);
      if (state.view === "live") await loadLive();
    } catch (error) {
      showToast("Provider sync failed", error.message, true);
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function deleteSource(sourceId, sourceName, button) {
    if (!window.confirm(`Remove ${sourceName} and deactivate its catalog records?`)) return;
    button.disabled = true;
    try {
      const payload = await apiFetch(`/sources/${sourceId}`, { method: "DELETE" });
      showToast("Provider removed", `${payload.deactivated_count} catalog records were deactivated.`);
      await Promise.all([loadSources(), loadFacets()]);
    } catch (error) {
      showToast("Could not remove provider", error.message, true);
      button.disabled = false;
    }
  }

  async function submitImport(event) {
    event.preventDefault();
    elements.importButton.disabled = true;
    $("span", elements.importButton).textContent = "Importing…";
    try {
      let options;
      if (state.importMode === "file") {
        const file = elements.playlistFile.files[0];
        if (!file) throw new Error("Choose an M3U file before importing.");
        const body = new FormData();
        body.append("file", file);
        body.append("source_name", elements.sourceName.value);
        body.append("default_language_code", elements.defaultLanguage.value || "und");
        body.append("default_country_code", elements.defaultCountry.value || "ZZ");
        body.append("default_category", elements.defaultCategory.value || "Uncategorized");
        body.append("validate_urls", String(elements.validateUrls.checked));
        options = { method: "POST", body };
      } else if (state.importMode === "url" && elements.saveRemoteSource.checked) {
        const source = await apiFetch("/sources", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: elements.sourceName.value,
            playlist_url: elements.sourceUrl.value.trim(),
            default_language_code: elements.defaultLanguage.value || "und",
            default_country_code: elements.defaultCountry.value || "ZZ",
            default_category: elements.defaultCategory.value || "Uncategorized",
            validate_urls: elements.validateUrls.checked,
            replace_missing: elements.replaceMissing.checked,
          }),
        });
        const synced = await apiFetch(`/sources/${source.id}/sync`, { method: "POST" });
        renderImportResult(synced.result);
        showToast("Provider added", `${synced.result.imported_count} records were saved.`);
        await Promise.all([loadFacets(), loadSources()]);
        return;
      } else {
        const payload = {
          source_name: elements.sourceName.value,
          default_language_code: elements.defaultLanguage.value || "und",
          default_country_code: elements.defaultCountry.value || "ZZ",
          default_category: elements.defaultCategory.value || "Uncategorized",
          validate_urls: elements.validateUrls.checked,
        };
        if (state.importMode === "url") payload.source_url = elements.sourceUrl.value.trim();
        if (state.importMode === "text") payload.raw_m3u = elements.rawM3u.value;
        options = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
      }
      const result = await apiFetch("/import-m3u", options);
      renderImportResult(result);
      showToast("Catalog updated", `${result.imported_count} records were saved.`);
      await loadFacets();
      await loadSources();
    } catch (error) {
      showToast("Import failed", error.message, true);
      elements.importResult.innerHTML = `<div class="empty-result"><svg viewBox="0 0 24 24"><path d="M12 8v5m0 3h.01"/><circle cx="12" cy="12" r="9"/></svg><h3>Import failed</h3><p>${escapeHtml(error.message)}</p></div>`;
    } finally {
      elements.importButton.disabled = false;
      $("span", elements.importButton).textContent = "Import into catalog";
    }
  }

  function bindEvents() {
    $$("[data-view-target]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.viewTarget)));
    elements.mobileMenuButton.addEventListener("click", openSidebar);
    elements.sidebarBackdrop.addEventListener("click", closeSidebar);
    elements.refreshLiveButton.addEventListener("click", async () => { await loadFacets(); await loadLive(); showToast("Live catalog refreshed", "The latest catalog metadata is displayed."); });
    elements.refreshVodButton.addEventListener("click", async () => { await loadFacets(); await Promise.all([loadVod(), loadVidsrc(), loadImdb()]); showToast("VOD library refreshed", "The latest stored catalog metadata is displayed."); });
    elements.syncVidsrcButton.addEventListener("click", syncVidsrc);
    elements.vidsrcTypeFilter.addEventListener("change", () => { state.vidsrc.mediaType = elements.vidsrcTypeFilter.value; state.vidsrc.page = 1; loadVidsrc(); });
    elements.imdbAvailabilityFilter.addEventListener("change", () => { state.imdb.matchedOnly = elements.imdbAvailabilityFilter.value === "matched"; state.imdb.page = 1; loadImdb(); });
    elements.refreshSourcesButton.addEventListener("click", loadSources);
    elements.vodImportForm.addEventListener("submit", submitVodImport);

    [elements.liveLanguageFilter, elements.liveCountryFilter, elements.liveCategoryFilter, elements.liveSort].forEach((element) => element.addEventListener("change", updateLiveFilters));
    [elements.vodLanguageFilter, elements.vodCountryFilter, elements.vodTypeFilter, elements.vodSort].forEach((element) => element.addEventListener("change", updateVodFilters));

    const search = debounce(() => {
      if (state.view === "live" || state.view === "india") { state.live.search = elements.globalSearch.value.trim(); state.live.page = 1; loadLive(); }
      if (state.view === "vod") { state.vod.search = elements.globalSearch.value.trim(); state.vod.page = 1; state.vidsrc.page = 1; state.imdb.page = 1; loadVod(); loadVidsrc(); loadImdb(); }
    });
    elements.globalSearch.addEventListener("input", search);
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); elements.globalSearch.focus(); }
      if (["Escape", "BrowserBack"].includes(event.key) || (event.key === "Backspace" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName))) {
        if (elements.playerModal.classList.contains("is-open")) { event.preventDefault(); closePlayer(); return; }
        if (elements.sidebar.classList.contains("is-open")) { event.preventDefault(); closeSidebar(); elements.mobileMenuButton.focus(); return; }
      }
      if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key) ||
        ["TEXTAREA", "VIDEO", "IFRAME"].includes(document.activeElement?.tagName) ||
        (document.activeElement?.tagName === "SELECT" && ["ArrowUp", "ArrowDown"].includes(event.key)) ||
        (document.activeElement?.tagName === "INPUT" && ["ArrowLeft", "ArrowRight"].includes(event.key))) return;
      document.body.classList.add("tv-remote");
      const modalOpen = elements.playerModal.classList.contains("is-open");
      const menuOpen = elements.sidebar.classList.contains("is-open");
      const scope = modalOpen ? elements.playerModal : menuOpen ? elements.sidebar : document;
      const targets = [...scope.querySelectorAll("button:not(:disabled), a[href], select:not(:disabled), input:not(:disabled)")].filter((node) =>
        node.getClientRects().length && !node.closest("[hidden]") &&
        (modalOpen || menuOpen || (!node.closest(".player-modal") &&
          (!node.closest(".sidebar") || window.matchMedia("(min-width: 981px)").matches) &&
          (!node.closest(".workspace") || node.closest(".workspace.is-active")))));
      const current = document.activeElement;
      const rect = current?.getBoundingClientRect?.();
      if (!rect || !targets.includes(current)) {
        const start = modalOpen ? elements.playerCloseButton : menuOpen ? $(".nav-item.is-active", elements.sidebar) :
          window.matchMedia("(min-width: 981px)").matches ? $(".nav-item.is-active", elements.sidebar) : elements.mobileMenuButton;
        if (start) { event.preventDefault(); start.focus(); start.scrollIntoView({ block: "nearest" }); }
        return;
      }
      const center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
      const forward = event.key === "ArrowRight" || event.key === "ArrowDown";
      const candidates = targets.filter((node) => {
        if (node === current) return false;
        const box = node.getBoundingClientRect();
        const delta = vertical ? box.top + box.height / 2 - center.y : box.left + box.width / 2 - center.x;
        return forward ? delta > 3 : delta < -3;
      });
      candidates.sort((left, right) => {
        const score = (node) => {
          const box = node.getBoundingClientRect();
          const dx = box.left + box.width / 2 - center.x;
          const dy = box.top + box.height / 2 - center.y;
          return (vertical ? Math.abs(dy) * 2 + Math.abs(dx) * 3 : Math.abs(dx) * 2 + Math.abs(dy) * 3);
        };
        return score(left) - score(right);
      });
      if (candidates[0]) { event.preventDefault(); candidates[0].focus(); candidates[0].scrollIntoView({ block: "nearest", inline: "nearest" }); }
    });

    document.addEventListener("click", (event) => {
      const facet = event.target.closest("[data-facet-type]");
      if (facet) {
        const type = facet.dataset.facetType;
        const value = facet.dataset.facetValue;
        if (type === "live-language") { state.live.language = state.live.language === value ? "" : value; state.live.page = 1; syncFilterControls(); loadFacets(); loadLive(); }
        if (type === "live-category") { state.live.category = state.live.category === value ? "" : value; state.live.page = 1; syncFilterControls(); loadFacets(); loadLive(); }
        if (type === "vod-country") { state.vod.country = state.vod.country === value ? "" : value; state.vod.page = 1; syncFilterControls(); loadFacets(); loadVod(); }
        if (type === "vod-language") { state.vod.language = state.vod.language === value ? "" : value; state.vod.page = 1; syncFilterControls(); loadFacets(); loadVod(); }
        if (type === "vod-type") { state.vod.mediaType = state.vod.mediaType === value ? "" : value; state.vod.page = 1; syncFilterControls(); loadFacets(); loadVod(); }
      }
      const pageButton = event.target.closest("[data-page-target]");
      if (pageButton && !pageButton.disabled) {
        const target = pageButton.dataset.pageTarget;
        state[target].page = Number(pageButton.dataset.page);
        if (target === "live") loadLive();
        else if (target === "vidsrc") loadVidsrc();
        else if (target === "imdb") loadImdb();
        else loadVod();
        window.scrollTo({ top: 250, behavior: "smooth" });
      }
      const channelButton = event.target.closest("[data-play-channel]") || event.target.closest(".channel-card")?.querySelector("[data-play-channel]");
      if (channelButton) {
        const item = (state.live.items || []).find((entry) => entry.id === Number(channelButton.dataset.playChannel));
        if (item) openPlayer(item, "live");
      }
      const vodButton = event.target.closest("[data-play-vod]") || event.target.closest(".vod-card")?.querySelector("[data-play-vod]");
      if (vodButton) {
        const item = (state.vod.items || []).find((entry) => entry.id === Number(vodButton.dataset.playVod));
        if (item) openPlayer(item, "vod");
      }
      const vidsrcButton = event.target.closest("[data-play-vidsrc]");
      if (vidsrcButton) {
        const item = state.vidsrc.items.find((entry) => entry.id === Number(vidsrcButton.dataset.playVidsrc));
        if (item) openVidsrcPlayer(item);
      }
      const imdbButton = event.target.closest("[data-play-imdb]");
      if (imdbButton) {
        const item = state.imdb.items.find((entry) => entry.imdb_id === imdbButton.dataset.playImdb);
        if (item?.embed_url) openVidsrcPlayer({ ...item, media_type: "movie" });
      }
      if (event.target.closest("[data-close-player]")) closePlayer();
      const syncButton = event.target.closest("[data-sync-source]");
      if (syncButton) syncSource(syncButton.dataset.syncSource, syncButton);
      const deleteButton = event.target.closest("[data-delete-source]");
      if (deleteButton) deleteSource(deleteButton.dataset.deleteSource, deleteButton.dataset.sourceName, deleteButton);
      const clearLive = event.target.closest("[data-clear-live]");
      if (clearLive) { state.live[clearLive.dataset.clearLive] = ""; state.live.page = 1; syncFilterControls(); loadFacets(); loadLive(); }
      if (event.target.closest("[data-clear-vod]")) { state.vod.language = ""; state.vod.country = ""; state.vod.mediaType = ""; state.vod.page = 1; syncFilterControls(); loadFacets(); loadVod(); }
    });

    $$("[data-import-mode]").forEach((button) => button.addEventListener("click", () => switchImportMode(button.dataset.importMode)));
    elements.playlistFile.addEventListener("change", () => { elements.selectedFileName.textContent = elements.playlistFile.files[0]?.name || "or drag and drop a file here"; });
    ["dragenter", "dragover"].forEach((name) => elements.dropzone.addEventListener(name, (event) => { event.preventDefault(); elements.dropzone.classList.add("is-dragging"); }));
    ["dragleave", "drop"].forEach((name) => elements.dropzone.addEventListener(name, (event) => { event.preventDefault(); elements.dropzone.classList.remove("is-dragging"); }));
    elements.dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer.files.length) {
        elements.playlistFile.files = event.dataTransfer.files;
        elements.selectedFileName.textContent = event.dataTransfer.files[0].name;
      }
    });
    elements.importForm.addEventListener("submit", submitImport);
    elements.sourceSelector.addEventListener("change", () => {
      const selectedIndex = state.player.sources.indexOf(elements.sourceSelector.value);
      startPlayback(selectedIndex >= 0 ? selectedIndex : 0);
    });
    elements.resumePlayerButton.addEventListener("click", () => {
      elements.videoPlayer.play().then(() => {
        if (elements.videoPlayer.readyState >= 2) hidePlayerStatus();
        else showPlayerStatus(state.player.kind === "live" ? "Waiting for live video…" : "Waiting for video…");
      }).catch(() => showManualPlay());
    });
    elements.copyStreamButton.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(elements.sourceSelector.value || state.player.sources[0]); showToast("Copied", "The stream URL is now on your clipboard."); }
      catch { showToast("Copy failed", "Your browser did not allow clipboard access.", true); }
    });
    elements.fullscreenButton.addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else (state.player.kind === "vidsrc" ? elements.embedPlayer : elements.videoPlayer).requestFullscreen?.().catch(() => showToast("Full screen unavailable", "Your TV browser does not support fullscreen video.", true));
    });
  }

  async function init() {
    cacheElements();
    bindEvents();
    const initialView = ["india", "vod", "import"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "live";
    await loadFacets();
    await loadSources();
    switchView(initialView);
  }

  document.addEventListener("DOMContentLoaded", init);
})();