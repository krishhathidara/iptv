(function () {
  "use strict";
  var stb = window.gSTB;
  var message = document.getElementById("message");
  var list = document.getElementById("channels");
  var loadMore = document.getElementById("loadMore");
  var liveTab = document.getElementById("liveTab");
  var movieTab = document.getElementById("movieTab");
  var seriesTab = document.getElementById("seriesTab");
  var playing = document.getElementById("playing");
  var mac, session, channels = [], current = -1, page = 0, loading = false, section = "itv", generation = 0;
  // Private legacy pages use their private API; the shared page uses the
  // common MAC-authenticated endpoint. Nothing is granted by loading this page.
  var portalRoot = location.pathname.split("/c/")[0];
  var shared = portalRoot === "" || portalRoot === "/stalker_portal";
  var base = shared ? "/server/load.php" : portalRoot + "/server/load.php";

  function report(text) { message.textContent = text; }
  function api(type, action, extra, done) {
    var xhr = new XMLHttpRequest();
    var url = base + "?type=" + encodeURIComponent(type) + "&action=" + encodeURIComponent(action) + (extra || "");
    xhr.open("GET", url, true);
    xhr.setRequestHeader("X-Device-Mac", mac);
    if (session) xhr.setRequestHeader("Authorization", "Bearer " + session);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      if (xhr.status !== 200) {
        if (action === "get_ordered_list") loading = false;
        if (xhr.status === 403) report("Authorization failed (403). Check that the STBEmu profile MAC matches the active customer's saved MAC; then reload the portal.");
        else if (xhr.status === 404) report(shared ? "Shared portal not enabled (404), or title not found. Check the TV deployment." : "Portal or title not found (404). Check that the customer is active and the private URL has not been replaced.");
        else report("Portal request failed (" + xhr.status + "). Check the TV service and reload the portal.");
        return;
      }
      try { done(JSON.parse(xhr.responseText).js); }
      catch (error) { if (action === "get_ordered_list") loading = false; report("Invalid portal response."); }
    };
    xhr.send();
  }
  function stop() {
    try { if (typeof stb.Stop === "function") stb.Stop(); } catch (error) { report("Unable to stop the player."); }
    current = -1;
    playing.textContent = "Choose a channel to start playing.";
  }
  function play(index) {
    var channel = channels[index];
    if (!channel) return;
    api(section, "create_link", "&cmd=" + encodeURIComponent(channel.cmd), function (result) {
      var cmd = result.cmd || "";
      var url = cmd.indexOf("ffmpeg ") === 0 ? cmd.slice(7) : "";
      if (!/^https?:\/\//i.test(url)) { report("This stream format is not supported by this portal."); return; }
      try {
        if (typeof stb.Stop === "function") stb.Stop();
        // MAG video is a separate plane behind WebKit. Key out the viewer color
        // in the graphics plane or an opaque web page hides the playing video.
        if (typeof stb.SetTransparentColor === "function") stb.SetTransparentColor(0x010203);
        if (typeof stb.SetWinMode === "function") stb.SetWinMode(0, 1);
        // state 0 is a reduced video window; state 1 covers the entire UI.
        if (typeof stb.SetPIG === "function") stb.SetPIG(0, 150, Math.floor(window.innerWidth * 0.39), 75);
        stb.Play(cmd);
        current = index;
        playing.textContent = "Playing: " + channel.name + " — press Back to stop";
        report("Playing " + channel.name + ". Availability and codecs depend on the stream and your MAG model.");
      } catch (error) { report("Device could not start playback: " + error.message); }
    });
  }
  function render(from, total) {
    for (var i = from; i < channels.length; i++) {
      (function (index) {
        var item = document.createElement("li");
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = (channels[index].number || index + 1) + ". " + channels[index].name;
        button.onclick = function () { play(index); };
        item.appendChild(button);
        list.appendChild(item);
      })(i);
    }
    loadMore.hidden = channels.length >= total;
    var label = section === "vod" ? " movies" : section === "series" ? " TV episodes" : " live channels";
    report(channels.length ? channels.length + " of " + total + label + " ready" :
      (section === "vod" ? "No movies available." : section === "series" ? "No TV episodes available." : "No active channels available."));
    if (from === 0 && list.querySelector("button")) list.querySelector("button").focus();
  }
  function nextPage() {
    if (loading) return;
    loading = true;
    var requestedSection = section, requestedGeneration = generation;
    api(requestedSection, "get_ordered_list", "&p=" + (page + 1), function (result) {
      if (requestedGeneration !== generation) return;
      var start = channels.length;
      channels = channels.concat(result.data || []);
      page++;
      loading = false;
      render(start, result.total_items || 0);
      if (start > 0 && channels.length > start) list.getElementsByTagName("button")[start].focus();
    });
  }
  function selectSection(type) {
    stop();
    generation++;
    section = type;
    channels = [];
    page = 0;
    loading = false;
    list.innerHTML = "";
    loadMore.hidden = true;
    report("Loading " + (type === "vod" ? "movies" : type === "series" ? "TV episodes" : "live TV") + "…");
    nextPage();
  }
  liveTab.onclick = function () { selectSection("itv"); };
  movieTab.onclick = function () { selectSection("vod"); };
  seriesTab.onclick = function () { selectSection("series"); };
  loadMore.onclick = nextPage;
  document.onkeydown = function (event) {
    var key = event.keyCode || event.which;
    if (key === 8 || key === 27 || key === 461) { event.preventDefault(); if (current !== -1) stop(); return; }
    if (key !== 38 && key !== 40) return;
    var buttons = [liveTab, movieTab, seriesTab].concat(Array.prototype.slice.call(list.getElementsByTagName("button")));
    if (!loadMore.hidden) buttons.push(loadMore);
    if (!buttons.length) return;
    var focused = -1;
    for (var i = 0; i < buttons.length; i++) if (buttons[i] === document.activeElement) focused = i;
    buttons[Math.max(0, Math.min(buttons.length - 1, focused + (key === 40 ? 1 : -1)))].focus();
    event.preventDefault();
  };
  if (!stb || typeof stb.GetDeviceMacAddress !== "function" || typeof stb.Play !== "function") {
    report("Open this URL as an external portal on a compatible MAG box (gSTB required). A regular browser cannot play through this portal.");
    return;
  }
  try {
    mac = String(stb.GetDeviceMacAddress()).toUpperCase();
    if (typeof stb.InitPlayer === "function") stb.InitPlayer();
  } catch (error) { report("Could not initialize MAG hardware: " + error.message); return; }
  api("stb", "handshake", "", function (result) {
    session = result.token;
    nextPage();
  });
})();