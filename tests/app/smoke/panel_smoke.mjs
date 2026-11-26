// Browser smoke of the workflow window: the side panel (#86, S1-S17) and its themes, buttons, motion and phone
// layout (#87, S18-S45), as listed in docs/design/workflow-window.md; and the window that updates by component
// (#105, S46-S51), as listed in docs/design/window-updates.md; and the gate sections of a phase (#100, S52-S57), as
// listed in docs/design/gate-sections.md; and the whole-window view of a row (#113, S58-S66, and S67-S75 which pin what
// the mutation matrix found blind), as listed in docs/design/row-view.md; and a row whose phase is checked again (#111, S76), as listed in
// docs/design/steady-rows.md; and the stage of the design phases (#127, S77-S79 and S84, on a stubbed workflow answer),
// the generated diagrams of phase 5 (#128, S80) and the Invariants of phase 3 (#129, S81), and the motion of rows, folds, phase change, diagrams and full screen (#130, S82-S83, and the fold's transition in S78), as listed in docs/design/design-diagrams.md.
// A real server, the real page, a real headless Edge driven over the DevTools protocol. Node built-ins
// only (fetch, global WebSocket). Not part of pytest: it needs Windows Edge and a Windows node.exe.
//
//   node panel_smoke.mjs <server base url, e.g. http://localhost:8791/> <Edge remote-debugging port> [S25,S43]
// (the optional list runs only those checks; the full run is S1-S84 and expects all 84)
//
// The data are the server's own (the window scans the real $HOME), except for the states a live server
// cannot be made to show on demand (delivered, computing, a skipped phase, a failed request, a feature
// that left the snapshot): those answers are stubbed with Fetch.fulfillRequest from the real answers.
// One line per check, `ok S<n> <name>` or `FAIL S<n> <name>: <why>`; exit 1 on any failure or any page error.
// The DevTools tab it opens is closed at the end.
//
// Limits (repeatable only under these conditions):
//  - it reads the real $HOME through the server, and it needs the feature TARGET ("workflow-window") to be an open
//    row whose phase is derived (it exits 2 once that feature is delivered or its trace is gone), so it runs from
//    this feature's own tree while it is being built;
//  - animation and paint timing use fixed sleeps in places (a "nothing happens" check cannot wait on a condition),
//    so a very slow machine can fail a check that only needs more time; where a condition exists the check waits on it.

const [baseArg, portArg, onlyArg] = process.argv.slice(2);
const ONLY = onlyArg ? new Set(onlyArg.split(",")) : null;   // optional: run only these checks, e.g. S25,S43
if (!baseArg || !portArg) {
  console.error("usage: node panel_smoke.mjs <base url> <debug port> [S25,S43]");
  process.exit(2);
}
const base = baseArg.replace(/\/$/, "");
const TARGET = "workflow-window";   // the slug of the feature this program is built on
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- DevTools protocol -------------------------------------------------------------------------

const target = await (await fetch(`http://127.0.0.1:${portArg}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", () => reject(new Error("cannot open the DevTools socket")), { once: true });
});
let nextId = 0;
const pending = new Map();
const listeners = [];
ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const done = pending.get(message.id);
    pending.delete(message.id);
    if (done) done(message);
  } else for (const listener of listeners) listener(message);
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++nextId;
  pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
  ws.send(JSON.stringify({ id, method, params }));
});

async function ev(expression) {
  const out = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (out.exceptionDetails) {
    throw new Error(`page threw: ${out.exceptionDetails.exception ? out.exceptionDetails.exception.description : out.exceptionDetails.text}`);
  }
  return out.result.value;
}

async function waitFor(expression, what, ms = 20000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await ev(`Boolean(${expression})`)) return;
    await sleep(100);
  }
  throw new Error(`timeout waiting for ${what}`);
}

const KEYS = { ArrowLeft: 37, ArrowRight: 39, Escape: 27, r: 82, j: 74, k: 75 };
async function press(key) {
  const event = { key, code: key.length === 1 ? "Key" + key.toUpperCase() : key, windowsVirtualKeyCode: KEYS[key] };
  await send("Input.dispatchKeyEvent", { type: "keyDown", ...event, ...(key.length === 1 ? { text: key } : {}) });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...event });
}

// --- the stubs: a real answer, modified, for the states a live server cannot show on demand ---------

const stub = { features: null, workflow: null };   // fn(realBody, url) -> body | { status, body }
const seen = [];                                   // every /api path the page asked, in order
const sentTags = [];                               // [path, If-None-Match or null] of every /api request, in order (#105)
const pageErrors = [];

async function answer(paused) {
  const url = new URL(paused.request.url);
  const path = url.pathname + url.search;
  const handler = url.pathname === "/api/features" ? stub.features
    : url.pathname === "/api/workflow" ? stub.workflow : null;
  if (url.pathname.startsWith("/api/")) seen.push(path);
  const headers = paused.request.headers || {};
  const tag = Object.entries(headers).find(([name]) => name.toLowerCase() === "if-none-match");
  if (url.pathname.startsWith("/api/")) sentTags.push([path, tag ? tag[1] : null]);
  if (!handler) return send("Fetch.continueRequest", { requestId: paused.requestId });
  const real = await (await fetch(base + path)).json();
  const out = handler(real, url, tag ? tag[1] : null);
  const { status = 200, body } = out && out.body !== undefined && out.status !== undefined ? out : { body: out };
  return send("Fetch.fulfillRequest", { requestId: paused.requestId, responseCode: status,
    responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "Cache-Control", value: "no-store" }],
    body: Buffer.from(status === 304 ? "" : JSON.stringify(body)).toString("base64") });
}
listeners.push((m) => {
  // a page that navigates away while a stubbed request is pending makes DevTools forget the request: not a defect
  if (m.method === "Fetch.requestPaused") answer(m.params).catch((e) => { if (!/Invalid InterceptionId/.test(e.message)) pageErrors.push(`stub: ${e.message}`); });
  if (m.method === "Runtime.exceptionThrown") pageErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});

await send("Runtime.enable");
await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
await send("Page.navigate", { url: base + "/" });
await waitFor("document.querySelector('tr[data-id]')", "the table");

// --- the real rows ---------------------------------------------------------------------------

// A row can be `computing` for a moment (the server derives its phase in the background): wait for it.
let features;
let mine;
for (const until = Date.now() + 90000; Date.now() < until;) {
  features = await (await fetch(base + "/api/features")).json();
  mine = features.open.find((r) => r.slug === TARGET);
  if (mine && mine.phase.kind === "derived" && features.open.some((r) => r.slug !== TARGET)) break;   // S47/S50 also need a second open row
  await sleep(1000);
}
if (!mine || mine.phase.kind !== "derived") {
  console.error(`the server has no derived open row for ${TARGET}: run the smoke from the worktree of the feature`);
  process.exit(2);
}
const other = features.open.find((r) => r.slug !== TARGET);
if (!other) {
  console.error("the server has no second open row besides " + TARGET + ": the S47 and S50 checks need one (open another worktree or claim)");
  process.exit(2);
}
const dotPhase = mine.phase.phase;
const rowSel = (id) => `tr[data-id="${id}"]`;
const text = (sel) => `((document.querySelector(${JSON.stringify(sel)}) || {}).textContent || "")`;
const count = (sel) => `document.querySelectorAll(${JSON.stringify(sel)}).length`;
const clickRow = (id) => ev(`document.querySelector(${JSON.stringify(rowSel(id))}).click()`);
const step = (n) => ev(`document.querySelectorAll('.wfp-step')[${n}].click()`);
const title = () => ev(text(".wfp-title"));
const currentStep = () => ev(`(document.querySelector('.wfp-step[aria-current="step"] .wfp-no') || {}).textContent`);

async function reset() {
  stub.features = null;
  stub.workflow = null;
  await ev("closePanel(); if (state.tab !== 'open') chooseTab('open'); refresh()");
  await waitFor("document.querySelector('tr[data-id]')", "the table");
  await sleep(150);
}

// With `stage` the call returns once the stage is drawn (a phase cannot be chosen before: there is nothing to show);
// without it, once the panel shows its title or a banner (the states with no stage).
async function open(id = mine.id, stage = true) {
  await clickRow(id);
  await waitFor(stage ? "document.querySelector('.wfp-title')"
    : "document.querySelector('.wfp-title') || document.querySelector('.wfp-banner:not([hidden])')", "the panel");
}

const deliveredRow = (row) => ({ ...row, agent: { process: "claude", pid: 1, since: row.last_activity },
  phase: { kind: "delivered", phase: null, verdicts: row.phase.verdicts } });
const withRow = (change) => (real) => ({ ...real, open: real.open.map((r) => (r.id === mine.id ? change(r) : r)) });

function check(table) {
  return async (id, name, body) => {
    if (ONLY && !ONLY.has(id)) return;
    try {
      await body();
      console.log(`ok ${id} ${name}`);
      table.push([id, true]);
    } catch (error) {
      console.log(`FAIL ${id} ${name}: ${error.message}`);
      table.push([id, false]);
    }
  };
}
const results = [];
const run = check(results);
const expect = (condition, why) => { if (!condition) throw new Error(why); };

// --- helpers of S18-S45 (#87): the system's scheme and motion, the phone, storage, what the page really paints ---

const CSS_SYSTEM = { scheme: "prefers-color-scheme", motion: "prefers-reduced-motion" };
const RING_SCRIPT_IDS = [];   // Page.addScriptToEvaluateOnNewDocument ids of the current check
let dirty = false;            // a check changed the scheme, the motion, the width or the storage: the next reset reloads

async function restoreEnvironment() {
  for (const id of RING_SCRIPT_IDS.splice(0)) await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: id });
  await send("Emulation.setEmulatedMedia", { features: [
    { name: CSS_SYSTEM.scheme, value: "" }, { name: CSS_SYSTEM.motion, value: "" }] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
}

// A fresh page load with the given environment. `stored` is the wf-theme value in localStorage (null: none);
// `before` is a script run before any page script (to simulate a storage that refuses, or to observe the first paint).
async function loadPage({ scheme = "", motion = "", width = 1280, stored = null, before = null } = {}) {
  stub.features = null;
  stub.workflow = null;
  const tainted = RING_SCRIPT_IDS.length > 0;   // the live document may carry the last check's storage thrower
  await restoreEnvironment();
  if (tainted) {   // seed the storage from a clean document
    await ev("window.__old = true");
    await send("Page.navigate", { url: base + "/" });
    await waitFor("!window.__old && document.readyState === 'complete'", "a clean document");
  }
  await send("Emulation.setEmulatedMedia", { features: [
    { name: CSS_SYSTEM.scheme, value: scheme }, { name: CSS_SYSTEM.motion, value: motion }] });
  if (width !== 1280) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 800, deviceScaleFactor: 1, mobile: true });
  }
  await ev(`try { ${stored === null ? "localStorage.removeItem('wf-theme')" : `localStorage.setItem('wf-theme', ${JSON.stringify(stored)})`} } catch (e) {}; window.__old = true`);
  if (before) RING_SCRIPT_IDS.push((await send("Page.addScriptToEvaluateOnNewDocument", { source: before })).identifier);
  dirty = true;
  await send("Page.navigate", { url: base + "/" });
  await waitFor("!window.__old && document.readyState === 'complete' && document.querySelector('tr[data-id]')", "the table after the load");
  await sleep(150);
}

// reset() of S1-S17 plus the environment of S18-S45 (a check that changed it makes the next reset reload the page)
const resetBase = reset;
reset = async function () {
  stub.features = null;
  stub.workflow = null;
  if (dirty) {
    await loadPage();
    dirty = false;
  }
  await resetBase();
};

const theme = () => ev("document.documentElement.dataset.theme");
const bodyBg = () => ev("getComputedStyle(document.body).backgroundColor");
const stored = () => ev("(() => { try { return localStorage.getItem('wf-theme'); } catch (e) { return 'THROWS'; } })()");
const tokenRgb = (name) => ev(`(() => { const p = document.createElement('i'); p.style.color = 'var(${name})'; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; })()`);
const tab = (name) => ev(`document.getElementById('tab-${name}').click()`);
const panelText = () => ev(`document.getElementById('wfpanel').textContent.replace(/\\s+/g, ' ').trim()`);

async function pointer(type, selector, extra = {}) {
  const at = await ev(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await send("Input.dispatchMouseEvent", { type, x: at.x, y: at.y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, ...extra });
}
const hover = (selector) => pointer("mouseMoved", selector, { buttons: 0 });
const parkMouse = () => send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 1, y: 1 });
const matrixOf = (selector, pseudo = "") => ev(`(() => { const t = getComputedStyle(document.querySelector(${JSON.stringify(selector)}), ${JSON.stringify(pseudo)}).transform; const m = t.match(/matrix\\(([^)]+)\\)/); return m ? m[1].split(',').map(Number) : null; })()`);
const running = (inside = "#wfpanel") => ev(`(() => document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest(${JSON.stringify(inside)})).map((a) => a.animationName).sort())()`);

// Every visible text node of the page: its computed colour against the opaque background it really sits on
// (gradient tints composited), the size and weight deciding 4.5 or 3. SVG text is left to the diagram check (S27).
function measureText() {
  const nums = (c) => { const p = (c.match(/[\d.]+/g) || []).map(Number); return p.length < 3 ? null : [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (t, u) => [0, 1, 2].map((i) => t[i] * t[3] + u[i] * (1 - t[3])).concat(1);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const [h, l] = [lum(a), lum(b)].sort((x, y) => y - x); return (h + 0.05) / (l + 0.05); };
  const stack = (el) => {
    const out = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const tints = cs.backgroundImage.startsWith("linear-gradient(") ? cs.backgroundImage.match(/rgba?\([^)]*\)/g) || [] : [];
      if (tints.length === 2 && tints[0] === tints[1]) out.push(nums(tints[0]));
      const bg = nums(cs.backgroundColor);
      if (bg && bg[3] > 0) out.push(bg);
      if (bg && bg[3] === 1) break;
    }
    return out;
  };
  const ground = (el) => { const s = stack(el); let c = s.length && s[s.length - 1][3] === 1 ? s.pop() : [255, 255, 255, 1]; while (s.length) c = over(s.pop(), c); return c; };
  const bad = [];
  let judged = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim();
    const el = node.parentElement;
    if (!text || !el || el.closest("svg, script, style, .vh, iframe")) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility !== "visible" || !el.getClientRects().length) continue;
    const size = parseFloat(cs.fontSize);
    const need = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700) ? 3 : 4.5;
    const got = ratio(nums(cs.color), ground(el));
    judged++;
    if (got < need) bad.push(`${(el.className && el.className.baseVal === undefined ? el.className : el.tagName)}: "${text.slice(0, 24)}" ${cs.color} on ${ground(el).map((v) => Math.round(v)).join(",")} = ${got.toFixed(2)} (needs ${need})`);
  }
  return { judged, bad };
}

// The effective background of an element, for S26 (self-contained: it runs in the page).
function surfaceLuminances(selectors, limit) {
  const nums = (c) => { const p = (c.match(/[\d.]+/g) || []).map(Number); return p.length < 3 ? null : [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (t, u) => [0, 1, 2].map((i) => t[i] * t[3] + u[i] * (1 - t[3])).concat(1);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ground = (el) => { const s = []; for (let e = el; e; e = e.parentElement) { const bg = nums(getComputedStyle(e).backgroundColor); if (bg && bg[3] > 0) s.push(bg); if (bg && bg[3] === 1) break; } let c = s.length && s[s.length - 1][3] === 1 ? s.pop() : [255, 255, 255, 1]; while (s.length) c = over(s.pop(), c); return c; };
  const seen = {};
  const tooLight = [];
  for (const sel of selectors) {
    const found = [...document.querySelectorAll(sel)].filter((e) => e.getClientRects().length);
    seen[sel] = found.length;
    for (const e of found) if (lum(ground(e)) > limit) tooLight.push(`${sel}: ${ground(e).map((v) => Math.round(v)).join(",")}`);
  }
  return { seen, tooLight: [...new Set(tooLight)] };
}

const rgbLum = (rgb) => { const c = rgb.match(/[\d.]+/g).map(Number); const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };

// A feature row stubbed with given verdicts / agent / links (phase = the derived phase when kind is derived).
const AGENT = { process: "claude", pid: 4242, since: "2026-11-18T13:00:00+00:00" };
const verdictsOf = (statuses, problems = {}) => statuses.map((status, n) => ({ phase: n, status, problems: problems[n] || [], reason: status === "skip" ? "CLI only, no UI." : null }));
const rowWith = (change) => (real) => ({ ...real, open: real.open.map((r) => (r.id === mine.id ? { ...r, ...change(r) } : r)) });
const derived = (phase, statuses, { agent = null, problems = {}, links = null } = {}) => rowWith((r) => ({
  agent, links: links || r.links, phase: { kind: "derived", phase, verdicts: verdictsOf(statuses, problems) } }));
const passedUpTo = (n) => Array.from({ length: 7 }, (_, i) => (i < n ? "pass" : "todo"));

// The panel on `mine`, ready on its stage, after the slide-in has finished.
async function openPanelReady(id = mine.id) {
  await open(id);
  await waitFor(`${count(".wfp-title")} === 1`, "the stage");
  await waitFor("document.getElementById('wfpanel').getAnimations().every((a) => a.animationName !== 'slidein' || a.playState !== 'running')", "the panel to settle");
}

// A spec-model's diagrams, drawn in the panel (phase 3, the spec-model card)
async function openDiagrams() {
  await step(3);
  // the texts of a design phase are folds (#127): the spec-model document is the fold of kind spec-model
  await waitFor(`document.querySelector('details.wfp-fold.spec-model > summary')`, "the spec-model fold");
  await ev(`document.querySelector('details.wfp-fold.spec-model > summary').click()`);
  await waitFor(`${count(".wfp-doc .diagram > svg")} > 0 && ${count(".wfp-doc .diagram path.flowchart-link")} > 0`, "the drawn flowchart");
}

// The Documents tab opens on the first repo of the snapshot, which may have no document: choose the feature's own.
async function chooseDocsRepo() {
  await waitFor("document.querySelector('.doc-bar select')", "the repository select");
  await ev(`(() => { const sel = document.querySelector('.doc-bar select'); sel.value = ${JSON.stringify(mine.repo)}; sel.dispatchEvent(new Event('change')); })()`);
  await waitFor(`${count(".lines li")} > 0`, "the document list");
}

// Tab, then S20/S21's walk: the table, the open panel, History, Workflow, Documents with a document open.
async function walkTheWindow() {
  const bad = [];
  let judged = 0;
  const take = async (label) => {
    await sleep(250);
    const out = await ev(`(${measureText})()`);
    judged += out.judged;
    bad.push(...out.bad.map((b) => `${label} ${b}`));
  };
  await resetBase();
  await take("open");
  await openPanelReady();
  await take("panel");
  await step(2);
  await waitFor(`${count(".wfp-card")} >= 1`, "a card");
  await take("panel/mockup");
  await openDiagrams();
  await take("panel/model");
  await tab("history");
  await take("history");
  await tab("workflow");
  await waitFor(`${count(".map .node")} === 7`, "the map");
  await ev(`document.querySelectorAll('.map .node')[2].click()`);
  await take("workflow");
  await tab("documents");
  await chooseDocsRepo();
  await ev(`Array.from(document.querySelectorAll('.lines li')).find((li) => li.querySelector('.pill').textContent === 'design').querySelector('.act').click()`);
  await waitFor(`${text(".docpane .doc")}.length > 100`, "the document pane");
  await take("documents");
  return { judged, bad };
}


// --- S1-S17 ------------------------------------------------------------------------------------

await run("S1", "a row click opens the panel on its derived phase", async () => {
  await reset();
  await open();
  expect((await currentStep()) === String(dotPhase), `current step is ${await currentStep()}, the row's phase is ${dotPhase}`);
  expect(await ev(`${count("#view table")} === 1`), "the table is gone");
  expect(await ev(`!document.querySelector('#wfpanel').hidden && document.querySelector('#body').classList.contains('has-panel')`),
    "the panel is not shown");
  const crumb = await ev(text(".wfp-crumb"));
  expect(crumb === `${TARGET} / phase ${dotPhase} of 7`, `the crumb reads "${crumb}"`);
  const hints = await ev(`Array.from(document.querySelectorAll('.wfp-keys span')).map((h) => h.textContent.replace(/\\s+/g, ' ').trim()).join('|')`);
  expect(hints === "← → phase|R refresh|Esc close", `the key hints read "${hints}"`);
  expect((await ev(count(".wfp-keys kbd"))) === 4, "not four key caps in the hints line");
});

await run("S2", "the red dot is on that phase only", async () => {
  expect((await ev(count(".wfp-dot"))) === 1, `${await ev(count(".wfp-dot"))} dots`);
  expect(await ev(`document.querySelector('.wfp-dot').closest('.wfp-step') === document.querySelectorAll('.wfp-step')[${dotPhase}]`),
    "the dot is not inside the derived phase's step");
});

await run("S3", "clicking another step shows its artefact and the dot stays", async () => {
  const other = dotPhase === 5 ? 3 : 5;
  const before = await title();
  await step(other);
  await waitFor(`${text(".wfp-title")}.startsWith("${other} ")`, "the stage of the other phase");
  expect((await title()) !== before, "the stage title did not change");
  expect((await ev(`document.querySelector('.wfp-step.live .wfp-no').textContent`)) === String(dotPhase), "the dot moved");
  expect((await ev(count(".wfp-dot"))) === 1, "more or fewer than one dot");
  await step(5);
  // phase 5 is a design phase (#127): its document is a folded row, drawn when the owner opens it
  await waitFor(`document.querySelector('details.wfp-fold.design > summary')`, "the design fold in the stage");
  expect((await ev(text("details.wfp-fold.design > summary"))).includes(`docs/design/${TARGET}.md`), `the design fold does not name docs/design/${TARGET}.md`);
  await ev(`document.querySelector('details.wfp-fold.design > summary').click()`);
  await waitFor(`document.querySelector('details.wfp-fold.design').open && ${text("details.wfp-fold.design .wfp-doc")}.length > 200`, "the design artefact drawn in the stage");
  expect((await currentStep()) === "5", "the current step did not follow the choice");
});

await run("S4", "Back and Next and the arrow keys move the stage; no Back at 0, no Next at 6", async () => {
  await step(5);
  await press("ArrowRight");
  await waitFor(`${text(".wfp-title")}.startsWith("6 ")`, "ArrowRight to 6");
  expect(await ev(`!document.querySelector('.wfp-next')`), "Next shown at phase 6");
  await press("ArrowRight");
  await sleep(200);
  expect((await currentStep()) === "6", "ArrowRight moved past 6");
  await ev(`document.querySelector('.wfp-back').click()`);
  await waitFor(`${text(".wfp-title")}.startsWith("5 ")`, "the Back button");
  await ev(`document.querySelector('.wfp-next').click()`);
  await waitFor(`${text(".wfp-title")}.startsWith("6 ")`, "the Next button");
  for (let n = 5; n >= 0; n--) {
    await press("ArrowLeft");
    await waitFor(`${text(".wfp-title")}.startsWith("${n} ")`, `ArrowLeft to ${n}`);
  }
  expect(await ev(`!document.querySelector('.wfp-back')`), "Back shown at phase 0");
  await press("ArrowLeft");
  await sleep(200);
  expect((await currentStep()) === "0", "ArrowLeft moved before 0");
});

await run("S5", "Esc closes the panel; so does the Back pill (row-view: the x button is gone); focus returns to the row", async () => {
  await press("Escape");
  await waitFor("document.querySelector('#wfpanel').hidden", "Esc to close");
  expect(await ev(`!document.querySelector('#body').classList.contains('has-panel')`), "has-panel stayed");
  expect(await ev(`document.activeElement && document.activeElement.dataset.id === ${JSON.stringify(mine.id)}`), "focus did not return to the row");
  await open();
  await ev(`document.querySelector('.wfp-back-pill').click()`);
  await waitFor("document.querySelector('#wfpanel').hidden", "the Back pill to close");
  expect(await ev(`document.activeElement && document.activeElement.dataset.id === ${JSON.stringify(mine.id)}`), "focus did not return after the button");
});

await run("S6", "another row replaces the panel", async () => {
  expect(other, "the server has no second open row");
  await open();
  await open(other.id, false);
  await waitFor(`${text(".wfp-slug")} === ${JSON.stringify(other.slug)}`, "the other slug in the top bar");
  expect((await ev(count("#wfpanel"))) === 1, "not exactly one panel in the DOM");
  expect(await ev(`${count("tr.sel")} === 1 && document.querySelector('tr.sel').dataset.id === ${JSON.stringify(other.id)}`), "the first row is still marked");
});

await run("S7", "a design document opens in the stage and a Model's Mermaid diagrams are drawn", async () => {
  await reset();
  await open();
  const workflow = await (await fetch(`${base}/api/workflow?feature=${mine.id}`)).json();
  const blocks = async (artefact) => ((await (await fetch(`${base}/api/documents/${artefact.id}`)).text()).match(/^```mermaid/gm) || []).length;
  // the design phase: the document is drawn in the stage (workflow-window's design has no mermaid block of its own)
  const design = workflow.phases[5].artefacts[0];
  await step(5);
  await waitFor(`document.querySelector('details.wfp-fold.design > summary')`, "the design fold");
  await ev(`document.querySelector('details.wfp-fold.design > summary').click()`);
  await waitFor(`${text(".wfp-doc")}.length > 200`, "the design document in the stage");
  const drawn = () => ev(`document.querySelectorAll('.wfp-doc .diagram > svg').length`);
  expect((await drawn()) === (await blocks(design)), `design: ${await drawn()} diagrams for ${await blocks(design)} blocks`);
  expect((await ev(count(".wfp-doc .diagram-error"))) === 0, "a diagram failed in the design document");
  // phase 3: the spec's Model, which has the Mermaid blocks
  const model = workflow.phases[3].artefacts.find((a) => a.kind === "spec-model");
  const expected = await blocks(model);
  expect(expected > 0, "the spec-model document has no mermaid block to draw");
  await step(3);
  await waitFor(`document.querySelector('details.wfp-fold.spec-model > summary')`, "the spec-model fold");
  await ev(`document.querySelector('details.wfp-fold.spec-model > summary').click()`);
  await waitFor(`document.querySelectorAll('.wfp-doc .diagram > svg').length === ${expected}`, `${expected} drawn diagrams`);
  expect((await ev(count(".wfp-doc .diagram-error"))) === 0, "a diagram failed in the Model");
  expect((await ev(count(".wfp-doc pre code.language-mermaid"))) === 0, "a mermaid block was left as code");
});

await run("S8", "a mockup opens in a sandboxed frame", async () => {
  await step(2);
  await waitFor(`Array.from(document.querySelectorAll('.wfp-card')).some((b) => b.title === 'mockup')`, "the mockup card");
  // phase 2 shows its gate section first (#100), so the whole document opens only on a click
  await ev(`Array.from(document.querySelectorAll('.wfp-card')).find((b) => b.title === 'mockup').click()`);
  await waitFor(`${count('.wfp-doc iframe[sandbox=""]')} === 1`, "the mockup in its frame");
  expect(await ev(`${count('.wfp-doc iframe[sandbox=""]')} === 1`), "no iframe with an empty sandbox in the stage");
  expect(await ev(`document.querySelector('.wfp-doc iframe').getAttribute('sandbox') === ''`), "the sandbox is not empty");
});

await run("S9", "a refresh keeps an opened fold and its drawn diagram (the stage is not rebuilt)", async () => {
  await step(3);
  await waitFor(`document.querySelector('details.wfp-fold.spec-model > summary')`, "the spec-model fold");
  // S7 left this fold open (a fold stays as the owner left it): open it only when it is closed
  await ev(`(() => { const d = document.querySelector('details.wfp-fold.spec-model'); if (!d.open) d.querySelector('summary').click(); })()`);
  await waitFor(`${count(".wfp-doc .diagram > svg")} > 0`, "a drawn diagram");
  await ev(`document.querySelector('.wfp-doc .diagram').dataset.marker = 'kept'`);
  const asked = seen.filter((p) => p.startsWith("/api/workflow?feature=")).length;
  await press("r");
  const until = Date.now() + 15000;
  while (seen.filter((p) => p.startsWith("/api/workflow?feature=")).length === asked && Date.now() < until) await sleep(100);
  expect(seen.filter((p) => p.startsWith("/api/workflow?feature=")).length > asked, "the R key did not make the panel ask again");
  await sleep(800);
  expect(await ev(`${count('.wfp-doc .diagram[data-marker="kept"]')} === 1`), "the drawn diagram was rebuilt by the refresh");
  expect(await ev(`document.querySelector('details.wfp-fold.spec-model').open`), "the refresh closed the fold");
});

await run("S10", "a delivered row (stubbed) opens on phase 6 with no dot", async () => {
  await reset();
  stub.features = withRow(deliveredRow);
  await ev("refresh()");
  await open();
  await waitFor(`${text(".wfp-title")}.startsWith("6 ")`, "the stage of phase 6");
  expect((await currentStep()) === "6", `current step ${await currentStep()}`);
  expect((await ev(count(".wfp-dot"))) === 0, "a dot on a delivered row");
  expect(await ev(`Array.from(document.querySelectorAll('.wfp-word')).map((w) => w.textContent).join() === 'PASS,PASS,PASS,PASS,PASS,PASS,PASS'`),
    "not every step PASS");
  expect((await ev(text(".wfp-tags"))).includes("delivered"), "no delivered tag");
  expect(await ev(`document.querySelector('.wfp-agent').hidden`), "agent active shown for a stale agent record");
});

await run("S11", "a computing row (stubbed) shows no dot and says it is checking", async () => {
  await reset();
  stub.features = withRow((r) => ({ ...r, agent: null, phase: { kind: "computing", phase: null, verdicts: [] } }));
  await ev("refresh()");
  await open(mine.id, false);
  await waitFor(`${text(".wfp-banner")}.includes("Checking the phases")`, "the checking banner");
  expect((await ev(count(".wfp-dot"))) === 0 && (await ev(count(".wfp-word"))) === 0, "a dot or a word while computing");
  expect(await ev(`document.querySelector('.wfp-banner').getAttribute('role') === 'status' && !document.querySelector('.wfp-title')`),
    "the banner is not a status or a stage is shown");
  // no stage: the arrows and a click on a step choose nothing, so the panel keeps following the dot
  await press("ArrowRight");
  await step(5);
  await sleep(200);
  expect(await ev("state.panel.chosen === null"), "a choice was kept while there was no stage");
  stub.features = null;
  await ev("refresh()");
  await waitFor(`${count(".wfp-title")} === 1`, "the stage once the row is derived");
  expect((await currentStep()) === String(dotPhase), `the panel did not follow the dot (current step ${await currentStep()}, dot ${dotPhase})`);
});

await run("S12", "a failed workflow request (stubbed 503) keeps the panel and says so; the next answer clears it", async () => {
  await reset();
  await open();
  await waitFor(`${count(".wfp-title")} === 1`, "the stage");
  await ev(`document.querySelector('.wfp-title').dataset.marker = 'kept'`);
  stub.workflow = () => ({ status: 503, body: {} });
  await ev("refresh()");
  await waitFor(`${text(".wfp-banner")}.startsWith("Cannot load the workflow: HTTP 503")`, "the error banner");
  expect(await ev(`document.querySelector('.wfp-banner').getAttribute('role') === 'alert' && !document.querySelector('#wfpanel').hidden`),
    "the banner is not an alert or the panel went away");
  expect(await ev(`${count('.wfp-title[data-marker="kept"]')} === 1`), "the stage was thrown away on a failed refresh");
  stub.workflow = null;
  await ev("refresh()");
  await waitFor("document.querySelector('.wfp-banner').hidden", "the banner to clear");
  expect(await ev(`${count('.wfp-title[data-marker="kept"]')} === 1`), "the stage was rebuilt when the banner cleared");
  // no map yet: the steps of the row stay and the banner explains
  await reset();
  stub.workflow = () => ({ status: 503, body: {} });
  await open(mine.id, false);
  await waitFor(`${text(".wfp-banner")}.includes("Cannot load the workflow")`, "the error with no map");
  expect((await ev(count(".wfp-step"))) === 7 && (await ev(count(".wfp-title"))) === 0, "steps missing or a stage shown with no map");
  await press("ArrowLeft");
  await step(1);
  await sleep(200);
  expect(await ev("state.panel.chosen === null"), "a choice was kept while there was no map");
  stub.workflow = null;
  await ev("refresh()");
  await waitFor(`${count(".wfp-title")} === 1`, "the stage after the retry");
  expect((await currentStep()) === String(dotPhase), `the panel did not follow the dot after the retry (current step ${await currentStep()})`);
});

await run("S13", "the feature leaving the snapshot (stubbed) closes the panel and shows the notice", async () => {
  await reset();
  await open();
  stub.features = (real) => ({ ...real, open: real.open.filter((r) => r.id !== mine.id), history: real.history.filter((r) => r.id !== mine.id) });
  await ev("refresh()");
  await waitFor("document.querySelector('#wfpanel').hidden", "the panel to close");
  expect(await ev(`${text("#notice")}.includes(${JSON.stringify(`The workflow panel closed: ${TARGET} is no longer in the table.`)}) && !document.querySelector('#notice').hidden`),
    "no notice");
  expect(await ev(`!document.querySelector('#body').classList.contains('has-panel')`), "has-panel stayed");
});

await run("S14", "the Workflow tab shows the generic map with no select", async () => {
  await reset();
  await open();
  seen.length = 0;
  await ev(`document.getElementById('tab-workflow').click()`);
  await waitFor(`${count(".map .node")} === 7`, "the seven phases");
  expect((await ev(count("#view select"))) === 0, "a select on the Workflow tab");
  expect(!(await ev(`document.getElementById('view').textContent`)).includes("feature on the map"), "the chooser text is there");
  expect(await ev(`document.querySelector('#wfpanel').hidden`), "the panel stayed open over the Workflow tab");
  expect(seen.includes("/api/workflow"), `the tab did not ask /api/workflow with no query (asked ${JSON.stringify(seen)})`);
  expect((await ev(count(".node.here, .node .badge"))) === 0, "an overlay mark on the generic map");
  await ev(`document.getElementById('tab-open').click()`);
});

await run("S15", "a skipped phase shows its reason; an empty phase says so; long names do not widen the page", async () => {
  await reset();
  const long = "x".repeat(90);
  stub.features = withRow((r) => ({ ...r, slug: long, phase: { ...r.phase,
    verdicts: r.phase.verdicts.map((v) => (v.phase === 2 ? { ...v, status: "skip", reason: "CLI only, no UI." } : v)) } }));
  stub.workflow = (real) => ({ ...real, phases: real.phases.map((p) =>
    (p.n === 3 ? { ...p, artefacts: [] } : p.n === 5 ? { ...p, artefacts: p.artefacts.map((a) => ({ ...a, name: "y".repeat(90) })) } : p)) });
  await ev("refresh()");
  await open();
  await waitFor(`${text(".wfp-slug")} === ${JSON.stringify(long)}`, "the long slug in the top bar");
  await step(2);
  await waitFor(`${text(".wfp-nothing")} === "Nothing to read: the phase was skipped."`, "the skipped line");
  expect((await ev(text(".wfp-idea"))).includes("CLI only, no UI.") && (await ev(text(".wfp-idea"))).includes("why it was skipped"), "the reason is not shown");
  expect((await ev(text(".wfp-tags"))).includes("SKIP") && (await ev(count(".wfp-card"))) === 0, "no SKIP tag or cards on a skipped phase");
  await step(3);
  await waitFor(`${text(".wfp-nothing")} === "Nothing to read here yet."`, "the empty-phase line");
  expect((await ev(text(".wfp-idea"))).includes("what leaves phase 3"), "the idea block is gone from an empty phase");
  await step(5);
  await waitFor(`${count("details.wfp-fold")} >= 1`, "the long fold row");   // a design phase folds its document (#127)
  await sleep(300);
  const wide = await ev(`({ page: document.documentElement.scrollWidth <= innerWidth,
    panel: document.getElementById('wfpanel').scrollWidth <= document.getElementById('wfpanel').clientWidth,
    card: document.querySelector('details.wfp-fold').getBoundingClientRect().right <= document.getElementById('wfpanel').getBoundingClientRect().right })`);
  expect(wide.page, "the page scrolls sideways");
  expect(wide.panel && wide.card, `the panel overflows (panel ${wide.panel}, card ${wide.card})`);
});

await run("S16", "a click on the row already open does nothing", async () => {
  await reset();
  await open();
  const chosen = dotPhase === 5 ? 3 : 5;
  await step(chosen);
  await waitFor(`${text(".wfp-title")}.startsWith("${chosen} ")`, "the chosen stage");
  await ev(`document.querySelector('.wfp-title').dataset.marker = 'kept'`);
  // The page's own poll also asks the workflow again (every second while a row of the snapshot is computing, as
  // the live target of a run can be). The poll is held off for the window of the click, so that a request seen
  // in it can only be the click's; it is put back, and run once, at the end.
  await ev(`window.__schedule = schedule; schedule = () => {}`);
  try {
    await sleep(1500);   // a refresh already in flight lands first
    const asked = seen.filter((p) => p.startsWith("/api/workflow?feature=")).length;
    await clickRow(mine.id);
    await sleep(500);
    expect((await currentStep()) === String(chosen), "the click moved the chosen phase");
    expect(await ev(`${count('.wfp-title[data-marker="kept"]')} === 1`), "the click rebuilt the stage");
    expect(seen.filter((p) => p.startsWith("/api/workflow?feature=")).length === asked, "the click asked the workflow again");
  } finally {
    await ev(`schedule = window.__schedule; refresh()`);
  }
  expect(await ev(`${count("#wfpanel")} === 1 && !document.querySelector('#wfpanel').hidden`), "the panel went away or doubled");
});

await run("S17", "with no choice the panel follows the dot at a refresh; a choice stays", async () => {
  await reset();
  await open();
  expect(await ev("state.panel.chosen === null"), "a phase is chosen at open");
  expect((await currentStep()) === String(dotPhase), "the panel does not open on the dot");
  const moved = dotPhase < 6 ? dotPhase + 1 : dotPhase - 1;
  stub.features = withRow((r) => ({ ...r, phase: { ...r.phase, phase: moved } }));
  await ev("refresh()");
  await waitFor(`${text(".wfp-title")}.startsWith("${moved} ")`, "the stage to follow the dot");
  expect((await currentStep()) === String(moved), `current step ${await currentStep()} while the dot is on ${moved}`);
  expect((await ev(`document.querySelector('.wfp-step.live .wfp-no').textContent`)) === String(moved), "the dot did not move");
  await step(0);
  await waitFor(`${text(".wfp-title")}.startsWith("0 ")`, "the chosen stage");
  stub.features = withRow((r) => ({ ...r, phase: { ...r.phase, phase: dotPhase } }));
  await ev("refresh()");
  await waitFor(`document.querySelector('.wfp-step.live .wfp-no').textContent === ${JSON.stringify(String(dotPhase))}`, "the dot to move back");
  await sleep(300);
  expect((await currentStep()) === "0", "a chosen phase was dragged along by the dot");
});

// --- S18-S45 (#87): themes, buttons, motion, the phone ------------------------------------------------------

const LIGHT_BG = "rgb(246, 243, 236)";
const DARK_BG = "rgb(19, 20, 19)";

await run("S18", "with the system in light the window is the light paper", async () => {
  await loadPage({ scheme: "light" });
  expect((await theme()) === "light", `data-theme is ${await theme()}`);
  expect((await bodyBg()) === LIGHT_BG, `the body background is ${await bodyBg()}`);
  expect((await ev("getComputedStyle(document.documentElement).colorScheme")) === "light", "color-scheme is not light");
});

await run("S19", "with the system in dark the window is the dark graphite", async () => {
  await loadPage({ scheme: "dark" });
  expect((await theme()) === "dark", `data-theme is ${await theme()}`);
  expect((await bodyBg()) === DARK_BG, `the body background is ${await bodyBg()}`);
  expect((await ev("getComputedStyle(document.documentElement).colorScheme")) === "dark", "color-scheme is not dark");
});

for (const [id, scheme] of [["S20", "light"], ["S21", "dark"]]) {
  await run(id, `every visible text reaches its contrast in the ${scheme} theme`, async () => {
    await loadPage({ scheme });
    const { judged, bad } = await walkTheWindow();
    expect(judged > 150, `only ${judged} text nodes were judged: the walk did not reach the whole window`);
    expect(bad.length === 0, `${bad.length} of ${judged} below their contrast, e.g. ${bad.slice(0, 4).join(" | ")}`);
  });
}

await run("S22", "a verdict is a word and a tint, four tints, in both themes", async () => {
  for (const scheme of ["light", "dark"]) {
    await loadPage({ scheme });
    stub.features = derived(3, ["pass", "skip", "todo", "fail", "todo", "todo", "todo"], { problems: { 3: ["a problem"] } });
    await ev("refresh()");
    await open();
    await waitFor(`${count(".wfp-word")} === 7`, "seven words");
    const steps = await ev(`Array.from(document.querySelectorAll('.wfp-step')).slice(0, 4).map((s) => ({ text: s.textContent.replace(/\\s+/g, ' '),
      color: getComputedStyle(s.querySelector('.wfp-word')).color, outline: getComputedStyle(s.querySelector('.wfp-no')).borderTopStyle }))`);
    ["PASS", "SKIP", "TODO", "FAIL"].forEach((word, i) => expect(steps[i].text.includes(word), `${scheme}: step ${i} does not say ${word}: "${steps[i].text}"`));
    expect(new Set(steps.map((s) => s.color)).size === 4, `${scheme}: the four word colours are ${steps.map((s) => s.color).join(" / ")}`);
    expect(steps[1].outline === "dashed", `${scheme}: SKIP's outline is ${steps[1].outline}, not dashed`);
    expect(steps.filter((s, i) => i !== 1 && s.outline === "dashed").length === 0, `${scheme}: a verdict other than SKIP has a dashed outline`);
  }
});

await run("S23", "the switch overrides the system, is remembered and survives a reload", async () => {
  await loadPage({ scheme: "dark" });
  expect((await theme()) === "dark" && (await stored()) === null, "not dark with nothing stored to start with");
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "light", `after the click data-theme is ${await theme()}`);
  expect((await stored()) === "light", `stored is ${await stored()}`);
  expect((await ev("document.getElementById('theme-switch').getAttribute('aria-pressed')")) === "false", "aria-pressed is not false in light");
  await ev("window.__old = true");
  await send("Page.navigate", { url: base + "/" });
  await waitFor("!window.__old && document.readyState === 'complete' && document.querySelector('tr[data-id]')", "the reload");
  expect((await ev("matchMedia('(prefers-color-scheme: dark)').matches")), "the emulated system is not dark after the reload");
  expect((await theme()) === "light" && (await bodyBg()) === LIGHT_BG, `after the reload data-theme is ${await theme()} and the background ${await bodyBg()}`);
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "dark" && (await stored()) === "dark", "the second click did not go back to dark");
  expect((await ev("document.getElementById('theme-switch').getAttribute('aria-pressed')")) === "true", "aria-pressed is not true in dark");
});

await run("S24", "a browser that refuses storage still opens, follows the system, and the switch works for the session", async () => {
  const errorsBefore = pageErrors.length;
  await loadPage({ scheme: "dark", before: "Storage.prototype.getItem = function () { throw new Error('storage refuses'); }; Storage.prototype.setItem = function () { throw new Error('storage refuses'); };" });
  expect((await theme()) === "dark", `with a refusing storage and the system in dark, data-theme is ${await theme()}`);
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "light", "the switch did not work for the session");
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "dark", "the second click did not return to dark");
  await open();
  expect((await ev(count(".wfp-step"))) === 7, "the panel does not open");
  expect(pageErrors.length === errorsBefore, `page errors: ${pageErrors.slice(errorsBefore).join(" | ")}`);
});

await run("S25", "the first paint is already in the stored theme", async () => {
  await loadPage({ scheme: "light", stored: "dark", before: `window.__first = null;
    new PerformanceObserver((list, obs) => { if (window.__first) return;
      window.__first = { theme: document.documentElement.dataset.theme, bg: document.body ? getComputedStyle(document.body).backgroundColor : 'no body yet' };
    }).observe({ type: 'paint', buffered: true });` });
  await waitFor("window.__first", "a first paint");
  const first = await ev("window.__first");
  expect(first.theme === "dark", `at the first paint data-theme was ${first.theme}`);
  expect(first.bg === DARK_BG, `at the first paint the body background was ${first.bg}`);
});

await run("S26", "the table, the tabs, the workflow map and the document pane are dark in the dark theme", async () => {
  await loadPage({ scheme: "dark" });
  // --surface2 under the --soft wash (the segmented control's track is a deliberate wash of the same family)
  const soft = (await tokenRgb("--soft")).match(/[\d.]+/g).map(Number);
  const s2 = (await tokenRgb("--surface2")).match(/[\d.]+/g).map(Number);
  const limit = rgbLum(`rgb(${[0, 1, 2].map((i) => soft[i] * soft[3] + s2[i] * (1 - soft[3])).join(",")})`) + 0.001;
  const selectors = ["table", "th", "td", ".tabs", ".tab", ".search", ".act", ".docpane", ".doc", ".doc pre", ".node", ".panel"];
  const seen = {};
  const tooLight = [];
  const look = async () => {
    const out = await ev(`(${surfaceLuminances})(${JSON.stringify(selectors)}, ${limit})`);
    for (const [sel, n] of Object.entries(out.seen)) seen[sel] = (seen[sel] || 0) + n;
    tooLight.push(...out.tooLight);
  };
  await look();
  await tab("history");
  await look();
  await tab("workflow");
  await waitFor(`${count(".map .node")} === 7`, "the map");
  await look();
  await tab("documents");
  await chooseDocsRepo();
  await ev(`Array.from(document.querySelectorAll('.lines li')).find((li) => li.querySelector('.pill').textContent === 'design').querySelector('.act').click()`);
  await waitFor(`${text(".docpane .doc")}.length > 100`, "the document pane");
  await look();
  const missing = selectors.filter((s) => !seen[s]);
  expect(missing.length === 0, `never on screen while visiting the tabs: ${missing.join(", ")}`);
  expect(tooLight.length === 0, `lighter than the window's surface2: ${tooLight.slice(0, 5).join(" | ")}`);
});

await run("S27", "a diagram is drawn in the colours of the theme and redrawn in place when the theme changes", async () => {
  const colours = () => ev(`(() => { const fig = document.querySelector('.wfp-doc .diagram'); const node = fig.querySelector('g.node rect, g.node polygon, g.node path');
    const label = fig.querySelector('.nodeLabel') || fig.querySelector('g.node span, g.node div');
    const edge = fig.querySelector('path.flowchart-link');
    return { fill: getComputedStyle(node).fill, label: getComputedStyle(label).color, stroke: getComputedStyle(edge).stroke,
      ground: getComputedStyle(fig).backgroundColor, svg: fig.querySelector('svg').id }; })()`);
  const lumOf = rgbLum;
  const ratioOf = (a, b) => { const [h, l] = [lumOf(a), lumOf(b)].sort((x, y) => y - x); return (h + 0.05) / (l + 0.05); };
  const seenColours = {};
  for (const scheme of ["light", "dark"]) {
    await loadPage({ scheme });
    await openPanelReady();
    await openDiagrams();
    const c = await colours();
    seenColours[scheme] = c;
    expect(ratioOf(c.label, c.fill) >= 4.5, `${scheme}: the label ${c.label} on the node ${c.fill} is ${ratioOf(c.label, c.fill).toFixed(2)}`);
    expect(ratioOf(c.stroke, c.ground) >= 3, `${scheme}: the edge ${c.stroke} on the grid ground ${c.ground} is ${ratioOf(c.stroke, c.ground).toFixed(2)}`);
  }
  expect(seenColours.light.fill !== seenColours.dark.fill, "the node fill is the same in both themes");
  // redrawn in place: the figure element stays, its svg is a new one, the scroll of the document does not move
  await ev(`(() => { const fig = document.querySelector('.wfp-doc .diagram'); fig.dataset.marker = 'kept'; const d = document.querySelector('.wfp-doc'); d.scrollTop = 40; window.__scroll = d.scrollTop; })()`);
  const before = await colours();
  await ev("document.getElementById('theme-switch').click()");
  await waitFor(`document.querySelector('.wfp-doc .diagram').querySelector('svg').id !== ${JSON.stringify(before.svg)}`, "a new svg in the same figure");
  const after = await colours();
  expect(await ev(`document.querySelectorAll('.wfp-doc .diagram[data-marker="kept"]').length > 0`), "the figure element was replaced, not redrawn in place");
  expect(after.fill !== before.fill, `the node fill did not change with the theme (${before.fill})`);
  expect((await ev("document.querySelector('.wfp-doc').scrollTop")) === (await ev("window.__scroll")), "the document scrolled when the theme changed");
});

await run("S28", "the diagram sits on the isometric grid", async () => {
  await loadPage({ scheme: "light" });
  await openPanelReady();
  await openDiagrams();
  const image = await ev(`getComputedStyle(document.querySelector('.wfp-doc .diagram'), '::before').backgroundImage`);
  expect((image.match(/repeating-linear-gradient/g) || []).length === 2, `the grid's background-image is ${image.slice(0, 120)}`);
});

await run("S29", "an enabled prev/next button turns to the ink colour on hover (row-view: the x button, which turned red, is gone)", async () => {
  await reset();
  await open();
  await sleep(700);
  const live = ".wfp-move:not(:disabled)";
  expect((await ev(count(live))) >= 1, "no enabled prev/next button");
  await hover(live);
  await sleep(450);
  const bg = await ev(`getComputedStyle(document.querySelector(${JSON.stringify(live)})).backgroundColor`);
  expect(bg === (await tokenRgb("--fg")), `the hovered background is ${bg}, --fg is ${await tokenRgb("--fg")}`);
  await parkMouse();
});

await run("S30", "an artefact chip carries its kind's glyph and colour and lifts on hover", async () => {
  await reset();
  const glyphs = { spec: "§", handoff: "§", design: "◇", "spec-model": "◇", model: "◇", mockup: "▣", report: "≣" };
  stub.workflow = (real) => {
    const byKind = {};
    for (const p of real.phases) for (const a of p.artefacts) byKind[a.kind] = byKind[a.kind] || a;
    // phase 4 keeps the cards of the plain stage; phase 6 is a design phase (#127) and keeps only the link cards
    return { ...real, phases: real.phases.map((p) => (p.n === 4 ? { ...p, artefacts: Object.values(byKind) } : p)) };
  };
  stub.features = derived(6, passedUpTo(6), { links: { ...mine.links, pr: "https://example.com/pr/1" } });
  await ev("refresh()");
  await open();
  await step(4);
  await waitFor(`${count(".wfp-card")} >= 3`, "the cards of phase 4");
  const chips = () => ev(`Array.from(document.querySelectorAll('.wfp-card')).map((c) => ({ kind: c.classList.contains('link') ? 'link' : c.title,
    glyph: getComputedStyle(c, '::before').content.replace(/"/g, ''), k: getComputedStyle(c, '::before').color }))`);
  const cards = await chips();
  await step(6);
  await waitFor(`${count(".wfp-card.link")} >= 1`, "the link card of phase 6");
  cards.push(...(await chips()));
  const wanted = { ...glyphs, link: "↗" };
  const kinds = new Set();
  for (const c of cards) {
    expect(wanted[c.kind] === c.glyph, `a ${c.kind} chip carries "${c.glyph}", expected "${wanted[c.kind]}"`);
    kinds.add(wanted[c.kind]);
  }
  expect(kinds.size >= 3, `only the glyphs ${[...kinds].join(" ")} are on screen: the kinds were not all exercised`);
  expect(new Set(cards.map((c) => c.k)).size >= 3, "the chips do not carry different kind colours");
  await step(4);
  await waitFor(`${count(".wfp-card")} >= 3`, "the cards of phase 4 again");
  // the hover is judged on a settled page: the panel's slide-in and the cards' own transitions are over, and the
  // pointer was moved before (a tab that never saw the mouse ignores its first hover)
  await waitFor("document.getAnimations().every((a) => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)", "the finite animations to finish");
  await parkMouse();
  await hover(".wfp-card");
  await waitFor(`document.querySelector('.wfp-card').matches(':hover') && document.querySelector('.wfp-card').getAnimations().every((a) => a.playState !== 'running')`, "the hovered card's transition to finish");
  const m = await matrixOf(".wfp-card");
  expect(m && m[5] === -1, `hover transform is matrix(${m}), expected a translateY(-1px)`);
  await parkMouse();
});

await run("S31", "every control shows a focus ring when reached by keyboard", async () => {
  await reset();
  await open();
  const ring = await tokenRgb("--ring");
  // phase 4 keeps its cards; phase 5 is a design phase (#127) whose document is a fold row, a control of its own
  const wantedAt = { 4: [".tab", "#theme-switch", ".wfp-back-pill", ".wfp-step", ".wfp-card", ".wfp-foot .act"],
    5: [".tab", ".wfp-step", ".wfp-fold-h", ".wfp-foot .act"] };
  for (const phase of [4, 5]) {
    await step(phase);
    await waitFor(`${count(".wfp-foot .act")} >= 1 && ${count(phase === 4 ? ".wfp-card" : "details.wfp-fold")} >= 1`, `the foot buttons and the controls of phase ${phase}`);
    await sleep(700);
    const wanted = wantedAt[phase];
    const reached = {};
    await ev("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)");
    for (let i = 0; i < 160 && wanted.some((w) => !reached[w]); i++) {
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      const at = await ev(`(() => { const e = document.activeElement; const cs = getComputedStyle(e); return { hit: ${JSON.stringify(wanted)}.filter((w) => e.matches(w)),
        style: cs.outlineStyle, width: parseFloat(cs.outlineWidth), color: cs.outlineColor, name: e.tagName + '.' + e.className }; })()`);
      for (const w of at.hit) {
        if (!reached[w]) reached[w] = at;
        expect(at.style === "solid" && at.width >= 2 && at.color === ring, `${at.name} focused by keyboard has outline ${at.style} ${at.width}px ${at.color}, the ring is ${ring}`);
      }
    }
    const missing = wanted.filter((w) => !reached[w]);
    expect(missing.length === 0, `phase ${phase}: never reached with Tab: ${missing.join(", ")}`);
  }
});

await run("S32", "a press gives feedback", async () => {
  await reset();
  await open();
  await step(5);
  await waitFor(`${count(".wfp-foot .wfp-back")} === 1 && ${count("details.wfp-fold.design")} === 1`, "Back, and the design fold above it");
  await ev(`document.querySelector('details.wfp-fold.design > summary').click()`);
  await waitFor(`${text(".wfp-doc")}.length > 200`, "the design drawn under Back");
  await sleep(700);
  await hover(".wfp-foot .wfp-back");
  await sleep(250);
  await pointer("mousePressed", ".wfp-foot .wfp-back");
  await sleep(250);
  const pressed = await ev(`(() => { const cs = getComputedStyle(document.querySelector('.wfp-foot .wfp-back')); return { t: cs.transform, s: cs.boxShadow }; })()`);
  expect(/matrix\(1, 0, 0, 1, 2, 2\)/.test(pressed.t), `pressed, the transform is ${pressed.t}`);
  expect(pressed.s === "none", `pressed, the box-shadow is ${pressed.s}`);
  await pointer("mouseReleased", ".wfp-foot .act");
  await sleep(300);
  const released = await ev(`(() => { const e = document.querySelector('.wfp-foot .act'); return e ? getComputedStyle(e).transform : 'none'; })()`);
  expect(!/matrix\(1, 0, 0, 1, 2, 2\)/.test(released), `after the release the transform is still ${released}`);
  await parkMouse();
});

await run("S33", "the live dot pings and the line flows only while an agent is active", async () => {
  await reset();
  stub.features = derived(3, passedUpTo(3), { agent: AGENT });
  await ev("refresh()");
  await open();
  await sleep(600);
  const busy = await running();
  expect(busy.includes("ping") && busy.includes("flow") && busy.includes("beat"), `with an agent the running animations are ${busy.join(",") || "none"}`);
  stub.features = derived(3, passedUpTo(3), { agent: null });
  await ev("refresh()");
  await waitFor(`${count(".wfp-agent:not([hidden])")} === 0`, "the agent tag to go");
  await sleep(300);
  const idle = await running();
  expect(!idle.includes("ping") && !idle.includes("flow") && !idle.includes("beat"), `with no agent these still run: ${idle.join(",")}`);
  expect(await ev(`getComputedStyle(document.querySelector('.wfp-step.live .wfp-dot')).opacity === '0.35'`), "the idle live dot is not drawn at 35 %");
});

await run("S34", "the problem line has a blinking caret", async () => {
  await reset();
  stub.features = derived(3, ["pass", "pass", "pass", "fail", "todo", "todo", "todo"], { problems: { 3: ["no invariants table"] } });
  await ev("refresh()");
  await open();
  await waitFor(`${count(".wfp-problem")} === 1`, "the problem line");
  await sleep(300);
  expect((await running()).includes("caret"), `the running animations are ${(await running()).join(",") || "none"}`);
});

await run("S35", "every link of a flowchart carries a packet", async () => {
  await reset();
  await open();
  await openDiagrams();
  const out = await ev(`(() => { const fig = Array.from(document.querySelectorAll('.wfp-doc .diagram')).find((f) => f.querySelector('path.flowchart-link'));
    const edges = Array.from(fig.querySelectorAll('path.flowchart-link')); const packets = Array.from(fig.querySelectorAll('.packet'));
    const near = (e, p) => { const a = e.getBoundingClientRect(), b = p.getBoundingClientRect(); const x = b.left + b.width / 2, y = b.top + b.height / 2;
      return x >= a.left - 8 && x <= a.right + 8 && y >= a.top - 8 && y <= a.bottom + 8; };
    const svg = fig.querySelector('svg').getBoundingClientRect();
    return { edges: edges.length, packets: packets.length, animated: packets.filter((p) => p.getAnimations().some((a) => a.animationName === 'packet' && a.playState === 'running')).length,
      onTheirEdge: edges.filter((e, i) => packets[i] && near(e, packets[i])).length,
      inside: packets.filter((p) => { const r = p.getBoundingClientRect(); return r.left >= svg.left - 4 && r.right <= svg.right + 4 && r.top >= svg.top - 4 && r.bottom <= svg.bottom + 4; }).length }; })()`);
  expect(out.edges > 0, "no flowchart edge was found as path.flowchart-link (premise P9)");
  console.log(`   info S35: ${out.edges} path.flowchart-link edges, ${out.packets} packets, ${out.animated} running, ${out.onTheirEdge} on their edge`);
  expect(out.packets === out.edges, `${out.packets} packets for ${out.edges} edges`);
  expect(out.animated === out.packets, `${out.animated} of ${out.packets} packets run the packet animation`);
  expect(out.onTheirEdge === out.edges, `${out.onTheirEdge} of ${out.edges} packets sit on their edge`);
  expect(out.inside === out.packets, `${out.inside} of ${out.packets} packets are inside the figure`);
});

await run("S36", "the panel slides in when it opens", async () => {
  await reset();
  const names = await ev(`(() => { document.querySelector(${JSON.stringify(rowSel(mine.id))}).click(); return document.getElementById('wfpanel').getAnimations().map((a) => a.animationName); })()`);
  expect(names.includes("slidein"), `the panel's animations right after the click are ${names.join(",") || "none"}`);
});

await run("S37", "a passed phase is stamped when the dot moves past it, for the whole animation, and not at a repaint without a move", async () => {
  await reset();
  stub.features = derived(5, passedUpTo(5), { agent: AGENT });
  await ev("refresh()");
  await open();
  await waitFor("document.getElementById('wfpanel').getAnimations().every((a) => a.animationName !== 'slidein' || a.playState !== 'running')", "the panel to settle");
  expect((await ev(count(".wfp-step.stamped"))) === 0, "a step is stamped before any move");
  await ev(`(() => { window.__stamp = null; window.__paints = 0;
    new MutationObserver(() => { const s = document.querySelector('.wfp-step.stamped');
      if (s && !window.__stamp) window.__stamp = { at: performance.now(), n: Array.from(document.querySelectorAll('.wfp-step')).indexOf(s),
        word: s.querySelector('.wfp-word'), anims: s.querySelector('.wfp-word').getAnimations().map((a) => a.animationName + ':' + a.playState) };
      if (!s && window.__stamp && !window.__stamp.gone) window.__stamp.gone = performance.now(); }).observe(document.querySelector('.wfp-steps'), { subtree: true, attributes: true, childList: true });
    const paint = window.paintPanel; window.paintPanel = function () { window.__paints++; return paint.apply(this, arguments); }; })()`);
  stub.features = derived(6, passedUpTo(6), { agent: AGENT });
  await ev("refresh()");
  await waitFor("window.__stamp", "the stamp");
  const stamp = await ev("({ n: window.__stamp.n, anims: window.__stamp.anims })");
  expect(stamp.n === 5, `step ${stamp.n} was stamped, expected 5`);
  expect(stamp.anims.some((a) => a.startsWith("stamp:running")), `the stamped word's animations are ${stamp.anims.join(",") || "none"}`);
  await waitFor("window.__stamp.gone", "the stamp to be taken off", 5000);
  const out = await ev("({ lasted: Math.round(window.__stamp.gone - window.__stamp.at), paints: window.__paints, same: window.__stamp.word.isConnected })");
  console.log(`   info S37: the stamp stayed ${out.lasted} ms over ${out.paints} paints (animation 600 ms)`);
  expect(out.paints >= 2, `only ${out.paints} paint(s) happened during the stamp: the check does not cover a repaint`);
  expect(out.lasted >= 560, `the stamp lasted ${out.lasted} ms, the animation is 600 ms`);
  expect(out.same, "the stamped word was rebuilt by a repaint (its animation would restart)");
  await ev("refresh()");
  await sleep(900);
  expect((await ev(count(".wfp-step.stamped"))) === 0, "a repaint with the dot on the same phase stamped a step");
});

await run("S38", "with prefers-reduced-motion nothing animates and hover and press move nothing", async () => {
  await loadPage({ motion: "reduce" });
  stub.features = derived(3, ["pass", "pass", "pass", "fail", "todo", "todo", "todo"], { agent: AGENT, problems: { 3: ["a problem"] } });
  await ev("refresh()");
  await open();
  await openDiagrams();
  await sleep(500);
  const all = await ev(`document.getAnimations().filter((a) => a.playState === 'running').map((a) => a.animationName)`);
  expect(all.length === 0, `these animations still run: ${all.join(",")}`);
  expect((await ev(count(".diagram .packet"))) > 0 && (await ev(`Array.from(document.querySelectorAll('.diagram .packet')).every((p) => getComputedStyle(p).display === 'none')`)), "the packets are not hidden");
  await hover(".wfp-back-pill");
  await sleep(450);
  expect((await ev(`getComputedStyle(document.querySelector('.wfp-back-arrow')).transform`)) === "none", "the hovered Back arrow still slides");
  await parkMouse();
});

await run("S39", "with prefers-reduced-motion nothing is lost", async () => {
  const stubs = () => { stub.features = derived(5, passedUpTo(5), { agent: AGENT, problems: { 5: ["no design file"] } }); };
  await loadPage({});
  stubs();
  await ev("refresh()");
  await open();
  await sleep(900);
  const normal = await panelText();
  await loadPage({ motion: "reduce" });
  stubs();
  await ev("refresh()");
  await open();
  await sleep(900);
  const reduced = await panelText();
  expect(normal === reduced, `the panel's text differs:\n  normal  ${normal.slice(0, 160)}\n  reduced ${reduced.slice(0, 160)}`);
  expect(reduced.includes("● live phase") && reduced.includes("agent active"), "the live phase or the agent tag is missing");
  const dot = await ev(`(() => { const d = document.querySelector('.wfp-step.live .wfp-no'); const cs = getComputedStyle(d); const ring = getComputedStyle(document.querySelector('.wfp-step.live .wfp-dot'));
    return { display: cs.display, opacity: cs.opacity, bg: cs.backgroundColor, w: d.getBoundingClientRect().width, ring: ring.display, ringBorder: ring.borderTopColor }; })()`);
  expect(dot.display !== "none" && dot.opacity === "1" && dot.w > 10 && dot.bg === (await tokenRgb("--hot")), `the live dot is ${JSON.stringify(dot)}`);
  expect(dot.ring !== "none" && dot.ringBorder === (await tokenRgb("--hot")), `the live dot's ring is ${JSON.stringify(dot)}`);
  expect((await ev(count(".wfp-step.lit"))) === 5, "the progress line is no longer lit");
});

await run("S40", "at the width of a phone the view fills the window's width, the table is not shown and nothing scrolls sideways (row-view: no fixed layer)", async () => {
  await loadPage({ width: 375 });
  await open();
  await sleep(700);
  const m = await ev(`(() => { const r = document.getElementById('wfpanel').getBoundingClientRect(); const t = document.querySelector('#view table');
    return { l: r.left, t: r.top, w: r.width, h: r.height, iw: innerWidth, ih: innerHeight, sw: document.documentElement.scrollWidth, tableShown: t ? t.getClientRects().length > 0 : false }; })()`);
  expect(m.w >= m.iw - 30 && m.l >= 0 && m.l <= 15, `the view's rectangle is ${JSON.stringify(m)}`);
  expect(m.sw <= m.iw, `scrollWidth ${m.sw} > innerWidth ${m.iw}`);
  expect(!m.tableShown, "the table is shown behind the panel");
});

await run("S41", "at the width of a phone the stepper is a strip of seven numbers on one row", async () => {
  const strip = await ev(`Array.from(document.querySelectorAll('.wfp-step')).map((s) => { const r = s.getBoundingClientRect(); const nm = s.querySelector('.wfp-nm').getBoundingClientRect();
    return { top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), no: s.querySelector('.wfp-no').textContent, nmW: Math.round(nm.width), text: s.textContent }; })`);
  expect(strip.length === 7, `${strip.length} steps`);
  expect(new Set(strip.map((s) => s.top)).size === 1, `the tops are ${strip.map((s) => s.top).join(",")}`);
  expect(strip.every((s) => s.w >= 36 && s.h >= 36), `a cell is smaller than 36 px: ${strip.map((s) => s.w + "x" + s.h).join(" ")}`);
  expect(strip.every((s, i) => s.no === String(i)), "a step does not show its number");
  expect(strip.every((s) => s.nmW <= 1), "a step still shows its name");
  expect(strip.slice(0, 5).every((s) => /PASS|TODO|FAIL|SKIP/.test(s.text)), "the verdict word is gone from the accessible text");
});

await run("S42", "at the width of a phone the Back pill returns to the table and the focus to its row", async () => {
  await loadPage({ width: 375 });   // its own setup: the row open at 375 px, not the state S41 leaves behind
  await open();
  expect((await ev(`getComputedStyle(document.querySelector('.wfp-back-pill')).display`)) !== "none", "the pill is hidden at 375 px");
  expect((await ev(count(".wfp-close"))) === 0, "an x is shown at 375 px");
  await ev("document.querySelector('.wfp-back-pill').click()");
  await waitFor("document.querySelector('#wfpanel').hidden", "the panel to close");
  expect(await ev(`document.querySelector('#view table').getClientRects().length > 0`), "the table is not shown");
  expect(await ev(`document.activeElement && document.activeElement.dataset.id === ${JSON.stringify(mine.id)}`), "the focus did not return to the row");
  await loadPage({});
  await open();
  expect((await ev(`getComputedStyle(document.querySelector('.wfp-back-pill')).display`)) !== "none", "the pill is hidden at 1280 px (row-view: it shows at every width)");
});

await run("S43", "at the width of a phone no state scrolls sideways, in either theme", async () => {
  const long = "x".repeat(90);
  const states = {
    ready: () => { stub.features = rowWith((r) => ({ slug: long }));
      stub.workflow = (real) => ({ ...real, phases: real.phases.map((p) => (p.n === 5 ? { ...p, artefacts: p.artefacts.map((a) => ({ ...a, name: "y".repeat(90) })) } : p)) }); },
    skipped: () => { stub.features = derived(3, ["pass", "skip", "pass", "todo", "todo", "todo", "todo"]); },
    delivered: () => { stub.features = rowWith((r) => ({ agent: AGENT, phase: { kind: "delivered", phase: null, verdicts: r.phase.verdicts } })); },
    computing: () => { stub.features = rowWith(() => ({ agent: null, phase: { kind: "computing", phase: null, verdicts: [] } })); },
    error: () => { stub.workflow = () => ({ status: 503, body: {} }); },
  };
  for (const scheme of ["light", "dark"]) {
    for (const [name, arrange] of Object.entries(states)) {
      await loadPage({ scheme, width: 375 });
      stub.features = null; stub.workflow = null;
      arrange();
      await ev("refresh()");
      await open(mine.id, false);
      await sleep(900);
      const m = await ev(`({ page: document.documentElement.scrollWidth, inner: innerWidth, panel: document.getElementById('wfpanel').scrollWidth, panelW: document.getElementById('wfpanel').clientWidth })`);
      const wide = m.page <= m.inner && m.panel <= m.panelW ? [] : await ev(`Array.from(document.querySelectorAll('#wfpanel *')).filter((e) => !e.closest('.wfp-doc') && e.getBoundingClientRect().right > innerWidth + 0.5 && e.getClientRects().length)
        .slice(0, 4).map((e) => e.tagName + '.' + String(e.className).slice(0, 30) + ' right=' + Math.round(e.getBoundingClientRect().right))`);
      expect(wide.length === 0, `${scheme}/${name}: page ${m.page}/${m.inner}, panel ${m.panel}/${m.panelW}; past the edge: ${wide.join(" | ")}`);
    }
  }
});

await run("S44", "the window's switch is reachable at the width of a phone and is the only one: the view has none (row-view, owner 2026-11-19)", async () => {
  await loadPage({ scheme: "light", width: 375 });
  const shown = (sel) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; })()`);
  expect(await shown("#theme-switch"), "the window's switch is not on screen at 375 px");
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "dark", `the window's switch left the theme at ${await theme()}`);
  await open();
  await sleep(700);
  expect((await ev("document.querySelectorAll('.theme-switch').length")) === 1, `${await ev("document.querySelectorAll('.theme-switch').length")} theme switches with the view open`);
  expect((await ev("document.querySelectorAll('.wfp-top .theme-switch').length")) === 0, "the view's bar still has a theme switch");
  expect(await shown("#theme-switch"), "the window's switch is not on screen at 375 px with the view open");
  await ev("document.getElementById('theme-switch').click()");
  expect((await theme()) === "light", `the window's switch left the theme at ${await theme()} with the view open`);
  expect((await ev(`document.getElementById('theme-switch').getAttribute('aria-pressed')`)) === "false", "aria-pressed is not false after the switch back to light");
});

await run("S45", "the Other worktrees group is absent with none, closed with some, and opens on a click of its summary", async () => {
  await loadPage({ scheme: "light" });
  const others = (n) => (real) => ({ ...real, other_worktrees: Array.from({ length: n }, (_, i) => ({ ...mine, id: `other${i}`.padEnd(12, "x"), slug: `leftover-${i}`, agent: null })) });
  stub.features = others(0);
  await ev("refresh()");
  await waitFor("document.querySelector('tr[data-id]')", "the table with no other worktree");
  await sleep(300);
  expect((await ev("document.querySelector('details.other') === null")) === true, "a group is drawn with 0 other worktrees");
  stub.features = others(2);
  await ev("refresh()");
  await waitFor("document.querySelector('details.other')", "the group with 2 other worktrees");
  expect((await ev("document.querySelector('details.other').open")) === false, "the group starts open");
  const head = await ev("document.querySelector('details.other > summary').textContent");
  expect(head.includes("Other worktrees (2)"), `the summary reads "${head}"`);
  await ev("document.querySelector('details.other > summary').click()");
  await waitFor("document.querySelector('details.other').open === true", "the group to open on a click");
});

// --- S46-S51 (#105): the window updates by component ------------------------------------------------------------

const NOT_MODIFIED = { status: 304, body: null };
const tagged = (answer) => (real, url, tag) => (tag ? NOT_MODIFIED : answer(real));   // a conditional poll gets the 304
const lastTag = (path) => (sentTags.filter(([p]) => p === path).pop() || [null, null])[1];

await run("S46", "a poll answered 304 redraws nothing, keeps the scroll and says draw unchanged in the footer", async () => {
  await loadPage({});
  await ev(`(() => { const t = document.querySelector('#view table'); t.__m = 1; const tr = document.querySelector('#view tbody tr'); tr.__m = 1; tr.cells[0].__m = 1; window.scrollTo(0, 30); window.__y = window.scrollY; })()`);
  stub.features = tagged((real) => real);
  await ev("refresh()");
  await sleep(300);
  const kept = await ev(`({ table: document.querySelector('#view table').__m === 1, row: document.querySelector('#view tbody tr').__m === 1, cell: document.querySelector('#view tbody tr').cells[0].__m === 1, y: window.scrollY === window.__y })`);
  expect(kept.table && kept.row && kept.cell, `the marks of the table, row or cell are gone: ${JSON.stringify(kept)}`);
  expect(kept.y, "the scroll offset moved");
  expect(lastTag("/api/features") !== null, "the page sent no If-None-Match");
  const footer = await ev("document.getElementById('timings').textContent");
  expect(/draw unchanged \(last [\d.]+ (ms|s)\)/.test(footer), `the footer reads "${footer}"`);
  expect((await ev("document.getElementById('fresh').textContent")) === "refreshed 0 s ago", "a 304 did not reset the refreshed line");
  // key R asks whole: with a tag saved, the request it makes carries none
  stub.features = null;
  await ev("refresh()");
  await waitFor("state.etag !== null", "the page to hold a tag");
  await open(mine.id, false);
  let n = sentTags.length;
  await press("r");
  for (const until = Date.now() + 5000; Date.now() < until && !sentTags.slice(n).some(([p]) => p === "/api/features");) await sleep(50);
  const forced = sentTags.slice(n).find(([p]) => p === "/api/features");
  expect(forced !== undefined, "key R sent no /api/features request");
  expect(forced[1] === null, `key R sent If-None-Match ${forced[1]}`);
  await ev("closePanel()");
  // a failed poll drops the tag: the request after a 503 is asked whole, the banner shows the error and then clears
  await ev("refresh()");
  await waitFor("state.etag !== null", "the page to hold a tag");
  n = sentTags.length;
  stub.features = () => { stub.features = null; return { status: 503, body: { error: "boom" } }; };
  await ev("refresh()");
  await waitFor("document.getElementById('banner').textContent.includes('boom') && !document.getElementById('banner').hidden", "the error banner");
  await ev("refresh()");
  await waitFor("document.getElementById('banner').hidden", "the banner to clear");
  const sent = sentTags.slice(n).filter(([p]) => p === "/api/features").map(([, t]) => t);
  expect(sent.length >= 2 && sent[0] !== null && sent[1] === null, `the tags of the 503 and of the request after it are ${JSON.stringify(sent)}`);
});

await run("S47", "one changed row is redrawn alone: the others, the selection, the focus and the filter box stay", async () => {
  await loadPage({});
  const cur = () => ev("JSON.parse(JSON.stringify(state.data))");
  const data = await cur();
  await open(other.id, false);
  await ev(`document.querySelectorAll('#view tbody tr').forEach((tr) => { tr.__m = 1; })`);   // row-view: the table is hidden while a row is open, so no row can hold the focus
  stub.features = () => ({ ...data, open: data.open.map((r) => (r.id === mine.id ? { ...r, agent: AGENT } : r)) });
  await ev("refresh()");
  const after = await ev(`({ gone: Array.from(document.querySelectorAll('#view tbody tr')).filter((tr) => tr.__m !== 1).map((tr) => tr.dataset.id),
    sel: Array.from(document.querySelectorAll('#view tbody tr.sel')).map((tr) => tr.dataset.id), focus: document.activeElement && document.activeElement.dataset.id,
    panel: !document.getElementById('wfpanel').hidden, inView: document.getElementById('wfpanel').contains(document.activeElement) })`);
  expect(after.gone.length === 1 && after.gone[0] === mine.id, `the redrawn rows are ${JSON.stringify(after.gone)}, not only ${mine.id}`);
  expect(after.sel.length === 1 && after.sel[0] === other.id, `the selected rows are ${JSON.stringify(after.sel)}`);
  expect(after.inView, `the focus left the view for ${after.focus}`);
  expect(after.panel, "the panel closed");
  // the selected row itself changes: its redrawn node is painted selected, and it is the only selected one
  await ev(`document.querySelectorAll('#view tbody tr').forEach((tr) => { tr.__m = 1; })`);
  stub.features = () => ({ ...data, open: data.open.map((r) => (r.id === other.id ? { ...r, agent: { ...AGENT, pid: 777001 } } : r.id === mine.id ? { ...r, agent: AGENT } : r)) });
  await ev("refresh()");
  const redrawn = await ev(`({ gone: Array.from(document.querySelectorAll('#view tbody tr')).filter((tr) => tr.__m !== 1).map((tr) => [tr.dataset.id, tr.classList.contains('sel')]),
    sel: Array.from(document.querySelectorAll('#view tbody tr.sel')).map((tr) => tr.dataset.id) })`);
  expect(redrawn.gone.length === 1 && redrawn.gone[0][0] === other.id, `the redrawn rows are ${JSON.stringify(redrawn.gone)}, not only ${other.id}`);
  expect(redrawn.gone[0][1] && redrawn.sel.length === 1 && redrawn.sel[0] === other.id, `the redrawn selected row lost its selection: ${JSON.stringify(redrawn)}`);
  await ev("closePanel()");
  // History: the filter box keeps its value and its focus while a row of the filtered list changes
  const history = await cur();
  expect(history.history.length >= 2, `the History half needs 2 history rows in the real data, there are ${history.history.length}`);
  await tab("history");
  await waitFor("document.querySelector('#view tbody tr')", "the History table");
  const needle = history.history[0].slug.slice(0, 2);
  await ev(`(() => { const f = document.getElementById('filter'); f.value = ${JSON.stringify(needle)}; f.dispatchEvent(new Event('input')); f.focus(); })()`);
  const shown = await ev("Array.from(document.querySelectorAll('#view tbody tr')).map((tr) => tr.dataset.id)");
  expect(shown.length > 0, "the filter shows no history row");
  await ev(`document.querySelectorAll('#view tbody tr').forEach((tr) => { tr.__m = 1; })`);
  stub.features = () => ({ ...history, history: history.history.map((r) => (r.id === shown[0] ? { ...r, agent: AGENT } : r)) });
  await ev("refresh()");
  const box = await ev(`({ value: document.getElementById('filter').value, focus: document.activeElement === document.getElementById('filter'),
    gone: Array.from(document.querySelectorAll('#view tbody tr')).filter((tr) => tr.__m !== 1).map((tr) => tr.dataset.id) })`);
  expect(box.value === needle && box.focus, `the filter box is ${JSON.stringify(box)}`);
  expect(box.gone.length === 1 && box.gone[0] === shown[0], `the redrawn history rows are ${JSON.stringify(box.gone)}`);
});

await run("S48", "the Workflow tab is not rebuilt by a 304 and the Other worktrees group keeps its open state", async () => {
  await loadPage({});
  await tab("workflow");
  await waitFor("document.querySelector('#view .map')", "the workflow map");
  await ev("document.querySelector('#view .map').__m = 1");
  stub.workflow = tagged((real) => real);
  await ev("refresh()");
  await sleep(300);
  expect(await ev("document.querySelector('#view .map').__m === 1"), "the diagram was rebuilt by a 304");
  expect(lastTag("/api/workflow") !== null, "the page sent no If-None-Match to /api/workflow");
  // features that changed while the Workflow tab is open do not rebuild the diagram either
  const held = await ev("JSON.parse(JSON.stringify(state.data))");
  stub.features = () => ({ ...held, other_worktrees: [...held.other_worktrees, { ...mine, id: "otherzzzzzzz", slug: "leftover-z", agent: null }] });
  await ev("refresh()");
  await waitFor(`state.data.other_worktrees.length === ${held.other_worktrees.length + 1}`, "the changed features");
  await sleep(300);
  expect(await ev("document.querySelector('#view .map').__m === 1"), "the diagram was rebuilt by a features change");
  stub.features = null;
  await tab("open");
  await waitFor("document.querySelector('#view tbody tr')", "the table");
  const data = await ev("JSON.parse(JSON.stringify(state.data))");
  const others = (n) => () => ({ ...data, other_worktrees: Array.from({ length: n }, (_, i) => ({ ...mine, id: `other${i}`.padEnd(12, "x"), slug: `leftover-${i}`, agent: null })) });
  stub.features = others(2);
  await ev("refresh()");
  await waitFor("document.querySelector('details.other')", "the group");
  await ev("document.querySelector('details.other').__m = 1; document.querySelector('details.other > summary').click()");
  await waitFor("document.querySelector('details.other').open === true", "the group to open");
  stub.features = others(3);
  await ev("refresh()");
  const group = await ev(`({ same: document.querySelector('details.other').__m === 1, open: document.querySelector('details.other').open, head: document.querySelector('details.other > summary').textContent, rows: document.querySelectorAll('details.other tbody tr').length })`);
  expect(group.same && group.open, `the group was rebuilt or closed: ${JSON.stringify(group)}`);
  expect(group.head.includes("Other worktrees (3)") && group.rows === 3, `the group shows ${JSON.stringify(group)}`);
});

await run("S49", "the real server and the real browser: a request with the previous tag is handed to the script as a 304", async () => {
  await loadPage({});
  const out = await ev(`(async () => {
    for (let i = 1; i <= 10; i++) {
      const first = await fetch('/api/features', { cache: 'no-store' });
      const tag = first.headers.get('ETag');
      await first.arrayBuffer();
      const second = await fetch('/api/features', { cache: 'no-store', headers: { 'If-None-Match': tag } });
      const again = second.headers.get('ETag');
      const body = await second.text();
      if (again === tag && second.status === 304 && body === '') return { ok: true, tries: i };
      if (again === tag) return { ok: false, why: 'status ' + second.status + ' for an equal tag' };
      await new Promise((r) => setTimeout(r, 1000));
    }
    return { ok: false, why: 'the content never settled in 10 tries (rerun)' };
  })()`);
  expect(out.ok, `${out.why}`);
  // the Documents tab: an unchanged list is a 304 the page keeps (no body read, nothing redrawn, the filter stays)
  await tab("documents");
  await waitFor("state.docRepo && state.docList !== null && state.docEtag.etag !== null", "the documents list");
  await ev(`(() => { const f = document.getElementById('filter'); f.value = 'd'; f.dispatchEvent(new Event('input')); })()`);
  await ev("document.getElementById('view').firstElementChild.__m = 1");
  const before = sentTags.length;
  await ev("refresh()");
  for (const until = Date.now() + 5000; Date.now() < until && !sentTags.slice(before).some(([p]) => p.startsWith("/api/documents?repo="));) await sleep(50);
  await sleep(500);
  const docs = await ev(`({ mark: document.getElementById('view').firstElementChild.__m === 1, filter: document.getElementById('filter').value,
    banner: document.getElementById('banner').hidden, failure: state.docFailure, list: state.docList !== null })`);
  expect(docs.mark && docs.filter === "d" && docs.banner && docs.failure === null && docs.list, `the Documents tab after a 304: ${JSON.stringify(docs)}`);
  const asked = sentTags.slice(before).filter(([p]) => p.startsWith("/api/documents?repo="));
  expect(asked.length > 0 && asked.every(([, t]) => t !== null), `the documents request carried no tag: ${JSON.stringify(asked)}`);
  await ev("chooseTab('open')");
});

await run("S50", "the Open table stays above the Other worktrees group when a row appears after an empty list, and in every transition", async () => {
  await loadPage({});
  const data = await ev("JSON.parse(JSON.stringify(state.data))");
  const group = [{ ...mine, id: "otherAxxxxxx", slug: "leftover-0", agent: null }];
  const feed = (open) => () => ({ ...data, open, other_worktrees: group });
  // the order of the nodes of the view: the table (".list") must precede the group when both exist
  const order = () => ev(`(() => { const v = document.getElementById('view'); const t = v.querySelector(':scope > .list'); const g = v.querySelector(':scope > details.other');
    return { table: !!t, group: !!g, before: !!(t && g && (t.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING)) }; })()`);
  const show = async (open, what) => { stub.features = feed(open); await ev("refresh()"); await waitFor(`document.querySelector('details.other') && (${open.length > 0}) === !!document.querySelector('#view > .list')`, what); return order(); };
  let o = await show([], "the group with an empty Open list");
  expect(!o.table && o.group, `empty Open with a group: ${JSON.stringify(o)}`);
  o = await show(data.open, "the table after an empty list");
  expect(o.table && o.group && o.before, `a row appeared after an empty list: the table is not above the group ${JSON.stringify(o)}`);
  o = await show([], "the empty list again");
  expect(!o.table && o.group, `rows then empty with a group: ${JSON.stringify(o)}`);
  o = await show(data.open, "the table again");
  expect(o.before, `the second time a row appeared the table is not above the group ${JSON.stringify(o)}`);
  stub.features = () => ({ ...data, open: data.open, other_worktrees: [] });   // the group goes
  await ev("refresh()");
  await waitFor("!document.querySelector('details.other')", "the group to go");
  stub.features = feed(data.open);   // and comes back: after the table
  await ev("refresh()");
  await waitFor("document.querySelector('details.other')", "the group to come back");
  o = await order();
  expect(o.table && o.before, `the group came back above the table ${JSON.stringify(o)}`);
});

await run("S51", "a 304 while a row is computing keeps the fast poll", async () => {
  await loadPage({});
  const data = await ev("JSON.parse(JSON.stringify(state.data))");
  const computing = { ...data, open: data.open.map((r) => (r.id === mine.id ? { ...r, phase: { kind: "computing" } } : r)) };
  stub.features = tagged(() => computing);
  await ev("state.etag = null; refresh()");               // an untagged request: the computing answer lands
  await waitFor(`state.data.open.some((r) => r.phase.kind === 'computing')`, "the computing row");
  await ev(`state.etag = '"held"'`);                       // the stubbed answer carries no ETag: hand the page one so the next poll is tagged
  await sleep(100);
  const featureRequests = () => sentTags.filter(([p]) => p === "/api/features").length;
  await ev("refresh()");                                   // tagged: a 304 over a snapshot with a computing row
  const from = featureRequests();
  await sleep(4000);                                        // 4 x FAST_MS (each stubbed poll also waits for a real scan): the fast pace polls at least twice, the 10 s pace not at all
  const polled = featureRequests() - from;
  expect(polled >= 2, `${polled} polls in 4 s after a 304 over a computing row (the fast pace polls twice)`);
  stub.features = null;
  await ev("refresh()");
});


// --- S52-S56 (#100): the gate sections of a phase, above the cards ----------------------------------------------

// A phase chosen and its sections drawn (no body still saying "Loading…").
async function stepAndSettle(n) {
  await step(n);
  await waitFor(`${text(".wfp-title")}.startsWith("${n} ")`, `the stage of phase ${n}`);
  await waitFor(`!document.querySelector('.wfp-sec-body .dim')`, `the sections of phase ${n} drawn`);
}
// the section folds of a design phase opened by a click, and their bodies waited for (#127)
async function openSectionFolds() {
  await ev(`document.querySelectorAll('details.wfp-fold.section > summary').forEach((s) => s.click())`);
  await waitFor(`${count("details.wfp-fold.section")} > 0 && !document.querySelector('.wfp-fold-body .dim')`, "the section folds open and drawn");
}
const sectionTitles = () => ev(`Array.from(document.querySelectorAll('.wfp-sec-t')).map((e) => e.textContent)`);

await run("S52", "at phase 4 the first thing of the stage is the block Tickets, the cards come after it, and no title or exit sentence shows a ##", async () => {
  await loadPage({});
  await open();
  await stepAndSettle(4);
  const first = await ev(`(() => { const f = document.querySelector('.wfp-art').firstElementChild; return { cls: f.className, title: (f.querySelector('.wfp-sec-t') || {}).textContent }; })()`);
  expect(first.cls.startsWith("wfp-sec") && first.title === "Tickets", `the first thing of the stage is ${JSON.stringify(first)}`);
  expect(await ev(`(() => { const cards = document.querySelector('.wfp-cards'); const last = Array.from(document.querySelectorAll('.wfp-sec')).pop();
    return Boolean(cards && last && (last.compareDocumentPosition(cards) & Node.DOCUMENT_POSITION_FOLLOWING)); })()`), "the cards do not come after the sections");
  for (let n = 0; n <= 6; n++) {
    await stepAndSettle(n);
    const titles = await sectionTitles();
    expect(titles.every((x) => !x.includes("#")), `phase ${n}: a section title shows a marker: ${JSON.stringify(titles)}`);
    if ((await ev(text(".wfp-idea b"))).startsWith("what leaves phase")) {   // a recorded skip reason is the owner's text, not a title
      const sentence = await ev(`${text(".wfp-idea p")}`);
      expect(!sentence.includes("##"), `phase ${n}: the exit sentence shows a marker: ${sentence}`);
    }
  }
});

await run("S53", "a section with a diagram draws an svg, and a theme switch redraws it in the new theme", async () => {
  await loadPage({ scheme: "light" });
  await open();
  await stepAndSettle(1);
  // phase 1 folds its sections (#127): opening them draws the diagrams of their bodies
  await openSectionFolds();
  await waitFor(`${count(".wfp-fold .diagram > svg")} > 0`, "a drawn diagram in a section");
  await waitFor(`${count(".wfp-fold-body pre code.language-mermaid")} === 0`, "every opened fold drawn: no diagram left as code");
  expect((await ev(count(".wfp-fold .diagram-error"))) === 0, "a diagram of a section failed");
  expect((await ev(count(".wfp-fold-body pre code.language-mermaid"))) === 0, "a diagram is still its code");
  const before = await ev(`document.querySelector('.wfp-fold .diagram svg').id`);
  const was = await theme();
  await ev("document.getElementById('theme-switch').click()");
  await waitFor(`document.querySelector('.wfp-fold .diagram svg').id !== ${JSON.stringify(before)}`, "a new svg after the theme switch");
  expect((await theme()) !== was, "the theme did not change");
});

await run("S54", "regression pin (green before #100): a phase with no contracted heading has no block and still opens its design; a skipped phase has none", async () => {
  await loadPage({});
  await open();
  await stepAndSettle(5);
  expect((await ev(count(".wfp-sec"))) === 0, "a block at phase 5");
  await waitFor(`${count(".wfp-doc")} === 1`, "the design open by itself at phase 5");
  stub.features = withRow((r) => ({ ...r, phase: { ...r.phase,
    verdicts: r.phase.verdicts.map((v) => (v.phase === 2 ? { ...v, status: "skip", reason: "no UI." } : v)) } }));
  await ev("refresh()");
  await sleep(300);
  await step(2);
  await waitFor(`${text(".wfp-nothing")} === "Nothing to read: the phase was skipped."`, "the skipped phase");
  expect((await ev(count(".wfp-sec"))) === 0, "a block on a skipped phase");
});

await run("S55", "a phase whose heading is missing shows the line of the gate as the tip of its slot, and the verdict box keeps the gate's text", async () => {
  await loadPage({});
  stub.features = withRow((r) => ({ ...r, phase: { ...r.phase,
    verdicts: r.phase.verdicts.map((v) => (v.phase === 4 ? { ...v, status: "todo", problems: ["no ## Tickets"] } : v)) } }));
  stub.workflow = (real) => ({ ...real, phases: real.phases.map((p) => (p.n === 4 ? { ...p,
    sections: [{ heading: "Tickets", source: "docs/specs/x.md", state: "missing", id: null, rev: null, note: null }] } : p)) });
  await ev("refresh()");
  await open();
  await step(4);
  await waitFor(`${count(".wfp-sec-miss")} === 1`, "the slot of the missing section");
  expect((await ev(text(".wfp-sec-miss"))) === "The section Tickets is missing.", `the slot reads ${await ev(text(".wfp-sec-miss"))}`);
  expect((await ev(`document.querySelector('.wfp-sec-miss').title`)) === "no ## Tickets", "the tip is not the gate's line");
  expect((await ev(text(".wfp-idea .wfp-problem"))).includes("no ## Tickets"), "the verdict box lost the gate's text");
  expect((await ev(count(".wfp-sec-body"))) === 0, "an empty body next to the slot");
});

await run("S56", "at 340 px the blocks, cards and verdict stack in one column with no sideways scroll", async () => {
  await loadPage({ width: 340 });
  await open();
  await stepAndSettle(1);
  await waitFor(`${count(".wfp-dg.drawn")} > 0`, "a drawn diagram");
  // phase 1 is a design phase (#127): its blocks are the Diagrams and the Texts (its link cards, when it has any, come after)
  const m = await ev(`(() => { const box = (e) => e.getBoundingClientRect(); const panel = document.getElementById('wfpanel');
    const blocks = Array.from(document.querySelector('.wfp-art').children).filter((e) => e.matches('.wfp-dgs, .wfp-texts, .wfp-cards')).map(box); const idea = box(document.querySelector('.wfp-idea'));
    const stacked = blocks.every((r, i, all) => i === 0 || r.top >= all[i - 1].bottom - 1);
    return { page: document.documentElement.scrollWidth <= innerWidth, panel: panel.scrollWidth <= panel.clientWidth, n: blocks.length,
      verdict: idea.right <= box(panel).right && idea.left >= box(panel).left, column: blocks.every((r) => Math.abs(r.left - blocks[0].left) <= 1 && r.right <= box(panel).right), stacked}; })()`);
  expect(m.n >= 2 && m.page && m.panel && m.column && m.stacked && m.verdict, `layout at 340 px: ${JSON.stringify(m)}`);
  // phase 4 keeps its cards (the plain stage): the cards block exists and stacks under the sections, in the column
  await step(4);
  await waitFor(`${count(".wfp-cards .wfp-card")} >= 1`, "the cards of phase 4");
  const c = await ev(`(() => { const box = (e) => e.getBoundingClientRect(); const panel = box(document.getElementById('wfpanel'));
    const blocks = Array.from(document.querySelector('.wfp-art').children).filter((e) => e.matches('.wfp-sec, .wfp-cards')).map(box);
    const cards = box(document.querySelector('.wfp-cards'));
    return { cards: blocks.length >= 1, stacked: blocks.every((r, i, all) => i === 0 || r.top >= all[i - 1].bottom - 1),
      column: blocks.every((r) => Math.abs(r.left - cards.left) <= 1 && r.right <= panel.right), page: document.documentElement.scrollWidth <= innerWidth }; })()`);
  expect(c.cards && c.stacked && c.column && c.page, `cards at 340 px: ${JSON.stringify(c)}`);
});

await run("S57", "the bodies of the folds a phase opens by itself are fetched one at a time", async () => {
  await loadPage({});
  await open();
  await stepAndSettle(1);
  // the owner opens three section folds of phase 1, one click after the other; then leaves the phase and comes back:
  // the stage draws again, and the three open bodies are asked one after the other (#100: the window does not get heavier)
  const folds = await ev(`document.querySelectorAll('details.wfp-fold.section').length`);
  expect(folds >= 3, `phase 1 has ${folds} section folds, the premise needs 3`);
  for (let i = 0; i < 3; i++) {
    await ev(`document.querySelectorAll('details.wfp-fold.section > summary')[${i}].click()`);
    await waitFor(`document.querySelectorAll('details.wfp-fold.section')[${i}].open && document.querySelectorAll('details.wfp-fold.section .wfp-fold-body')[${i}].children.length > 0 && !document.querySelector('.wfp-fold-body .dim')`, `fold ${i} drawn`);
  }
  await step(0);
  await waitFor(`${text(".wfp-title")}.startsWith("0 ")`, "the stage of phase 0");
  await ev(`(() => { const real = window.fetch.bind(window); window.__bodies = { now: 0, max: 0, total: 0 };
    window.fetch = (input, init) => { const url = String(typeof input === 'string' ? input : input.url);
      if (!/\\/api\\/documents\\/[^/?]+/.test(url)) return real(input, init);
      const c = window.__bodies; c.now++; c.total++; c.max = Math.max(c.max, c.now);
      return real(input, init).finally(() => { c.now--; }); }; })()`);
  await step(1);
  await waitFor(`${count("details.wfp-fold.section[open]")} === 3 && !document.querySelector('.wfp-fold-body .dim')`, "the three open folds drawn again");
  const c = await ev("window.__bodies");
  expect(c.total >= 3, `the redraw fetched ${c.total} bodies, the premise needs its 3`);
  expect(c.max === 1, `${c.max} bodies in flight at once (${JSON.stringify(c)})`);
});

// --- #113 (row-view): selecting a row opens the whole-window view (S58-S66) ---------------------------------

const BIG = { width: 1894, height: 900 };
async function sized({ width, height = 900, mobile = false }) {
  await loadPage({});
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
  await sleep(200);
}
const rowIds = () => ev(`Array.from(document.querySelectorAll('tr[data-id]')).map((t) => t.dataset.id)`);
const viewOpen = () => ev(`document.getElementById('body').classList.contains('has-panel')`);
const hashNow = () => ev("location.hash");
const slugOpen = () => ev(text(".wfp-slug"));
const rowSlug = async (id) => {
  const all = await (await fetch(base + "/api/features")).json();
  return all.open.concat(all.history).find((r) => r.id === id).slug;
};
const inLayout = (sel) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })()`);
async function historyIds(min) {
  await ev("chooseTab('history')");
  await waitFor(`${count("tr[data-id]")} >= ${min}`, `${min} history rows`);
  return rowIds();
}

await run("S58", "selecting a row hides the table and the view fills the window: at 1894 px the stage is wider than 1000 px, nothing is clipped, no x button", async () => {
  await sized(BIG);
  await open();
  expect(await viewOpen(), "the view is not open");
  expect(!(await inLayout("#view table")) && !(await inLayout("#view")), "the table is still in the layout");
  const m = await ev(`(() => { const box = (s) => document.querySelector(s).getBoundingClientRect(); const v = box('#wfpanel'), st = box('.wfp-stage'), main = box('main');
    const clipped = Array.from(document.querySelectorAll('#wfpanel *')).filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > v.right + 1 || r.left < v.left - 1); }).length;
    return { stage: st.width, view: v.width, main: main.width, win: innerWidth, clipped, page: document.documentElement.scrollWidth <= innerWidth,
      back: getComputedStyle(document.querySelector('.wfp-back-pill')).display, close: document.querySelectorAll('.wfp-close').length }; })()`);
  expect(m.stage > 1000, `the stage is ${m.stage} px wide`);
  expect(m.main >= m.win - 100, `the window is ${m.main} px of ${m.win}`);
  expect(m.clipped === 0 && m.page, `clipped ${m.clipped}, page fits ${m.page}`);
  expect(m.back !== "none" && m.close === 0, `back pill ${m.back}, x buttons ${m.close}`);
});

await run("S59", "the Back pill and Esc return to the table at the same scroll, the row marked and focused", async () => {
  await sized({ width: 1280, height: 400 });
  const ids = await historyIds(8);
  for (const how of ["pill", "esc"]) {
    await ev("window.scrollTo(0, 150)");
    const y = await ev("window.scrollY");
    expect(y > 100, `the table does not scroll (${y})`);
    const pick = ids[3];
    await clickRow(pick);
    await waitFor("document.querySelector('.wfp-title')", "the view");
    if (how === "pill") await ev("document.querySelector('.wfp-back-pill').click()"); else await press("Escape");
    await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the table back");
    await sleep(150);
    const back = await ev(`({ y: window.scrollY, sel: document.querySelector('tr[data-id="${pick}"]').classList.contains('sel'), focus: document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.id : null })`);
    expect(Math.abs(back.y - y) <= 2, `${how}: scroll ${y} became ${back.y}`);
    expect(back.sel && back.focus === pick, `${how}: marked ${back.sel}, focused ${back.focus}`);
  }
  // owner, 2026-11-19: the last row read stays marked after Back and is brought into view when it is off screen
  await ev("window.scrollTo(0, 0)");
  await clickRow(ids[0]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  for (let i = 0; i < 6; i++) await press("j");
  await waitFor(`location.hash === '#row=${ids[6]}'`, "the seventh row");
  await ev("document.querySelector('.wfp-back-pill').click()");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the table back");
  await sleep(150);
  const far = await ev(`(() => { const tr = document.querySelector('tr[data-id="${ids[6]}"]'); const r = tr.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, h: innerHeight, sel: tr.classList.contains('sel') }; })()`);
  expect(far.sel, "the last row read is not marked after Back");
  expect(far.bottom > 0 && far.top < far.h, `the last row read is off screen after Back (${far.top}..${far.bottom} of ${far.h})`);
});

await run("S60", "the address is #row=<id> while a row is open and empty after Back; a page loaded with it opens that row, in History for a delivered one", async () => {
  await loadPage({});
  await open();
  expect((await hashNow()) === `#row=${mine.id}`, `the address is ${await hashNow()}`);
  await ev("document.querySelector('.wfp-back-pill').click()");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the table");
  await waitFor(`location.hash === ''`, "an empty address");
  await send("Page.navigate", { url: "about:blank" });
  await send("Page.navigate", { url: `${base}/#row=${mine.id}` });
  await waitFor("document.querySelector('.wfp-title')", "the row opened by the address");
  expect((await slugOpen()) === TARGET, `opened ${await slugOpen()}`);
  const delivered = features.history[0];
  expect(delivered, "the fixture has no delivered row");
  await send("Page.navigate", { url: "about:blank" });
  await send("Page.navigate", { url: `${base}/#row=${delivered.id}` });
  await waitFor("document.querySelector('.wfp-title')", "the delivered row opened by the address");
  expect((await slugOpen()) === delivered.slug, `opened ${await slugOpen()}`);
  await ev("document.querySelector('.wfp-back-pill').click()");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the table");
  expect((await ev("state.tab")) === "history", `the table behind is ${await ev("state.tab")}`);
});

await run("S61", "the buttons and j/k open the neighbour row and replace the address; at the last row next is disabled and j does nothing", async () => {
  await loadPage({});
  const ids = await rowIds();
  expect(ids.length >= 2, "the fixture needs two open rows");
  await clickRow(ids[0]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  const length = await ev("navigation.entries().length");
  expect(await ev(`document.querySelector('.wfp-row-prev').disabled`), "previous is not greyed at the first row");
  await press("k");
  await sleep(150);
  expect((await hashNow()) === `#row=${ids[0]}`, "k moved before the first row");
  await press("j");
  await waitFor(`location.hash === '#row=${ids[1]}'`, "j to the second row");
  expect((await slugOpen()) === (await rowSlug(ids[1])), `the view shows ${await slugOpen()}`);
  await ev("document.querySelector('.wfp-row-prev').click()");
  await waitFor(`location.hash === '#row=${ids[0]}'`, "the previous button");
  await ev("document.querySelector('.wfp-row-next').click()");
  await waitFor(`location.hash === '#row=${ids[1]}'`, "the next button");
  for (let i = ids.length; i > 2; i--) await press("j");
  await waitFor(`location.hash === '#row=${ids[ids.length - 1]}'`, "the last row");
  expect(await ev(`document.querySelector('.wfp-row-next').disabled`), "next is not greyed at the last row");
  await press("j");
  await sleep(150);
  expect((await hashNow()) === `#row=${ids[ids.length - 1]}`, "j moved past the last row");
  expect((await ev("navigation.entries().length")) === length, `history grew from ${length} to ${await ev("navigation.entries().length")}`);
  // focus stays on the pressed button on a move (a keyboard user can press it again); on a button that
  // became disabled it goes to the other one
  const focusIs = (cls) => ev(`document.activeElement === document.querySelector('.${cls}')`);
  await ev("document.querySelector('.wfp-row-prev').focus(); document.querySelector('.wfp-row-prev').click()");
  await waitFor(`location.hash === '#row=${ids[ids.length - 2]}'`, "the previous button from the last row");
  expect(await focusIs("wfp-row-prev"), "focus left the previous button after it moved the row");
  await ev("document.querySelector('.wfp-row-next').focus(); document.querySelector('.wfp-row-next').click()");
  await waitFor(`location.hash === '#row=${ids[ids.length - 1]}'`, "the next button to the last row");
  expect(await ev(`document.querySelector('.wfp-row-next').disabled`), "next is not disabled at the last row");
  expect(await focusIs("wfp-row-prev"), "focus was not moved to the previous button when next became disabled");
});

await run("S62", "after opening a row and moving through three rows one browser back returns to the table", async () => {
  await sized({ width: 1280, height: 600 });
  const ids = await historyIds(5);
  const length = await ev("navigation.entries().length");
  await clickRow(ids[0]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  for (let i = 0; i < 3; i++) await press("j");
  await waitFor(`location.hash === '#row=${ids[3]}'`, "the fourth row");
  expect((await ev("navigation.entries().length")) === length + 1, `${await ev("navigation.entries().length")} entries, ${length + 1} expected`);
  await ev("history.back()");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the table after one back");
  expect((await hashNow()) === "", `the address is ${await hashNow()}`);
});

await run("S63", "with the History filtered, the buttons and j/k walk only the rows the filter shows", async () => {
  await loadPage({});
  const all = await historyIds(4);
  const slugs = await ev(`Array.from(document.querySelectorAll('tr[data-id]')).map((t) => t.querySelector('.slug') ? t.querySelector('.slug').textContent : t.textContent)`);
  let found = null;
  for (const slug of slugs) for (let n = 2; n <= Math.min(5, slug.length) && !found; n++) {
    const candidate = slug.slice(0, n).toLowerCase();
    await ev(`(() => { const f = document.getElementById('filter'); f.value = ${JSON.stringify(candidate)}; f.dispatchEvent(new Event('input')); })()`);
    const shown = await rowIds();
    if (shown.length >= 3 && shown.length < all.length && shown[1] !== all[1] && shown[0] === all[0]) found = { candidate, shown };
  }
  expect(found, "no filter leaves 3 rows with a different second row");
  await ev(`(() => { const f = document.getElementById('filter'); f.value = ${JSON.stringify(found.candidate)}; f.dispatchEvent(new Event('input')); })()`);
  await clickRow(found.shown[0]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await press("j");
  await waitFor(`location.hash === '#row=${found.shown[1]}'`, "j to the next filtered row");
  await press("j");
  await waitFor(`location.hash === '#row=${found.shown[2]}'`, "j to the third filtered row");
  await press("k");
  await waitFor(`location.hash === '#row=${found.shown[1]}'`, "k back");
});

await run("S64", "a refresh that removes the open row closes the view with the notice and clears the address; one that keeps it leaves phase and diagrams alone", async () => {
  await loadPage({});
  await open();
  await stepAndSettle(1);
  await waitFor(`${count(".wfp-dg.drawn")} > 0`, "a drawn diagram");   // phase 1 draws its diagrams in the Diagrams block (#127)
  await ev("document.querySelector('.wfp-dg .diagram svg').dataset.mark = 'kept'");
  const at = await currentStep();
  await ev("refresh(true)");
  await sleep(600);
  expect(await viewOpen(), "a refresh that keeps the row closed the view");
  expect((await currentStep()) === at, "the refresh moved the phase");
  expect((await ev(`document.querySelector('.wfp-dg .diagram svg').dataset.mark`)) === "kept", "the diagram was redrawn");
  expect((await hashNow()) === `#row=${mine.id}`, "the address changed");
  const length = await ev("navigation.entries().length");
  stub.features = (real) => ({ ...real, open: real.open.filter((r) => r.id !== mine.id) });
  await ev("refresh(true)");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the view closed");
  expect((await ev(text("#notice"))).includes(TARGET), `the notice reads ${await ev(text("#notice"))}`);
  expect((await hashNow()) === "", `the address is ${await hashNow()}`);
  expect((await ev("navigation.entries().length")) === length, "closing added a history entry");
});

await run("S65", "at 700 px the view is the whole window with the phase column at the side; at 390 px it is the phone layout", async () => {
  await sized({ width: 700, height: 900, mobile: true });
  await open();
  const wide = await ev(`(() => { const b = (s) => document.querySelector(s).getBoundingClientRect(); const n = b('.wfp-nav'), st = b('.wfp-stage'), p = b('#wfpanel');
    return { side: n.right <= st.left + 1 && Math.abs(n.width - 158) <= 2, hidden: document.querySelector('#view').getBoundingClientRect().height === 0, full: p.width >= innerWidth - 70, fits: document.documentElement.scrollWidth <= innerWidth }; })()`);
  expect(wide.side && wide.hidden && wide.full && wide.fits, `700 px: ${JSON.stringify(wide)}`);
  await sized({ width: 390, height: 844, mobile: true });
  await open();
  const phone = await ev(`(() => { const b = (s) => document.querySelector(s).getBoundingClientRect(); const n = b('.wfp-nav'), st = b('.wfp-stage');
    return { row: n.bottom <= st.top + 1 && n.height < 100, hidden: document.querySelector('#view').getBoundingClientRect().height === 0, fits: document.documentElement.scrollWidth <= innerWidth,
      back: getComputedStyle(document.querySelector('.wfp-back-pill')).display !== 'none' }; })()`);
  expect(phone.row && phone.hidden && phone.fits && phone.back, `390 px: ${JSON.stringify(phone)}`);
});

await run("S66", "regression pin (green before row-view): a click on a tab with the view open closes it and shows that tab, the address cleared", async () => {
  await loadPage({});
  await open();
  await ev("document.getElementById('tab-history').click()");
  await waitFor("!document.getElementById('body').classList.contains('has-panel')", "the view closed");
  expect((await ev("state.tab")) === "history", `the tab is ${await ev("state.tab")}`);
  expect(await ev(`document.getElementById('tab-history').classList.contains('on')`), "History is not marked");
  expect((await hashNow()) === "", `the address is ${await hashNow()}`);
  expect((await inLayout("#view table")), "the History table is not shown");
});

// --- S67-S73: the cases the mutation matrix of #113 found blind (each sets up its own page) ---------------------

const hasPanelGone = "!document.getElementById('body').classList.contains('has-panel')";
const leave = async (how) => {
  if (how === "pill") await ev("document.querySelector('.wfp-back-pill').click()"); else await press("Escape");
  await waitFor(hasPanelGone, "the table back");
  await sleep(150);
};

await run("S67", "Back restores the table's scroll even after moves through other rows (a move resets the view to its top)", async () => {
  await sized({ width: 1280, height: 400 });
  await historyIds(8);
  await ev("window.scrollTo(0, 150)");
  expect((await ev("window.scrollY")) > 100, `the table does not scroll (${await ev("window.scrollY")})`);
  // three consecutive rows fully on screen at this scroll: the row read last stays in view, so Back has no reason to scroll to it
  const seen = await ev(`Array.from(document.querySelectorAll('tr[data-id]')).filter((t) => { const r = t.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }).map((t) => t.dataset.id)`);
  expect(seen.length >= 3, `only ${seen.length} rows are fully visible at scroll 150`);
  const [first, , third] = seen;
  await clickRow(first);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await press("j");
  await press("j");
  await waitFor(`location.hash === '#row=${third}'`, "the second move");
  expect((await ev("window.scrollY")) === 0, `a move left the view at ${await ev("window.scrollY")}, not at its top`);
  await leave("pill");
  const y = await ev("window.scrollY");
  expect(Math.abs(y - 150) <= 2, `Back after two moves left the table at ${y}, not at 150`);
});

await run("S68", "Back restores the table's scroll when the view is shorter than the saved scroll", async () => {
  await sized({ width: 1280, height: 400 });
  const ids = await historyIds(8);
  await ev("window.scrollTo(0, 150)");
  expect((await ev("window.scrollY")) > 100, `the table does not scroll (${await ev("window.scrollY")})`);
  await clickRow(ids[3]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await ev(`(() => { const s = document.createElement('style'); s.id = 'short-stage'; s.textContent = '.wfp-stage, .wfp-nav { display: none !important; }'; document.head.append(s); })()`);
  await sleep(150);
  const m = await ev("({ h: document.documentElement.scrollHeight, w: innerHeight })");
  expect(m.h <= m.w + 5, `the stubbed view is ${m.h} px tall in a ${m.w} px window: not shorter than the saved scroll`);
  await leave("pill");
  await ev("document.getElementById('short-stage') && document.getElementById('short-stage').remove()");
  const y = await ev("window.scrollY");
  expect(Math.abs(y - 150) <= 2, `Back from a short view left the table at ${y}, not at 150`);
});

await run("S69", "Back and Esc go back in the history (the entry the click pushed is undone), they do not replace it", async () => {
  for (const how of ["pill", "esc"]) {
    await loadPage({});
    await ev("window.__doc = 1; for (let i = 0; i < 2; i++) history.pushState(null, '', location.pathname + location.search)");   // two entries of this document behind the click's: the further back stays in the page
    // an entry's id, not its index: a browser caps its list (50 entries), so past the cap the index no longer grows
    const before = await ev("navigation.currentEntry.id");
    await clickRow(mine.id);
    await waitFor("document.querySelector('.wfp-title')", "the view");
    expect((await ev("navigation.currentEntry.id")) !== before, `${how}: the click did not push an entry`);
    await leave(how);
    const after = await ev("navigation.currentEntry.id");
    expect(after === before, `${how}: leaving did not return to the entry the click started from`);
    // the entry the click pushed is still ahead (a replace would have overwritten it with the table's address)
    const ahead = await ev("navigation.canGoForward ? navigation.entries()[navigation.currentEntry.index + 1].url : null");
    expect(ahead && ahead.endsWith(`#row=${mine.id}`), `${how}: the entry ahead is ${ahead}, not the row's`);
    await ev("history.back()");   // a back the browser may skip (no user activation on the entries pushed above) or send out of the page
    await sleep(500);
    if ((await ev("window.__doc")) === 1) {
      expect(await ev(hasPanelGone), `${how}: a further history.back() reopened the row`);
      expect((await hashNow()) === "", `${how}: the address after a further back is ${await hashNow()}`);
    }
  }
});

await run("S70", "a move brings the new row's view to its top, whatever the scroll of the view that was open", async () => {
  await sized({ width: 1280, height: 400 });
  const ids = await historyIds(8);
  await clickRow(ids[0]);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await waitFor(`${count(".wfp-sec, .wfp-card")} > 0`, "the stage's content");
  await ev("window.scrollTo(0, 300)");
  const y = await ev("window.scrollY");
  expect(y > 200, `the view does not scroll in a 400 px window (${y})`);
  await press("j");
  await waitFor(`location.hash === '#row=${ids[1]}'`, "j to the second row");
  await sleep(150);
  expect((await ev("window.scrollY")) === 0, `the new row's view is at ${await ev("window.scrollY")}, not at its top`);
});

await run("S71", "a click on a tab clears the mark of the row that was read", async () => {
  await loadPage({});
  await open();
  await ev("document.querySelector('.wfp-back-pill').click()");
  await waitFor(hasPanelGone, "the table back");
  expect((await ev(count("tr.sel"))) === 1, "the premise: the row read is not marked after Back");
  await tab("history");
  await waitFor(`${count("tr[data-id]")} >= 1`, "the History rows");
  await tab("open");
  await waitFor(`document.querySelector('tr[data-id="${mine.id}"]')`, "the Open rows");
  expect((await ev(count("tr.sel"))) === 0, `${await ev(count("tr.sel"))} row(s) still marked after History then Open`);
});

const MISSING = "000000000000";
async function loadMissingRow() {
  await loadPage({});
  await send("Page.navigate", { url: "about:blank" });
  await send("Page.navigate", { url: `${base}/#row=${MISSING}` });
  await waitFor(`document.querySelector('tr[data-id]') && ${text("#notice")}.includes(${JSON.stringify(MISSING)})`, "the notice naming the missing row");
}

await run("S72", "an address naming a row that is not there shows the table and a notice naming the id", async () => {
  await loadMissingRow();
  expect(await ev(hasPanelGone), "the view is open for a row that does not exist");
  expect(await ev(`(() => { const n = document.getElementById('notice'); return !n.hidden && n.getClientRects().length > 0; })()`), "the notice is not visible");
  expect((await ev(text("#notice"))).includes(MISSING), `the notice reads ${await ev(text("#notice"))}`);
  expect(await inLayout("#view table"), "the table is not shown");
});

await run("S73", "an address naming a row that is not there is cleared after the load", async () => {
  await loadMissingRow();
  expect((await hashNow()) === "", `the address is ${await hashNow()}`);
});

// S74-S75: the table's scroll is restored when the view closes WITHOUT history.back(): a row that vanishes at a refresh
// (Decision 4: the notice and the table as it was). A tab click is not tested: restoring there is not a stated behaviour.
async function vanishAtRefresh(id) {
  stub.features = (real) => ({ ...real, open: real.open.filter((r) => r.id !== id), history: real.history.filter((r) => r.id !== id) });
  await ev("refresh(true)");
  await waitFor(hasPanelGone, "the view closed");
  await waitFor(`${text("#notice")}.length > 0`, "the notice");
  await sleep(150);
}
// The notice is a banner above the table: when it appears the browser's scroll anchoring moves scrollY by its height to keep
// the table's content where it was (150 + 36 = 186), so the check is where a row above the vanished one sits in the window.
const topOf = (id) => ev(`document.querySelector('tr[data-id="${id}"]').getBoundingClientRect().top`);
const visibleRows = () => ev(`Array.from(document.querySelectorAll('tr[data-id]')).filter((t) => { const r = t.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }).map((t) => t.dataset.id)`);

await run("S74", "when the open row vanishes at a refresh the table comes back at the scroll it had when the row was clicked", async () => {
  await sized({ width: 1280, height: 400 });
  await historyIds(8);
  await ev("window.scrollTo(0, 150)");
  expect((await ev("window.scrollY")) > 100, `the table does not scroll (${await ev("window.scrollY")})`);
  const seen = await visibleRows();
  expect(seen.length >= 2, "fewer than two rows are fully visible at scroll 150");
  const [above, clicked] = [seen[0], seen[seen.length - 1]];
  const before = await topOf(above);
  await clickRow(clicked);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await vanishAtRefresh(clicked);
  const after = await topOf(above);
  expect(Math.abs(after - before) <= 2, `a row above the vanished one is at ${after} px in the window, was at ${before} (scrollY ${await ev("window.scrollY")})`);
});

await run("S75", "when a row reached by moves vanishes at a refresh the table comes back at the scroll of the click, not the view's", async () => {
  await sized({ width: 1280, height: 400 });
  await historyIds(8);
  await ev("window.scrollTo(0, 150)");
  expect((await ev("window.scrollY")) > 100, `the table does not scroll (${await ev("window.scrollY")})`);
  const seen = await visibleRows();
  expect(seen.length >= 3, `only ${seen.length} rows are fully visible at scroll 150`);
  const [first, , third] = seen;
  const before = await topOf(first);
  await clickRow(first);
  await waitFor("document.querySelector('.wfp-title')", "the view");
  await press("j");
  await press("j");
  await waitFor(`location.hash === '#row=${third}'`, "the second move");
  await vanishAtRefresh(third);
  // the vanished row is gone, so the rule that brings the last read row into view has no row to act on: the table is where the click left it
  const after = await topOf(first);
  expect(Math.abs(after - before) <= 2, `the first row is at ${after} px in the window, was at ${before} (scrollY ${await ev("window.scrollY")})`);
});

// S76 (#111): a row whose phase is checked again stays in History with its last known phase and the word "refreshing".
const refreshingCell = (id) => ev(`(() => { const td = document.querySelector('tr[data-id="${id}"] td.phase'); const m = td && td.querySelector('.refreshing');
  return { text: td ? td.textContent : null, mark: !!m, anim: m ? getComputedStyle(m, '::before').animationName : null }; })()`);
await run("S76", "a refreshing row stays in History with its phase and the word; the word goes when the check ends; reduced motion stops the pulse and keeps the word", async () => {
  await loadPage({});
  const data = await ev("JSON.parse(JSON.stringify(state.data))");
  expect(data.history.length >= 2, `needs 2 delivered rows in History, there are ${data.history.length}`);
  const [target, bystander] = data.history.map((r) => r.id);
  const answer = (on) => () => ({ ...data, history: data.history.map((r) => (r.id === target ? { ...r, phase: { ...r.phase, kind: "delivered", refreshing: on } } : r)) });
  await tab("history");
  await waitFor("document.querySelector('#view tbody tr')", "the History table");
  await ev(`document.querySelectorAll('#view tbody tr').forEach((tr) => { tr.__m = 1; })`);
  const index = await ev(`Array.from(document.querySelectorAll('#view tbody tr')).findIndex((tr) => tr.dataset.id === '${target}')`);
  stub.features = answer(true);
  await ev("refresh()");
  await waitFor(`document.querySelector('tr[data-id="${target}"] .refreshing')`, "the refreshing word");
  const on = await refreshingCell(target);
  expect(on.text.startsWith("delivered") && on.text.includes("refreshing"), `the cell reads ${JSON.stringify(on.text)}`);
  expect(on.anim !== "none", `the mark does not pulse (${on.anim})`);
  stub.features = answer(false);
  await ev("refresh()");
  await waitFor(`!document.querySelector('tr[data-id="${target}"] .refreshing')`, "the word to go");
  const kept = await ev(`({ at: Array.from(document.querySelectorAll('#view tbody tr')).findIndex((tr) => tr.dataset.id === '${target}'),
    bystander: document.querySelector('tr[data-id="${bystander}"]').__m === 1 })`);
  expect(kept.at === index, `the row moved from ${index} to ${kept.at}`);
  expect(kept.bystander, "an unchanged row was redrawn");
  // reduced motion: the word stays, the pulse does not move
  await loadPage({ motion: "reduce" });
  stub.features = answer(true);
  await tab("history");
  await ev("refresh()");
  await waitFor(`document.querySelector('tr[data-id="${target}"] .refreshing')`, "the refreshing word under reduced motion");
  const still = await refreshingCell(target);
  expect(still.mark && still.anim === "none", `under reduced motion the animation is ${still.anim}`);
});

// --- S77-S79, S84 (#127; S77 also reads the Invariants block of #129, S81 below): the stage of a design phase draws its diagrams, then folds its texts -----------------
// Built on a STUBBED /api/workflow answer (phase 3 of the target row), so they do not depend on what the target
// feature's own spec holds. The folds' bodies are real requests for stub ids: a fold's body says it cannot be
// opened, which is enough: these checks are about the stage's structure and the fold's state, not the text.
const STUB_MERMAID = ["flowchart LR\n  A[scan] --> B[fresh?]\n  B --> C[cached]", "sequenceDiagram\n  Page->>server: GET",
  "stateDiagram-v2\n  [*] --> Closed\n  Closed --> Open: click"];
const stubDesign = (diagrams = STUB_MERMAID, tweak = (p) => p) => (real) => ({ ...real, phases: real.phases.map((p) => (p.n !== 3 ? p : tweak({
  ...p,
  invariants: null,   // never the real server's table for the target's own spec (S81 sets its own through `withInvariants`)
  diagrams: diagrams.map((mermaid, i) => ({ title: ["Model › Flow", "Model › Sequence", "Model › State machines › Fold"][i] || `Model › ${i}`,
    source: "docs/specs/stub.md § Model", doc: null, generated: null, mermaid })),
  sections: [{ heading: "System Architecture", source: "docs/specs/stub.md", state: "present", id: "stub-sec-a", rev: "r1", note: null, lines: 64 },
    { heading: "Model", source: "docs/specs/stub.md", state: "present", id: "stub-sec-b", rev: "r1", note: null, lines: 92 }],
  artefacts: [{ id: "stub-spec", kind: "spec", name: "docs/specs/stub.md", path: "/x/stub.md", lines: 212 }] }))) });
// both open rows at phase 3 (S78 opens the panel on the other one too)
const bothAtPhase3 = (real) => ({ ...real, open: real.open.map((r) => (r.id === mine.id || r.id === other.id
  ? { ...r, agent: null, phase: { kind: "derived", phase: 3, verdicts: verdictsOf(passedUpTo(3)) } } : r)) });
async function openStubDesign(options = {}) {
  await loadPage(options);
  stub.features = bothAtPhase3;
  stub.workflow = stubDesign();
  await ev("refresh()");
  await open();
  await step(3);
  await waitFor(`${count(".wfp-dg.drawn")} === 3`, "the 3 stubbed diagrams drawn");
}
const foldState = () => ev(`Array.from(document.querySelectorAll('details.wfp-fold')).map((d) => d.open)`);
const foldClick = (i) => ev(`document.querySelectorAll('details.wfp-fold > summary')[${i}].click()`);

await run("S77", "phase 3 draws the Invariants block, then the Diagrams block, then the folded texts (sections, then documents, closed, with their lengths), and a refresh keeps a drawn diagram's node", async () => {
  await openStubDesign();
  const order = await ev(`Array.from(document.querySelector('.wfp-art').children).map((c) => c.className.split(' ')[0])`);
  expect(JSON.stringify(order.slice(0, 3)) === JSON.stringify(["wfp-invs", "wfp-dgs", "wfp-texts"]), `the stage reads ${JSON.stringify(order)}, expected Invariants, Diagrams, Texts`);
  expect((await ev(text(".wfp-invs .wfp-nothing"))) === "The spec has no § System invariants.", "the stub has no table: the Invariants block should say so in one line");
  const captions = await ev(`Array.from(document.querySelectorAll('.wfp-dg-cap')).map((c) => c.firstChild.textContent + ' | ' + c.lastChild.textContent)`);
  expect(captions[0] === "Model › Flow | docs/specs/stub.md § Model" && captions.length === 3, `the captions read ${JSON.stringify(captions)}`);
  const folds = await ev(`Array.from(document.querySelectorAll('details.wfp-fold')).map((d) => [d.querySelector('.wfp-fold-t').textContent, d.querySelector('.wfp-fold-len').textContent, d.open])`);
  expect(JSON.stringify(folds) === JSON.stringify([["System Architecture", "64 lines", false], ["Model", "92 lines", false], ["stub.md", "212 lines", false]]),
    `the folds read ${JSON.stringify(folds)}`);
  expect((await ev(count(".wfp-dg .diagram-error"))) === 0, "a stubbed diagram failed");
  await ev(`document.querySelector('.wfp-dg .diagram').dataset.marker = 'kept'`);
  await ev("refresh(true)");
  await sleep(700);
  expect((await ev(count('.wfp-dg .diagram[data-marker="kept"]'))) === 1, "a refresh redrew a diagram whose source had not changed");
});

await run("S78", "a fold opens on a click, stays open across a refresh, and is closed again when the panel is opened on another row", async () => {
  await openStubDesign();
  expect((await foldState()).every((x) => !x), "a fold starts open");
  await foldClick(1);
  await waitFor(`document.querySelectorAll('details.wfp-fold')[1].open`, "the second fold open");
  // ticket 130: where the browser interpolates `auto`, a fold's height transitions over MOTION_MS.fold (220 ms) with the page's easing
  const heightMotion = await ev(`document.documentElement.dataset.motionFold === 'height' ? getComputedStyle(document.querySelectorAll('details.wfp-fold')[1], '::details-content').transitionProperty + ' ' + getComputedStyle(document.querySelectorAll('details.wfp-fold')[1], '::details-content').transitionDuration : 'unsupported'`);
  // "unsupported" FAILS: premise P4 says this Edge has interpolate-size, so a green run means the transition was seen
  expect(/^height, content-visibility 0\.22s, 0\.22s$/.test(heightMotion), `the fold's content transition reads "${heightMotion}" (unsupported = this Edge lacks interpolate-size, which premise P4 says it has)`);
  await ev(`document.querySelectorAll('details.wfp-fold')[1].dataset.marker = 'kept'`);
  await ev("refresh(true)");
  await sleep(700);
  expect(JSON.stringify(await foldState()) === JSON.stringify([false, true, false]), `after a refresh the folds read ${JSON.stringify(await foldState())}`);
  expect((await ev(count('details.wfp-fold[data-marker="kept"]'))) === 1, "a refresh redrew the stage and took the fold's node with it");
  await foldClick(1);
  await waitFor(`!document.querySelectorAll('details.wfp-fold')[1].open`, "the fold closed by a second click");
  await foldClick(2);
  await waitFor(`document.querySelectorAll('details.wfp-fold')[2].open`, "the third fold open");
  await ev("closePanel()");
  await open(other.id);
  await step(3);
  await waitFor(`${count("details.wfp-fold")} === 3`, "the folds of the other row");
  await ev("closePanel()");
  await open();
  await step(3);
  await waitFor(`${count("details.wfp-fold")} === 3`, "the folds of the first row again");
  expect((await foldState()).every((x) => !x), `a fold stayed open after another row: ${JSON.stringify(await foldState())}`);
});

await run("S79", "a drawn diagram opens full screen on a click and closes on Esc and on a click outside; Esc with a diagram open leaves the panel in place", async () => {
  await openStubDesign();
  const open1 = () => ev(`document.querySelector('.wfp-dg.drawn .wfp-dg-open').click()`);
  await open1();
  await waitFor(`${count(".wfp-full")} === 1 && ${count(".wfp-full .diagram > svg")} === 1`, "the diagram full screen");
  // the zoom grows the sheet from the card's rectangle: measure the end state, once the dialog runs no animation (ticket 130)
  // only the sheet's own animation (the zoom): a Mermaid drawing may carry an infinite CSS animation of its own
  await waitFor(`document.querySelector('.wfp-full-sheet').getAnimations().length === 0`, "the zoom to end");
  const size = await ev(`(() => { const r = document.querySelector('.wfp-full .diagram').getBoundingClientRect(); const c = document.querySelector('.wfp-dg .diagram').getBoundingClientRect(); return { full: r.width, card: c.width }; })()`);
  expect(size.full > size.card, `the full-screen drawing is ${size.full} px wide, the card's ${size.card}`);
  await press("Escape");
  await waitFor(`${count(".wfp-full")} === 0`, "Esc to close it");
  expect((await ev(count(".wfp-title"))) === 1, "Esc with a diagram open closed the panel too");
  await open1();
  await waitFor(`${count(".wfp-full")} === 1`, "the diagram full screen again");
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: 2, y: 2, button: "left", buttons: 1, clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: 2, y: 2, button: "left", buttons: 0, clickCount: 1 });
  await waitFor(`${count(".wfp-full")} === 0`, "a click outside to close it");
  await open1();
  await waitFor(`${count(".wfp-full")} === 1`, "the diagram full screen a third time");
  await ev(`document.querySelector('.wfp-full-sheet figcaption').click()`);
  await sleep(200);
  expect((await ev(count(".wfp-full"))) === 1, "a click inside the sheet closed it");
  await press("Escape");
  await waitFor(`${count(".wfp-full")} === 0`, "Esc to close it again");
});

// --- S80 (#128): phase 5 draws the two diagrams generated from the design's json block ------------------------
// The sources below are what catalog.data_flow_mermaid / call_stack_mermaid write for a design of an add, a mod and a keep node.
const GEN_FLOW = ['flowchart LR', '  n0["a.new"]', '  n1["b.changed"]', '  n2["c.same"]', '  n0 --> n1', '  n1 --> n2',
  '  class n0 add', '  class n1 mod', '  class n2 keep', '  classDef add stroke-width:1.5px', '  classDef mod stroke-width:1.5px',
  '  classDef keep stroke-width:1.5px'].join("\n");
const GEN_STACK = ['flowchart TD', '  n0["entry"]', '  n1["a.new"]', '  n2["b.changed"]', '  n0 --> n1', '  n1 --> n2',
  '  class n0 keep', '  class n1 add', '  class n2 mod', '  classDef keep stroke-width:1.5px', '  classDef add stroke-width:1.5px',
  '  classDef mod stroke-width:1.5px'].join("\n");
const stubFive = (extra = []) => (real) => ({ ...real, phases: real.phases.map((p) => (p.n !== 5 ? p : { ...p,
  diagrams: [
    { title: "Data flow (generated)", source: "docs/design/stub.md § json design", doc: null, generated: "data_flow", mermaid: GEN_FLOW },
    { title: "Call stack (generated)", source: "docs/design/stub.md § json design", doc: null, generated: "call_stack", mermaid: GEN_STACK },
    ...extra],
  sections: [], artefacts: [{ id: "stub-design", kind: "design", name: "docs/design/stub.md", path: "/x/stub.md", lines: 120 }] })) });

await run("S80", "phase 5 draws the generated Data flow and Call stack, their add and mod nodes coloured by the tokens (keep left to the theme), and a design whose json cannot be read is a failed card", async () => {
  await loadPage({});
  stub.features = derived(5, passedUpTo(5));
  stub.workflow = stubFive();
  await ev("refresh()");
  await open();
  await step(5);
  await waitFor(`${count(".wfp-dg.drawn")} === 2`, "the 2 generated diagrams drawn");
  const caps = await ev(`Array.from(document.querySelectorAll('.wfp-dg-cap')).map((c) => c.firstChild.textContent)`);
  expect(JSON.stringify(caps) === JSON.stringify(["Data flow (generated)", "Call stack (generated)"]), `the captions read ${JSON.stringify(caps)}`);
  const paint = (kind, card) => ev(`(() => { const r = document.querySelectorAll('.wfp-dg')[${card}].querySelector('g.node.${kind} > rect'); if (!r) return null; const cs = getComputedStyle(r); return { fill: cs.fill, stroke: cs.stroke }; })()`);
  for (const card of [0, 1]) {
    const add = await paint("add", card), mod = await paint("mod", card), keep = await paint("keep", card);
    expect(add && mod && keep, `card ${card}: a node has no add, mod or keep class on its g.node`);
    expect(add.fill === (await tokenRgb("--pass-bg")) && add.stroke === (await tokenRgb("--pass")), `card ${card}: add reads ${JSON.stringify(add)}, not the pass tokens`);
    expect(mod.fill === (await tokenRgb("--todo-bg")) && mod.stroke === (await tokenRgb("--todo")), `card ${card}: mod reads ${JSON.stringify(mod)}, not the todo tokens`);
    expect(keep.fill !== add.fill && keep.fill !== mod.fill, `card ${card}: keep is coloured like add or mod: ${JSON.stringify(keep)}`);
  }
  const nodes = await ev(`Array.from(document.querySelectorAll('.wfp-dg')[0].querySelectorAll('g.node')).length`);
  expect(nodes === 3, `the Data flow draws ${nodes} nodes, expected 3`);
  stub.workflow = stubFive([{ title: "json design: JSONDecodeError Expecting value", source: "docs/design/stub.md § json design", doc: null, generated: null, mermaid: "" }]);
  await ev("refresh(true)");
  await waitFor(`${count(".wfp-dg.failed .diagram-error")} === 1 && ${count(".wfp-dg.drawn")} === 2`, "the failed card beside the 2 drawn ones");
  const failed = await ev(`document.querySelector('.wfp-dg.failed .wfp-dg-cap b').textContent`);
  expect(failed.startsWith("json design:"), `the failed card is captioned ${JSON.stringify(failed)}`);
});

// --- S81 (ticket 129): the Invariants block of phase 3 -- a chip and an evidence line per law, counts in the heading ---
const STUB_TESTS = ["tests/a.py::test_p", "tests/a.py::test_b", "tests/a.py::test_n", "tests/a.py::test_m"];
const stubRow = (n, status, test, evidence) => ({ n, law: `Law ${n}`, negation: `negation ${n}`, verified: test ? `\`${test}\`` : "`scripts/check_x.py`",
  tests: test ? [test] : [], status, evidence });
const none = (test) => [{ test, matrix: null, mutant: null, outcome: null, at: null }];
const STUB_INVARIANTS = { source: "docs/specs/stub.md", readable: true, tree: "/stub/repo", why: null, rows: [
  stubRow(1, "pinned", STUB_TESTS[0], [{ test: STUB_TESTS[0], matrix: "m-new", mutant: "M2", outcome: "pinned", at: "2026-11-20T10:00:00" }]),
  stubRow(2, "blind", STUB_TESTS[1], [{ test: STUB_TESTS[1], matrix: "m-old", mutant: "M5", outcome: "blind-test", at: "2026-11-10T10:00:00" }]),
  stubRow(3, "never-mutated", STUB_TESTS[2], none(STUB_TESTS[2])),
  stubRow(4, "test-missing", STUB_TESTS[3], none(STUB_TESTS[3])),
  stubRow(5, "not-checkable", null, []),
  stubRow(6, "pinned", STUB_TESTS[0], [{ test: STUB_TESTS[0], matrix: "m-new", mutant: "M2", outcome: "pinned", at: "2026-11-20T10:00:00" }])] };
const withInvariants = (invariants) => stubDesign(STUB_MERMAID, (p) => ({ ...p, invariants }));

await run("S81", "the Invariants block draws a card per law (negation and test always visible) with its status chip and evidence line, and a heading whose counts match the stub; an unreadable table is shown as written and a spec without one says so", async () => {
  await loadPage({});
  stub.features = derived(3, passedUpTo(3));
  stub.workflow = withInvariants(STUB_INVARIANTS);
  await ev("refresh()");
  await open();
  await step(3);
  await waitFor(`${count(".wfp-inv")} === 6`, "the 6 invariant cards");
  const order = await ev(`Array.from(document.querySelector('.wfp-art').children).map((c) => c.className.split(' ')[0])`);
  expect(JSON.stringify(order.slice(0, 3)) === JSON.stringify(["wfp-invs", "wfp-dgs", "wfp-texts"]), `the stage reads ${JSON.stringify(order)}`);
  const heading = await ev(`document.querySelector('.wfp-invs .wfp-h b').textContent`);
  const counts = await ev(`Array.from(document.querySelectorAll('.wfp-invs .wfp-inv-count')).map((c) => c.textContent)`);
  expect(heading === "Invariants · 6", `the heading reads ${JSON.stringify(heading)}`);
  expect((await ev(text(".wfp-invs .wfp-inv-tree"))) === "status from /stub/repo", "the tree line under the heading is missing or wrong");
  expect((await ev(text(".wfp-invs .wfp-inv-src"))) === "from docs/specs/stub.md § System invariants", "the source line under the heading is missing or wrong");
  expect(JSON.stringify(counts) === JSON.stringify(["2 pinned", "1 blind", "1 never mutated", "1 test missing", "1 not checkable"]), `the counts read ${JSON.stringify(counts)}`);
  const chips = await ev(`Array.from(document.querySelectorAll('.wfp-inv')).map((c) => [c.querySelector('.wfp-chip').textContent, c.querySelector('.wfp-chip').className.split(' ')[1]])`);
  expect(JSON.stringify(chips) === JSON.stringify([["pinned", "pinned"], ["blind", "blind"], ["never mutated", "never-mutated"], ["test missing", "test-missing"], ["not checkable", "not-checkable"], ["pinned", "pinned"]]), `the chips read ${JSON.stringify(chips)}`);
  const evidence = await ev(`Array.from(document.querySelectorAll('.wfp-inv-ev')).map((c) => c.textContent)`);
  expect(evidence[0] === "m-new M2 pinned" && evidence[1] === "m-old M5 blind-test" && evidence[2].startsWith("the test exists") && evidence[3] === "no def in the file today", `the evidence reads ${JSON.stringify(evidence)}`);
  const shown = await ev(`Array.from(document.querySelectorAll('.wfp-inv-neg, .wfp-inv-test')).every((c) => c.getBoundingClientRect().height > 0) && document.querySelectorAll('.wfp-inv-neg').length === 6`);
  expect(shown, "a negation or a test line is not visible on a card");
  const blind = await ev(`(() => { const c = getComputedStyle(document.querySelectorAll('.wfp-chip')[1]); return { color: c.color, border: c.borderTopColor }; })()`);
  expect(blind.color === (await tokenRgb("--fail")) && blind.border === (await tokenRgb("--fail")), `the blind chip reads ${JSON.stringify(blind)}, not the fail token`);
  expect((await ev(count("tr.row .wfp-chip, #view .wfp-inv"))) === 0, "an invariant signal on the table rows");
  stub.workflow = withInvariants({ source: "docs/specs/stub.md", readable: false, raw: "| Module | Owns |\n|---|---|\n| a | b |", why: "no column reads as law and negation (columns: Module, Owns)" });
  await ev("refresh(true)");
  await waitFor(`${count(".wfp-inv-raw")} === 1 && ${count(".wfp-inv")} === 0`, "the table shown as written");
  expect((await ev(text(".wfp-inv-raw"))).startsWith("| Module | Owns |") && (await ev(text(".wfp-invs .wfp-sec-miss"))).includes("no column reads as law"), "the raw table or the reason is not shown");
  stub.workflow = withInvariants(null);
  await ev("refresh(true)");
  await waitFor(`${count(".wfp-inv")} === 0 && ${text(".wfp-invs .wfp-nothing")} === "The spec has no § System invariants."`, "the one-line absence");
});

// --- S82-S83 (#130): motion. Rows move through a view transition, and none of it runs under reduced motion -------
// SPY counts the calls the page makes to the two APIs it animates with; it is installed before any page script.
const SPY = `window.__anim = 0; window.__vt = 0; const a = Element.prototype.animate; Element.prototype.animate = function (...x) { window.__anim++; return a.apply(this, x); };
  const v = Document.prototype.startViewTransition; if (v) Document.prototype.startViewTransition = function (...x) { window.__vt++; window.__named = Array.from(document.querySelectorAll('tr[data-id]')).every((tr) => tr.style.viewTransitionName === 'row-' + tr.dataset.id); return v.apply(this, x); };`;
// Ticket 130: DRAW records each time a diagram gains `draw-in` (its first drawing) and how many animations run under it
// at that moment (getAnimations flushes the style, so the nodes' and edges' CSS animations are there).
const DRAW_SPY = `window.__draw = { runs: 0, anims: 0 };
  new MutationObserver((list) => { for (const m of list) { const t = m.target; if (t.classList.contains("draw-in") && !(m.oldValue || "").split(/\\s+/).includes("draw-in")) { window.__draw.runs++; window.__draw.anims += t.getAnimations({ subtree: true }).length; } } })
    .observe(document, { subtree: true, attributes: true, attributeFilter: ["class"], attributeOldValue: true });`;
const openRowIds = () => ev(`Array.from(document.querySelectorAll('#view > .list tbody tr[data-id]')).map((tr) => tr.dataset.id)`);

await run("S82", "rows move through a view transition when the browser has them (a 304 and an empty plan start none, focus survives, the names go when it ends), and with startViewTransition deleted they are placed at once and the window works", async () => {
  await loadPage({ before: SPY });
  const data = await ev("JSON.parse(JSON.stringify(state.data))");
  expect(data.open.length >= 2, "the move needs 2 open rows in the real data");
  const ids = data.open.map((r) => r.id);
  const reversed = [...ids].reverse();
  const feed = (order) => () => ({ ...data, open: order.map((id) => data.open.find((r) => r.id === id)) });
  expect((await ev("typeof document.startViewTransition")) === "function", "this Edge has no View Transitions: S82 needs the API");
  const vt = () => ev("window.__vt");
  // a 304 starts no transition
  stub.features = tagged((real) => real);
  await ev("refresh()");
  await sleep(200);
  expect((await vt()) === 0, `a 304 started ${await vt()} view transition(s)`);
  // an identical answer is an empty plan: none either
  stub.features = feed(ids);
  await ev("refresh()");
  await sleep(200);
  expect((await vt()) === 0, `an empty plan started ${await vt()} view transition(s)`);
  // rows move: one transition, the order is the new one when refresh() returns, the rows carry their names, and the focus stays
  await ev(`document.querySelector(${JSON.stringify(rowSel(ids[0]))}).focus()`);
  stub.features = feed(reversed);
  await ev("refresh()");
  expect((await vt()) === 1, `a move started ${await vt()} view transition(s), not 1`);
  expect(JSON.stringify(await openRowIds()) === JSON.stringify(reversed), `the rows read ${JSON.stringify(await openRowIds())}, expected ${JSON.stringify(reversed)}`);
  expect(await ev("window.__named"), "the rows did not carry row-<id> as their view-transition-name when the transition began");
  expect((await ev(`document.activeElement && document.activeElement.dataset && document.activeElement.dataset.id`)) === ids[0], "the focus left the moved row");
  await waitFor(`Array.from(document.querySelectorAll('tr[data-id]')).every((tr) => tr.style.viewTransitionName === '')`, "the names to go when the transition ends");
  // a row leaves and comes back: one transition each, the DOM is the plan's at once
  stub.features = feed(reversed.slice(1));
  await ev("refresh()");
  expect((await vt()) === 2 && JSON.stringify(await openRowIds()) === JSON.stringify(reversed.slice(1)), `a removal: ${await vt()} transitions, rows ${JSON.stringify(await openRowIds())}`);
  stub.features = feed(ids);
  await ev("refresh()");
  expect((await vt()) === 3 && JSON.stringify(await openRowIds()) === JSON.stringify(ids), `a new row: ${await vt()} transitions, rows ${JSON.stringify(await openRowIds())}`);
  await sleep(400);
  // without the API the rows are placed at once and the window works
  await ev("delete document.startViewTransition; delete Document.prototype.startViewTransition");
  expect((await ev("typeof document.startViewTransition")) === "undefined", "could not remove startViewTransition");
  stub.features = feed(reversed);
  await ev("refresh()");
  expect(JSON.stringify(await openRowIds()) === JSON.stringify(reversed), `without the API the rows read ${JSON.stringify(await openRowIds())}`);
  stub.features = feed(reversed.slice(1));
  await ev("refresh()");
  expect(JSON.stringify(await openRowIds()) === JSON.stringify(reversed.slice(1)), "without the API a removed row is still there");
  stub.features = feed(ids);
  await ev("refresh()");
  expect(JSON.stringify(await openRowIds()) === JSON.stringify(ids), "without the API a new row is not placed");
  // a click opens the panel as on master; the row has a stage (title), unlike a row that shows only a banner
  await open(mine.id);
  expect((await ev(`state.panel !== null && state.panel.rowId === ${JSON.stringify(mine.id)} && ${count(".wfp-title")} === 1`)), "without the API the panel did not open on a click");
  await ev("closePanel()");
});

// The same walk in both modes: rows move, the phase changes, a diagram draws, a fold opens, a diagram opens full screen.
// What it leaves on the page is `endState()`; reduced motion must leave the same one, with nothing animated on the way.
async function walk(options) {
  await loadPage({ ...options, before: SPY + DRAW_SPY });
  const atPhase3 = (order) => (real) => {
    const r = bothAtPhase3(real);
    return order ? { ...r, open: order.map((id) => r.open.find((x) => x.id === id)) } : r;
  };
  const quiet = async (what) => {
    // reduced motion: the WHOLE list must be empty, Mermaid's and the page's own infinite animations included (the universal `animation: none !important` of the reduced block stops them too)
    const running = () => ev(`document.getAnimations().map((a) => a.animationName || a.transitionProperty || a.constructor.name)`);
    const at = await running();
    expect(at.length === 0, `${what}: ${JSON.stringify(at)} still run`);
    await sleep(120);
    const later = await running();
    expect(later.length === 0, `${what}, 120 ms later: ${JSON.stringify(later)} run`);
  };
  stub.features = atPhase3(null);
  stub.workflow = stubDesign();
  await ev("refresh()");
  const ids = await openRowIds();
  stub.features = atPhase3([...ids].reverse());
  await ev("refresh()");
  const reduced = options.motion === "reduce";
  if (reduced) await quiet("a row move");
  await open(mine.id);
  await step(2);
  await waitFor(`${text(".wfp-title")}.includes("Product review")`, "the stage of phase 2");
  await step(3);
  await waitFor(`${count(".wfp-dg.drawn")} === 3`, "the 3 stubbed diagrams drawn");
  if (reduced) await quiet("a phase change and the diagrams' draw");
  // a first drawing draws in (a class and animations under it), a redraw of the same source does not: the theme redraws every diagram in place
  const first = await ev("({ ...window.__draw })");
  await ev(`document.querySelectorAll('.wfp-art .diagram > svg').forEach((svg) => { svg.__old = 1; }); document.getElementById('theme-switch').click()`);
  await waitFor(`${count(".wfp-art .diagram > svg")} > 0 && !Array.from(document.querySelectorAll('.wfp-art .diagram > svg')).some((svg) => svg.__old)`, "the diagrams redrawn for the theme");
  const redrawn = await ev("({ ...window.__draw })");
  await foldClick(1);
  await waitFor(`document.querySelectorAll('details.wfp-fold')[1].open`, "the second fold open");
  if (reduced) {
    await quiet("a fold open");
    const folding = await ev(`getComputedStyle(document.querySelectorAll('details.wfp-fold')[1], '::details-content').transitionDuration`);
    expect(folding === "0s", `the fold's content transition lasts ${folding} under reduced motion`);
  }
  await ev(`document.querySelector('.wfp-dg.drawn .wfp-dg-open').click()`);
  await waitFor(`${count(".wfp-full")} === 1 && ${count(".wfp-full .diagram > svg")} === 1`, "the diagram full screen");
  if (reduced) await quiet("a full-screen open");
  // only what this ticket starts: WAAPI animations (stage fade, zoom), transitions (folds), the draw-in's CSS animations
  // and the stage's copy; never the page's or Mermaid's infinite ones (dashes, packet, edge-animation-frame)
  await waitFor(`document.getAnimations().filter((a) => a.constructor.name !== 'CSSAnimation' || /^(node-in|edge-in|row-in|row-out)$/.test(a.animationName)).length === 0 && !document.querySelector('.draw-in, .wfp-art-out')`, "every motion of this ticket to be over");
  return { calls: await ev("({ anim: window.__anim, vt: window.__vt })"), end: await endState(), order: JSON.stringify(ids), first, redrawn };
}
const endState = () => ev(`JSON.stringify({
  rows: Array.from(document.querySelectorAll('tr[data-id]')).map((tr) => tr.dataset.id),
  stage: Array.from(document.querySelectorAll('.wfp-art *:not(svg *)')).map((n) => n.tagName + '.' + (n.getAttribute('class') || '') + (n.tagName === 'DETAILS' ? (n.open ? '+open' : '-open') : '')),
  nodes: document.querySelectorAll('.wfp-art .diagram g.node').length,
  loose: document.querySelectorAll('.draw-in, [pathLength], [style*="view-transition-name"]').length,
  full: Array.from(document.querySelectorAll('.wfp-full *:not(svg *)')).map((n) => n.tagName + '.' + (n.getAttribute('class') || '')),
  attrs: [document.documentElement.dataset.motionFold, document.documentElement.dataset.motionDraw] })`);

await run("S83", "under reduced motion no animation runs (document.getAnimations() is empty after a row move, a phase change, a diagram draw, a fold open and a full-screen open, and the page calls neither startViewTransition nor animate) and the DOM equals the animated end state", async () => {
  const animated = await walk({});
  // rows (1 view transition) + the two phase changes (a fade in each, and the old stage's fade out when it had content) + the zoom (1)
  expect(animated.calls.vt >= 1 && animated.calls.anim >= 3, `the animated run made ${JSON.stringify(animated.calls)} calls: the walk did not exercise the motion (needs a view transition for the rows, a cross-fade per phase change and the zoom)`);
  // WITNESS (ticket 130, AC5): this goes red if `drawIn()` is deleted (no diagram would gain `draw-in`, so `runs` stays 0)
  // or if it stops starting the nodes' and edges' animations (`anims` stays 0); the redraw must add nothing to either.
  expect(animated.first.runs >= 3 && animated.first.anims >= animated.first.runs, `the first drawing of the 3 diagrams drew in ${JSON.stringify(animated.first)}: drawIn did not run or started no animation`);
  expect(JSON.stringify(animated.redrawn) === JSON.stringify(animated.first), `a redraw of the same source drew in again: ${JSON.stringify(animated.first)} then ${JSON.stringify(animated.redrawn)}`);
  const quiet = await walk({ motion: "reduce" });
  expect(quiet.calls.vt === 0 && quiet.calls.anim === 0, `under reduced motion the page called ${JSON.stringify(quiet.calls)}`);
  expect(quiet.first.runs === 0 && quiet.redrawn.runs === 0, `under reduced motion a diagram drew in: ${JSON.stringify(quiet.first)}`);
  expect(quiet.order === animated.order, `the rows started in another order: ${quiet.order} / ${animated.order}`);
  const a = JSON.parse(animated.end);
  const q = JSON.parse(quiet.end);
  expect(a.loose === 0, `the animated run left ${a.loose} motion leftovers (draw-in, pathLength, a view-transition-name)`);
  expect(q.attrs.join() === "none,none", `the motion switches read ${q.attrs} under reduced motion`);
  expect(a.attrs[1] === "in", `the draw-in switch reads ${a.attrs[1]} in the animated run`);
  for (const key of ["rows", "stage", "nodes", "loose", "full"]) {
    expect(JSON.stringify(a[key]) === JSON.stringify(q[key]), `the DOM differs in ${key}:\n  animated ${JSON.stringify(a[key]).slice(0, 300)}\n  reduced  ${JSON.stringify(q[key]).slice(0, 300)}`);
  }
});

await run("S84", "at 375 and 390 px the Diagrams and the folds stack in one column and nothing scrolls sideways, with a failed diagram and an empty block too", async () => {
  for (const width of [375, 390]) {
    await loadPage({ width });
    stub.features = derived(3, passedUpTo(3));
    stub.workflow = stubDesign([...STUB_MERMAID, "flowchart LR\n  A -->"]);   // the last one cannot be parsed
    await ev("refresh()");
    await open();
    await step(3);
    await waitFor(`${count(".wfp-dg.drawn")} === 3 && ${count(".wfp-dg.failed")} === 1`, `${width} px: 3 drawn diagrams and the failed card`);
    expect((await ev(count(".wfp-dg.failed .diagram-error"))) === 1, `${width} px: the failed card does not show its error`);
    const lefts = await ev(`Array.from(document.querySelectorAll('.wfp-dg')).map((c) => Math.round(c.getBoundingClientRect().left))`);
    expect(new Set(lefts).size === 1, `${width} px: the cards are not one column: lefts ${JSON.stringify(lefts)}`);
    const m = await ev(`({ page: document.documentElement.scrollWidth, inner: innerWidth, panel: document.getElementById('wfpanel').scrollWidth, panelW: document.getElementById('wfpanel').clientWidth })`);
    expect(m.page <= m.inner && m.panel <= m.panelW, `${width} px: page ${m.page}/${m.inner}, panel ${m.panel}/${m.panelW}`);
    await foldClick(0);
    await sleep(400);
    expect((await ev(`document.documentElement.scrollWidth <= innerWidth`)), `${width} px: an open fold scrolls the page sideways`);
    stub.workflow = stubDesign([]);   // the empty state: one line, no box
    await ev("refresh(true)");
    await waitFor(`${count(".wfp-dg")} === 0 && ${text(".wfp-dgs .wfp-nothing")} === "No diagram in this phase's documents."`, `${width} px: the empty Diagrams line`);
  }
});

await loadPage({});   // leave the page as it was found: light system, nothing stored
await ev("try { localStorage.removeItem('wf-theme'); } catch (e) {}");
await restoreEnvironment();

if (pageErrors.length) console.log(`page errors: ${[...new Set(pageErrors)].join(" | ")}`);
const failed = results.filter(([, ok]) => !ok).length;
console.log(`${results.length - failed}/${results.length} ok`);
// close the tab this run opened: Target.closeTarget, and the HTTP endpoint when the page socket refuses it
await send("Target.closeTarget", { targetId: target.id }).catch(() =>
  fetch(`http://127.0.0.1:${portArg}/json/close/${target.id}`).catch(() => {}));
ws.close();
process.exit(failed || pageErrors.length || (!ONLY && results.length !== 84) ? 1 : 0);
