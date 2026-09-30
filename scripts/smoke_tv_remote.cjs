// Optional real-browser TV-remote smoke test; uses Node, Chrome/Edge, and the existing venv.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const python = path.join(root, ".venv", "Scripts", "python.exe");
const chrome = [
  process.env.TV_TEST_BROWSER,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].find((candidate) => candidate && fs.existsSync(candidate));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "nexastream-tv-"));
const profile = path.join(temp, "browser");
const db = path.join(temp, "smoke.sqlite3").replaceAll("\\", "/");
const apiKey = randomBytes(48).toString("base64url");
const apiPort = 18762;
const chromePort = 18763;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(callback, description) {
  for (let attempt = 0; attempt < 300; attempt++) {
    try {
      const result = await callback();
      if (result) return result;
    } catch { /* Wait for server, browser, or page initialization. */ }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for ${description}`);
}

async function run() {
  if (!chrome || !fs.existsSync(python)) throw new Error("Chrome/Edge and .venv Python are required");
  const server = spawn(python, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(apiPort), "--no-access-log"], {
    cwd: root,
    env: { ...process.env, DATABASE_URL: `sqlite+aiosqlite:///${db}`, REDIS_URL: "", CREATE_TABLES_ON_STARTUP: "true", SEED_PUBLIC_CATALOG: "false", SEED_DEMO_DATA: "false", ADMIN_API_KEY: apiKey, SUBSCRIBER_BASE_URL: `http://127.0.0.1:${apiPort}` },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let serverError = "";
  server.stderr.on("data", (data) => { serverError += data.toString().slice(0, 4096); });
  const browser = spawn(chrome, ["--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--no-proxy-server", "--window-size=1440,900", `--user-data-dir=${profile}`, `--remote-debugging-port=${chromePort}`, "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    await waitFor(async () => {
      if (server.exitCode !== null) throw new Error(`Test server stopped: ${serverError}`);
      return (await fetch(`http://127.0.0.1:${apiPort}/health`)).ok;
    }, "API").catch((error) => { throw new Error(`${error.message}; server: ${serverError}`); });
    const page = await waitFor(async () => {
      const tabs = await (await fetch(`http://127.0.0.1:${chromePort}/json/list`)).json();
      return tabs.find((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
    }, "browser debugger");
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.addEventListener("open", resolve, { once: true }); ws.addEventListener("error", reject, { once: true }); });
    let nextId = 0;
    const requests = new Map();
    ws.addEventListener("message", (event) => {
      const response = JSON.parse(event.data);
      if (!requests.has(response.id)) return;
      const { resolve, reject } = requests.get(response.id);
      requests.delete(response.id);
      if (response.error) reject(new Error(response.error.message));
      else resolve(response.result);
    });
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++nextId;
      requests.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expression) => {
      const response = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
      return response.result.value;
    };
    const navigate = async (url) => {
      await send("Page.navigate", { url });
      await waitFor(() => evaluate("document.readyState === 'complete' && (document.getElementById('adminKey') !== null || document.getElementById('liveGrid') !== null || document.getElementById('watchStatus') !== null || document.getElementById('channels') !== null)"), "page");
    };
    const key = async (name) => {
      const codes = { ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Enter: 13, Escape: 27 };
      const params = { key: name, code: name, windowsVirtualKeyCode: codes[name], nativeVirtualKeyCode: codes[name] };
      if (name === "Enter") { params.text = "\r"; params.unmodifiedText = "\r"; }
      await send("Input.dispatchKeyEvent", { type: "keyDown", ...params });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...params });
    };

    await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await navigate(`http://127.0.0.1:${apiPort}/`);
    await waitFor(() => evaluate("document.querySelector('[data-view-target=live]')?.classList.contains('is-active')"), "dashboard initialization");
    await key("ArrowDown");
    assert.equal(await evaluate("document.activeElement?.dataset.viewTarget"), "live");
    await key("ArrowDown");
    assert.equal(await evaluate("document.activeElement?.dataset.viewTarget"), "india");
    await key("Enter");
    await waitFor(() => evaluate("location.hash === '#india'"), "Indian TV navigation");
    await key("ArrowRight");
    assert.equal(await evaluate("document.activeElement?.closest('.main-content') !== null"), true);
    await send("Emulation.setDeviceMetricsOverride", { width: 800, height: 600, deviceScaleFactor: 1, mobile: false });
    await evaluate("document.getElementById('mobileMenuButton').focus()");
    await key("Enter");
    assert.equal(await evaluate("document.getElementById('sidebar').classList.contains('is-open')"), true);
    await key("Escape");
    assert.equal(await evaluate("document.getElementById('sidebar').classList.contains('is-open')"), false);

    await navigate(`http://127.0.0.1:${apiPort}/admin`);
    await evaluate(`document.getElementById('adminKey').value = ${JSON.stringify(apiKey)}; document.getElementById('unlockButton').click()`);
    await waitFor(() => evaluate("!document.getElementById('adminContent').hidden"), "admin unlock");
    await evaluate("document.getElementById('subscriberName').value = 'TV browser smoke'; document.getElementById('subscriberForm').requestSubmit()");
    await waitFor(() => evaluate("document.querySelector('.subscriber-row') !== null"), "account creation");
    assert.equal(await evaluate("document.getElementById('newLink').hidden"), false);
    assert.equal(await evaluate("document.querySelector('[data-action=toggle]').textContent"), "Activate");
    const magUrl = await evaluate("document.querySelectorAll('#newLink a')[0].href");
    const portalUrl = await evaluate("document.querySelectorAll('#newLink a')[1].href");
    assert.equal((await fetch(magUrl)).status, 404);
    assert.equal((await fetch(portalUrl)).status, 404);
    await evaluate("document.querySelector('[data-action=toggle]').click(); document.getElementById('subscriberAction').requestSubmit()");
    await waitFor(() => evaluate("document.querySelector('[data-action=toggle]')?.textContent === 'Suspend'"), "customer activation");
    assert.equal((await fetch(portalUrl)).status, 200);
    assert.equal((await fetch(magUrl)).status, 200);
    const imported = await fetch(`http://127.0.0.1:${apiPort}/import-m3u`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_m3u: "#EXTM3U\n#EXTINF:-1,Sample TV\nhttps://media.example/sample.m3u8" }),
    });
    assert.equal(imported.status, 200);
    await navigate(portalUrl);
    await waitFor(() => evaluate("document.querySelector('.watch-item')?.textContent.includes('Sample TV')"), "customer TV channel list");
    await evaluate("document.querySelector('.watch-item').click()");
    assert.equal(await evaluate("document.getElementById('watchPlayer').hidden"), false);
    await key("Escape");
    assert.equal(await evaluate("document.getElementById('watchPlayer').hidden"), true);
    await navigate(`http://127.0.0.1:${apiPort}/admin`);
    await evaluate(`document.getElementById('adminKey').value = ${JSON.stringify(apiKey)}; document.getElementById('unlockButton').click()`);
    await waitFor(() => evaluate("!document.getElementById('adminContent').hidden"), "admin re-unlock");
    await evaluate("document.querySelector('[data-action=mac]').click()");
    await evaluate("document.getElementById('actionMac').value = 'aa-bb-cc-dd-ee-ff'; document.getElementById('subscriberAction').requestSubmit()");
    await waitFor(() => evaluate("document.querySelector('.subscriber-row small')?.textContent.includes('AA:BB:CC:DD:EE:FF')"), "MAC update");
    await evaluate("document.querySelector('[data-action=extend]').click()");
    assert.equal(await evaluate("document.activeElement?.id"), "actionMonthsPreset");
    await key("ArrowRight");
    assert.equal(await evaluate("document.activeElement?.id"), "confirmAction");
    await evaluate("document.getElementById('actionMonthsPreset').focus()");
    await evaluate("document.getElementById('actionMonthsPreset').value = '3'; document.getElementById('subscriberAction').requestSubmit()");
    await waitFor(() => evaluate("document.getElementById('subscriberAction').hidden"), "account extension");
    await evaluate("document.querySelector('[data-action=rotate]').click()");
    await key("Escape");
    assert.equal(await evaluate("document.getElementById('subscriberAction').hidden"), true);
    await evaluate("document.querySelector('[data-action=rotate]').click()");
    await evaluate("document.getElementById('subscriberAction').requestSubmit()");
    await waitFor(() => evaluate("document.getElementById('subscriberAction').hidden"), "link replacement");
    assert.equal(await evaluate("document.activeElement?.textContent"), "Copy MAG portal URL");
    assert.equal((await fetch(portalUrl)).status, 404);
    assert.equal((await fetch(magUrl)).status, 404);
    console.log("PASS: Chrome TV remote navigation, admin create/activate/MAC/extend/rotate/cancel, TV browser, and MAG portal access");
  } finally {
    ws?.close();
    browser.kill();
    server.kill();
    await sleep(500);
    try { fs.rmSync(temp, { recursive: true, force: true, maxRetries: 6, retryDelay: 300 }); }
    catch (error) { console.warn(`Could not remove temporary browser profile: ${error.message}`); }
  }
}

run().catch((error) => { console.error(error.message); process.exitCode = 1; });