// Deterministic MAG API/UI interaction check; does not claim physical-device compatibility.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let activeElement;
const requests = [];
const playback = [];
const elements = new Map();
function element() {
  return {
    textContent: "", hidden: false, children: [],
    appendChild(child) { this.children.push(child); },
    querySelector(tag) { return this.getElementsByTagName(tag)[0] || null; },
    getElementsByTagName(tag) { return this.children.flatMap((child) =>
      (child.tag === tag ? [child] : []).concat(child.getElementsByTagName?.(tag) || [])); },
    focus() { activeElement = this; },
  };
}
for (const id of ["message", "channels", "playing", "loadMore"]) elements.set(id, element());
const list = elements.get("channels");
const loadMore = elements.get("loadMore");
loadMore.hidden = true;
const titles = Array.from({ length: 52 }, (_, index) => ({
  name: `Test ${index + 1}`, number: index + 1, cmd: `ffmpeg channel_${index + 1}`,
}));
function XMLHttpRequest() {}
XMLHttpRequest.prototype.open = function (method, url) { this.method = method; this.url = url; };
XMLHttpRequest.prototype.setRequestHeader = function (key, value) { (this.headers ||= {})[key] = value; };
XMLHttpRequest.prototype.send = function () {
  const url = new URL(this.url, "http://127.0.0.1:8000");
  const action = url.searchParams.get("action");
  requests.push({ action, url, headers: this.headers });
  let js;
  if (action === "handshake") js = { token: "test-session" };
  else if (action === "get_ordered_list") {
    const page = Number(url.searchParams.get("p"));
    js = { data: titles.slice((page - 1) * 50, page * 50), total_items: titles.length };
  } else if (action === "create_link") js = { cmd: "ffmpeg https://media.example/test.m3u8" };
  else throw Error(`Unexpected action: ${action}`);
  this.status = 200;
  this.readyState = 4;
  this.responseText = JSON.stringify({ js });
  this.onreadystatechange();
};
const document = {
  getElementById(id) { return elements.get(id); },
  createElement(tag) { return Object.assign(element(), { tag }); },
  get activeElement() { return activeElement; },
};
const sandbox = {
  window: { innerWidth: 1280, gSTB: {
    GetDeviceMacAddress: () => "aa:bb:cc:dd:ee:ff", InitPlayer() {}, Stop() {},
    SetTransparentColor() {}, SetWinMode() {}, SetPIG() {},
    Play(command) { playback.push(command); },
  } },
  document, location: { pathname: "/stalker/secret/c/index.html" }, XMLHttpRequest,
  URL, Math, JSON, String, Array, Number,
};
const script = fs.readFileSync(path.join(__dirname, "..", "app", "static", "js", "mag.js"), "utf8");
vm.runInNewContext(script, sandbox, { filename: "mag.js" });
assert.deepEqual(requests.map(({ action }) => action), ["handshake", "get_ordered_list"]);
assert.equal(requests[0].headers["X-Device-Mac"], "AA:BB:CC:DD:EE:FF");
assert.equal(requests[1].headers.Authorization, "Bearer test-session");
assert.equal(requests[1].url.searchParams.get("p"), "1");
assert.equal(list.getElementsByTagName("button").length, 50);
assert.equal(loadMore.hidden, false);
loadMore.onclick();
assert.equal(requests[2].url.searchParams.get("p"), "2");
assert.equal(list.getElementsByTagName("button").length, 52);
assert.equal(loadMore.hidden, true);
list.getElementsByTagName("button")[0].onclick();
assert.equal(requests[3].headers.Authorization, "Bearer test-session");
assert.equal(playback[0], "ffmpeg https://media.example/test.m3u8");
assert.match(elements.get("playing").textContent, /Playing: Test 1/);
sandbox.document.onkeydown({ keyCode: 27, preventDefault() {} });
assert.equal(elements.get("playing").textContent, "Choose a channel to start playing.");
console.log("PASS: MAG mock handshake, pagination, MAC/session headers, playback and Back key");