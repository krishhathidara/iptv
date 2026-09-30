(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const token = location.pathname.match(/^\/watch\/([A-Za-z0-9_-]{32,128})$/)?.[1];
  let items = [];
  let hls = null;
  let previousFocus = null;

  function stop() {
    if (hls) { hls.destroy(); hls = null; }
    $("watchVideo").pause();
    $("watchVideo").removeAttribute("src");
    $("watchVideo").load();
    $("watchPlayer").hidden = true;
    previousFocus?.focus?.();
  }
  function parsePlaylist(text) {
    const lines = text.replaceAll("\r", "").split("\n");
    const result = [];
    let title = "";
    let group = "Live TV";
    for (const line of lines) {
      if (line.startsWith("#EXTINF:")) {
        title = line.slice(line.indexOf(",") + 1).trim();
        group = line.match(/group-title="([^"]*)"/)?.[1] || "Live TV";
      } else if (title && /^https?:\/\//i.test(line.trim())) {
        result.push({ title, group, url: line.trim() });
        title = "";
      }
    }
    return result;
  }
  function render() {
    const search = $("watchSearch").value.trim().toLocaleLowerCase();
    const filtered = items.filter((item) => `${item.title} ${item.group}`.toLocaleLowerCase().includes(search));
    const list = $("watchList");
    list.replaceChildren();
    for (const item of filtered) {
      const button = document.createElement("button");
      button.className = "watch-item";
      button.type = "button";
      button.textContent = item.title;
      const label = document.createElement("span");
      label.textContent = item.group;
      button.append(label);
      button.addEventListener("click", () => play(item));
      list.append(button);
    }
    $("watchStatus").textContent = filtered.length ? `${filtered.length} title${filtered.length === 1 ? "" : "s"} available` : "No matching titles. Try another search.";
  }
  async function load() {
    stop();
    $("watchStatus").textContent = "Loading your channels…";
    try {
      if (!token) throw new Error("Invalid portal URL");
      const response = await fetch(`/subscribers/playlist/${token}.m3u`, { cache: "no-store", referrerPolicy: "no-referrer" });
      if (!response.ok) throw new Error("Account inactive, expired, or link replaced. Contact your provider.");
      items = parsePlaylist(await response.text());
      render();
      if (!items.length) $("watchStatus").textContent = "No channels or direct VOD links are available yet. Contact your provider.";
    } catch (error) { items = []; $("watchList").replaceChildren(); $("watchStatus").textContent = error.message; }
  }
  function play(item) {
    stop();
    previousFocus = document.activeElement;
    const video = $("watchVideo");
    $("watchTitle").textContent = item.title;
    $("watchPlayer").hidden = false;
    $("playerStatus").textContent = "Connecting to the stream…";
    $("closePlayer").focus();
    $("watchPlayer").scrollIntoView({ block: "start" });
    const start = () => video.play().then(() => { $("playerStatus").textContent = "Playing"; })
      .catch(() => { $("playerStatus").textContent = "Press Play on your remote to start."; });
    if (/\.m3u8(?:[?#]|$)/i.test(item.url) && video.canPlayType("application/vnd.apple.mpegurl")) video.src = item.url;
    else if (/\.m3u8(?:[?#]|$)/i.test(item.url) && window.Hls?.isSupported()) {
      hls = new Hls();
      hls.loadSource(item.url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, start);
      hls.on(Hls.Events.ERROR, (_, error) => { if (error.fatal) $("playerStatus").textContent = "Stream unavailable on this device."; });
      return;
    } else video.src = item.url;
    start();
  }
  $("refresh").addEventListener("click", load);
  $("closePlayer").addEventListener("click", stop);
  $("watchSearch").addEventListener("input", render);
  $("watchVideo").addEventListener("error", () => {
    if (!$("watchPlayer").hidden) $("playerStatus").textContent = "Stream unavailable on this device or network.";
  });
  document.addEventListener("keydown", (event) => {
    if (["Escape", "BrowserBack"].includes(event.key) && !$("watchPlayer").hidden) { event.preventDefault(); stop(); return; }
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key) || ["INPUT", "VIDEO"].includes(document.activeElement?.tagName)) return;
    const targets = [...document.querySelectorAll(".watch-shell button:not([disabled]), .watch-search input")].filter((node) => node.getClientRects().length && !node.closest("[hidden]"));
    const current = document.activeElement;
    if (!targets.includes(current)) { targets[0]?.focus(); event.preventDefault(); return; }
    const origin = current.getBoundingClientRect();
    const x = origin.left + origin.width / 2;
    const y = origin.top + origin.height / 2;
    const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
    const forward = event.key === "ArrowDown" || event.key === "ArrowRight";
    const candidates = targets.filter((node) => {
      if (node === current) return false;
      const rect = node.getBoundingClientRect();
      const delta = vertical ? rect.top + rect.height / 2 - y : rect.left + rect.width / 2 - x;
      return forward ? delta > 3 : delta < -3;
    }).sort((a, b) => {
      const score = (node) => {
        const rect = node.getBoundingClientRect();
        const dx = rect.left + rect.width / 2 - x;
        const dy = rect.top + rect.height / 2 - y;
        return vertical ? Math.abs(dy) * 2 + Math.abs(dx) : Math.abs(dx) * 2 + Math.abs(dy);
      };
      return score(a) - score(b);
    });
    if (candidates[0]) { event.preventDefault(); candidates[0].focus(); candidates[0].scrollIntoView({ block: "nearest" }); }
  });
  load();
})();