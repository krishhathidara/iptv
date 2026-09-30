(function () {
  "use strict";
  var stb = window.gSTB;
  var message = document.getElementById("message");
  var list = document.getElementById("channels");
  var loadMore = document.getElementById("loadMore");
  var playing = document.getElementById("playing");
  var mac, session, channels = [], current = -1, page = 0, loading = false;
  var base = location.pathname.split("/c/")[0] + "/server/load.php";

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
        report("Portal request failed (" + xhr.status + "). Check activation and MAC; reload to reconnect.");
        return;
      }
      try { done(JSON.parse(xhr.responseText).js); }
      catch (error) { if (action === "get_ordered_list") loading = false; report("Invalid portal response."); }
    };
    xhr.send();
  }
  function stop() {
    try { stb.Stop(); } catch (error) { report("Unable to stop the player."); }
    current = -1;
    playing.textContent = "Choose a channel to start playing.";
  }
  function play(index) {
    var channel = channels[index];
    if (!channel) return;
    api("itv", "create_link", "&cmd=" + encodeURIComponent(channel.cmd), function (result) {
      var cmd = result.cmd || "";
      var url = cmd.indexOf("ffmpeg ") === 0 ? cmd.slice(7) : "";
      if (!/^https?:\/\//i.test(url)) { report("This stream format is not supported by this portal."); return; }
      try {
        stb.Stop();
        // MAG video is a separate plane behind WebKit. Key out the viewer color
        // in the graphics plane or an opaque web page hides the playing video.
        stb.SetTransparentColor(0x010203);
        stb.SetWinMode(0, 1);
        // state 0 is a reduced video window; state 1 covers the entire UI.
        stb.SetPIG(0, 150, Math.floor(window.innerWidth * 0.39), 75);
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
        button.textContent = channels[index].number + ". " + channels[index].name;
        button.onclick = function () { play(index); };
        item.appendChild(button);
        list.appendChild(item);
      })(i);
    }
    loadMore.hidden = channels.length >= total;
    report(channels.length ? channels.length + " of " + total + " live channels ready" : "No active channels available.");
    if (from === 0 && list.querySelector("button")) list.querySelector("button").focus();
  }
  function nextPage() {
    if (loading) return;
    loading = true;
    api("itv", "get_ordered_list", "&p=" + (page + 1), function (result) {
      var start = channels.length;
      channels = channels.concat(result.data || []);
      page++;
      loading = false;
      render(start, result.total_items || 0);
      if (start > 0 && channels.length > start) list.getElementsByTagName("button")[start].focus();
    });
  }
  loadMore.onclick = nextPage;
  document.onkeydown = function (event) {
    var key = event.keyCode || event.which;
    if (key === 8 || key === 27 || key === 461) { event.preventDefault(); if (current !== -1) stop(); return; }
    if (key !== 38 && key !== 40) return;
    var buttons = Array.prototype.slice.call(list.getElementsByTagName("button"));
    if (!loadMore.hidden) buttons.push(loadMore);
    if (!buttons.length) return;
    var focused = -1;
    for (var i = 0; i < buttons.length; i++) if (buttons[i] === document.activeElement) focused = i;
    buttons[Math.max(0, Math.min(buttons.length - 1, focused + (key === 40 ? 1 : -1)))].focus();
    event.preventDefault();
  };
  if (!stb || typeof stb.GetDeviceMacAddress !== "function" || typeof stb.InitPlayer !== "function" ||
      typeof stb.Play !== "function" || typeof stb.SetTransparentColor !== "function" ||
      typeof stb.SetWinMode !== "function" || typeof stb.SetPIG !== "function") {
    report("Open this URL as an external portal on a compatible MAG box (gSTB required). A regular browser cannot play through this portal.");
    return;
  }
  try {
    mac = String(stb.GetDeviceMacAddress()).toUpperCase();
    stb.InitPlayer();
  } catch (error) { report("Could not initialize MAG hardware: " + error.message); return; }
  api("stb", "handshake", "", function (result) {
    session = result.token;
    nextPage();
  });
})();