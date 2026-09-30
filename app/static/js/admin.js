(() => {
  "use strict";
  let key = "";
  let pendingAction = null;
  let actionOrigin = null;
  let customers = [];
  const $ = (id) => document.getElementById(id);
  const status = (message, error = false) => {
    $("adminStatus").textContent = message;
    $("adminStatus").classList.toggle("is-error", error);
  };
  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      cache: "no-store",
      headers: { "X-Admin-Key": key, ...(options.body ? { "Content-Type": "application/json" } : {}) },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === "string" ? error.detail : `HTTP ${response.status}`);
    }
    return response.json();
  }
  function showLink(playlistUrl, portalUrl, magPortalUrl) {
    const container = $("newLink");
    container.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = "Save all customer URLs now: they cannot be retrieved after this page is closed. Replacing them disables the old URLs.";
    container.append(title);
    for (const [label, copyLabel, url] of [["MAG external portal URL (set on the MAG box; requires the recorded MAC)", "MAG portal", magPortalUrl], ["TV browser URL (not a MAG portal)", "TV browser", portalUrl], ["M3U playlist URL (for M3U players)", "M3U", playlistUrl]]) {
      const heading = document.createElement("p");
      heading.textContent = label;
      const link = document.createElement("a");
      link.href = url;
      link.textContent = url;
      link.rel = "noreferrer";
      const copy = document.createElement("button");
      copy.className = "secondary-button";
      copy.type = "button";
      copy.textContent = `Copy ${copyLabel} URL`;
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(url); status("Customer URL copied"); }
        catch { status("Copy failed; select and copy the URL above", true); }
      });
      container.append(heading, link, copy);
    }
    container.hidden = false;
    container.querySelector("button")?.focus();
    container.scrollIntoView({ block: "nearest" });
  }
  function closeAction(restoreFocus = true) {
    const origin = actionOrigin;
    pendingAction = null;
    actionOrigin = null;
    $("subscriberAction").hidden = true;
    $("actionMonthsPreset").value = "1";
    $("actionCustomLabel").hidden = true;
    $("actionCustomMonths").disabled = true;
    $("actionMacLabel").hidden = true;
    $("actionMac").disabled = true;
    if (restoreFocus && origin?.isConnected) origin.focus();
  }
  function beginAction(button) {
    closeAction(false);
    pendingAction = { id: Number(button.dataset.id), action: button.dataset.action, active: button.dataset.active === "true" };
    actionOrigin = button;
    const name = button.closest(".subscriber-row").querySelector("strong").textContent;
    const { action, active } = pendingAction;
    $("actionTitle").textContent = `${action === "extend" ? "Extend" : action === "mac" ? "Update MAC for" : action === "rotate" ? "Replace links for" : active ? "Suspend" : "Activate"} ${name}?`;
    $("actionDescription").textContent = action === "rotate"
      ? "All three old URLs will stop working immediately. Copy all three replacement URLs after confirming."
      : action === "mac" ? "The MAG portal compares the reported MAC against this value. A MAC can be spoofed; changing it does not prove device identity."
      : action === "extend" ? "Add calendar months to the current expiry (or from today if expired)."
        : active ? "This stops future portal, API and playlist requests, not previously copied stream URLs." : "This restores portal and playlist requests until the expiry date.";
    $("actionMonthsLabel").hidden = action !== "extend";
    $("actionMacLabel").hidden = action !== "mac";
    $("actionMac").disabled = action !== "mac";
    if (action === "mac") $("actionMac").value = customers.find((row) => row.id === pendingAction.id)?.mac_address || "";
    $("confirmAction").textContent = action === "rotate" ? "Replace links" : action === "mac" ? "Save MAC" : action === "extend" ? "Extend account" : active ? "Suspend account" : "Activate account";
    $("subscriberAction").hidden = false;
    (action === "extend" ? $("actionMonthsPreset") : action === "mac" ? $("actionMac") : $("confirmAction")).focus();
    $("subscriberAction").scrollIntoView({ block: "nearest" });
  }
  async function load() {
    customers = await request("/admin/subscribers");
    $("customerCount").textContent = String(customers.length);
    $("activeCount").textContent = String(customers.filter((row) => row.is_active && Date.parse(row.expires_at) > Date.now()).length);
    $("todayDate").textContent = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date());
    renderCustomers();
  }
  function renderCustomers() {
    const search = $("subscriberSearch").value.trim().toLowerCase();
    const rows = customers.filter((row) => `${row.name} ${row.mac_address || ""}`.toLowerCase().includes(search));
    $("subscriberList").innerHTML = rows.length ? rows.map((row) => {
      const expired = Date.parse(row.expires_at) <= Date.now();
      return `<article class="subscriber-row"><div><strong>${escapeHtml(row.name)}</strong><small>MAC: ${escapeHtml(row.mac_address || "Not recorded")} · ${row.is_active ? expired ? "Expired" : "Active" : "Inactive"} · Expires ${escapeHtml(new Date(row.expires_at).toLocaleString())}${row.notes ? ` · ${escapeHtml(row.notes)}` : ""}</small></div><div class="subscriber-actions"><button class="secondary-button" data-action="extend" data-id="${row.id}" type="button">Extend</button><button class="secondary-button" data-action="toggle" data-id="${row.id}" data-active="${row.is_active}" type="button">${row.is_active ? "Suspend" : "Activate"}</button><button class="secondary-button" data-action="mac" data-id="${row.id}" type="button">Edit MAC</button><button class="secondary-button" data-action="rotate" data-id="${row.id}" type="button">Replace URLs</button></div></article>`;
    }).join("") : "No matching customers.";
  }
  $("unlockButton").addEventListener("click", async () => {
    key = $("adminKey").value;
    $("adminKey").value = "";
    try { await load(); $("adminContent").hidden = false; status("Administration unlocked for this tab"); $("subscriberName").focus(); }
    catch (error) { key = ""; $("adminContent").hidden = true; status(error.message, true); }
  });
  $("lockButton").addEventListener("click", () => { key = ""; customers = []; closeAction(false); $("adminContent").hidden = true; $("subscriberList").replaceChildren(); $("newLink").replaceChildren(); $("newLink").hidden = true; status("Locked"); $("adminKey").focus(); });
  $("adminKey").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); $("unlockButton").click(); } });
  $("subscriberMonths").addEventListener("change", () => {
    const custom = $("subscriberMonths").value === "custom";
    $("subscriberCustomLabel").hidden = !custom;
    $("subscriberCustomMonths").disabled = !custom;
    if (custom) $("subscriberCustomMonths").focus();
  });
  $("subscriberForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await request("/admin/subscribers", { method: "POST", body: JSON.stringify({
        name: $("subscriberName").value, mac_address: $("subscriberMac").value || null,
        months: Number($("subscriberMonths").value === "custom" ? $("subscriberCustomMonths").value : $("subscriberMonths").value), notes: $("subscriberNotes").value || null,
        is_active: $("subscriberActive").checked,
      }) });
      $("subscriberForm").reset();
      $("subscriberCustomLabel").hidden = true;
      $("subscriberCustomMonths").disabled = true;
      showLink(result.playlist_url, result.portal_url, result.mag_portal_url);
      status(result.is_active ? "Customer activated. Share the URLs privately." : "Customer created inactive. Select Activate before sharing the URLs.");
      await load();
    } catch (error) { status(error.message, true); }
  });
  $("refreshSubscribers").addEventListener("click", () => { closeAction(false); load().catch((error) => status(error.message, true)); });
  $("subscriberSearch").addEventListener("input", () => { closeAction(false); renderCustomers(); });
  $("subscriberList").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    beginAction(button);
  });
  $("actionMonthsPreset").addEventListener("change", () => {
    const custom = $("actionMonthsPreset").value === "custom";
    $("actionCustomLabel").hidden = !custom;
    $("actionCustomMonths").disabled = !custom;
    if (custom) $("actionCustomMonths").focus();
  });
  $("cancelAction").addEventListener("click", () => closeAction());
  $("subscriberAction").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pendingAction) return;
    const { id, action, active } = pendingAction;
    const confirm = $("confirmAction");
    confirm.disabled = true;
    try {
      let links = null;
      if (action === "extend") {
        const months = Number($("actionMonthsPreset").value === "custom" ? $("actionCustomMonths").value : $("actionMonthsPreset").value);
        if (!Number.isInteger(months) || months < 1 || months > 120) throw new Error("Enter 1 to 120 months");
        await request(`/admin/subscribers/${id}`, { method: "PATCH", body: JSON.stringify({ extend_months: months }) });
      } else if (action === "toggle") {
        await request(`/admin/subscribers/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: !active }) });
      } else if (action === "mac") {
        await request(`/admin/subscribers/${id}`, { method: "PATCH", body: JSON.stringify({ mac_address: $("actionMac").value || null }) });
      } else if (action === "rotate") {
        const result = await request(`/admin/subscribers/${id}/rotate`, { method: "POST" });
        links = result;
      }
      closeAction(false);
      if (links) showLink(links.playlist_url, links.portal_url, links.mag_portal_url);
      await load();
      status(links ? "URLs replaced. Copy all three private URLs now." : "Customer updated");
      if (links) $("newLink").querySelector("button")?.focus();
      else $("subscriberList").querySelector(`[data-id="${id}"][data-action="${action}"]`)?.focus();
    } catch (error) { status(error.message, true); confirm.focus(); }
    finally { confirm.disabled = false; }
  });
  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const actionOpen = !$("subscriberAction").hidden;
    const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (actionOpen && (["Escape", "BrowserBack"].includes(event.key) || (event.key === "Backspace" && !editing))) { event.preventDefault(); closeAction(); return; }
    if (actionOpen && document.activeElement === $("actionMonthsPreset") && event.key === "ArrowRight") {
      event.preventDefault(); $("confirmAction").focus(); return;
    }
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key) ||
      (editing && (["TEXTAREA"].includes(document.activeElement.tagName) ||
        (document.activeElement.tagName === "INPUT" && ["ArrowLeft", "ArrowRight"].includes(event.key)) ||
        (document.activeElement.tagName === "SELECT" && ["ArrowUp", "ArrowDown"].includes(event.key))))) return;
    const scope = actionOpen ? $("subscriberAction") : document;
    const targets = [...scope.querySelectorAll("button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled)")]
      .filter((node) => node.getClientRects().length && !node.closest("[hidden]"));
    const current = document.activeElement;
    const rect = current?.getBoundingClientRect();
    if (!targets.includes(current)) { if (targets[0]) { event.preventDefault(); targets[0].focus(); } return; }
    const center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
    const forward = event.key === "ArrowDown" || event.key === "ArrowRight";
    const candidates = targets.filter((node) => {
      if (node === current) return false;
      const box = node.getBoundingClientRect();
      const delta = vertical ? box.top + box.height / 2 - center.y : box.left + box.width / 2 - center.x;
      return forward ? delta > 3 : delta < -3;
    });
    candidates.sort((a, b) => {
      const score = (node) => {
        const box = node.getBoundingClientRect();
        const dx = box.left + box.width / 2 - center.x;
        const dy = box.top + box.height / 2 - center.y;
        return vertical ? Math.abs(dy) * 2 + Math.abs(dx) : Math.abs(dx) * 2 + Math.abs(dy);
      };
      return score(a) - score(b);
    });
    if (candidates[0]) { event.preventDefault(); candidates[0].focus(); candidates[0].scrollIntoView({ block: "nearest", inline: "nearest" }); }
  });
})();