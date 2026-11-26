"use strict";
// The window: fetches /api/features on load and every 10 s, renders Open / History / Workflow / Documents.
// Every string from disk goes through textContent, never innerHTML — except a markdown document,
// which `marked` turns into HTML: what keeps a hostile file from running code is the page CSP
// (scripts 'self' only), Decision 22. Mermaid runs in strict mode on the vendored files.
// Actions only open things (#26): the server embeds its token in the page only for a loopback
// Host, so without one (the phone) there is no Terminal button, only links and copy. Files open in
// the window (#46): there is no action that starts an editor.

const REFRESH_MS = 10000;
const FAST_MS = 1000;   // the poll while a phase is computing or refreshing (#65)
const SLOW_MS = 5000;   // past this, the first answer is called slow
const STARTED = performance.now();
const PHASES = WfPanel.PHASES;   // panel.js loads first (index.html): the decisions of the side panel live there
// theme.js is a blocking script in <head>: WfTheme exists here and the theme is already on <html> (#87)

const state = { data: null, tab: "open", filter: "", fetchedAt: null, failure: null,
  panel: null,   // the view of a row (#86, row-view): null, or { rowId, slug, chosen, openIds, openFolds, map, failure, token, keys, artToken, dot, stamped, tableScroll, pushed }
  marked: null,  // the row of the view just left: it stays marked in the table (row-view), until the tab changes or another row opens
  addressRead: false,   // the address of the first load is read once, when the first snapshot lists the rows (row-view)
  docFilter: "", docRepo: "", docList: null, docFailure: null, docToken: 0,
  wfMap: null, wfFailure: null, wfPhase: 0, wfToken: 0,
  etag: null,       // the version of the features answer the page holds (window-updates); null: none, so the next poll is asked whole
  lastDraw: null,   // the last measured draw, ms: what the footer keeps showing while a poll changes nothing
  screen: null,     // "open" / "history" while #view holds that tab's table (kept in place); null for anything else
  table: null,      // { tab, prints, wrap, body, nodes } of the table on screen
  other: null,      // the same for the Other worktrees group: { prints, box, body, nodes }
  wfEtag: null, docEtag: { repo: "", etag: null }, docRepoKey: "" };
const $ = (id) => document.getElementById(id);
const LOADING = $("loading").cloneNode(true);   // taken once, before the first render empties #view
const TOKEN = (document.querySelector('meta[name="wf-token"]') || {}).content || "";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "?" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function phaseCell(row) {
  const td = el("td", "phase");
  td.dataset.label = "Phase";
  const p = row.phase;
  if (p.kind === "delivered") {
    td.classList.add("pass");
    td.textContent = "delivered";
  } else if (p.kind === "no-spec") {
    td.classList.add("dim");
    td.textContent = "no spec";
  } else if (p.kind === "computing") {
    td.classList.add("dim");
    td.textContent = "computing…";
  } else {
    const last = p.verdicts[p.verdicts.length - 1];
    if (last && last.status === "fail") td.classList.add("fail");
    else td.classList.add("todo");
    td.textContent = `${p.phase} · ${PHASES[p.phase] || "?"}`;
    if (last && last.problems.length) td.title = last.problems.join("\n");
  }
  if (p.refreshing) {   // the last known phase above, the mark beside it
    const mark = el("span", "refreshing");
    mark.textContent = "refreshing";
    td.append(" ", mark);
    td.title = td.title ? `${td.title}\nchecking this phase again` : "checking this phase again";
  }
  return td;
}

function whereCell(row) {
  const td = el("td", "mono");
  td.dataset.label = "Where";
  const w = row.where;
  const head = w.kind === "none" ? "—" : w.kind + (w.branch ? " · " + w.branch : "");
  td.append(head);
  if (w.kind === "primary" && row.claim) td.append(` · claim since ${clock(row.claim.since)}`);
  const bits = [];
  if (w.dirty !== null) bits.push(w.dirty === 0 ? "clean" : `${w.dirty} dirty`);
  if (w.ahead !== null && w.ahead > 0) bits.push(WfPanel.aheadLabel(w));
  if (bits.length) td.append(document.createElement("br"), el("span", "dim", bits.join(" · ")));
  return td;
}

function agentCell(row) {
  const td = el("td");
  td.dataset.label = "Agent";
  if (!row.agent) {
    td.append(el("span", "pill muted", "no record"));
  } else {
    td.append(el("span", "pill ok", `active · ${row.agent.process} ${row.agent.pid}`));
    td.append(el("div", "dim mono", `since ${clock(row.agent.since)}`));
  }
  return td;
}

function signalsCell(row) {
  const td = el("td");
  td.dataset.label = "Signals";
  for (const s of row.signals) {
    const pill = el("span", s.name === "removable" ? "pill ok" : "pill warn", s.label);
    // A signal about another worktree of the row names it (#35).
    pill.title = s.path && s.path !== row.where.path ? `${s.rule}\n${s.path}` : s.rule;
    td.append(pill);
    const note = WfPanel.baseNoteLine(s, row.where);
    if (note) td.append(el("div", "signal-note", note));
  }
  return td;
}

// A legacy handoff has no repo: null on the wire, "repo unknown" on the page.
function repoLabel(row) {
  return row.repo === null ? "repo unknown" : row.repo;
}

function featureCell(row) {
  const td = el("td");
  td.dataset.label = "Feature";
  td.append(el("div", "slug", row.slug),
    el("div", row.repo === null ? "repo dim" : "repo", repoLabel(row)));
  if (row.error) td.append(el("div", "rowerr", row.error));
  td.append(actionsBar(row));
  return td;
}

let noticeTimer = null;

function notice(text) {
  const box = $("notice");
  box.textContent = text;
  box.hidden = text === "";
  clearTimeout(noticeTimer);
  if (text) noticeTimer = setTimeout(() => notice(""), 8000);
}

async function openAction(action, id) {
  try {
    const resp = await fetch("/api/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, id, token: TOKEN }) });
    if (resp.status !== 204) {
      let why = `HTTP ${resp.status}`;
      try { why = (await resp.json()).error || why; } catch (e) { /* no JSON body: keep the status */ }
      throw new Error(why);
    }
    notice("");
  } catch (err) {
    notice(`cannot open (${action}): ${err.message}`);
  }
}

function actionButton(label, action, id) {
  const b = el("button", "act", label);
  b.type = "button";
  b.addEventListener("click", () => openAction(action, id));
  return b;
}

function copyButton(slug) {
  const command = `python3 ~/agentic-dev-kit/wf.py next ${slug}`;
  const b = el("button", "act", `copy: wf.py next ${slug}`);
  b.type = "button";
  b.title = command;
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command);
      notice("");
    } catch (err) {
      notice(`cannot copy: ${err.message}`);
    }
  });
  return b;
}

function link(label, href) {
  const a = el("a", "act", label);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  return a;
}

// The spec copy of the worktree when it has one, else the first (the server lists the spec
// documents in trace order; the detail's handoffs come newest first).
function specTrace(row) {
  const specs = row.traces.filter((t) => t.kind === "spec");
  const here = row.where.path ? row.where.path + "/" : null;
  return specs.find((t) => here && t.path.startsWith(here)) || specs[0] || null;
}

function hasHandoff(row) {
  return row.traces.some((t) => t.kind === "handoff");
}

// A file button of the row: the detail names the document, the pane shows it.
function fileButton(label, row, pick) {
  const b = el("button", "act", label);
  b.type = "button";
  b.addEventListener("click", async () => {
    try {
      const resp = await fetch(`/api/features/${encodeURIComponent(row.id)}`, { cache: "no-store" });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
      const doc = pick(body.documents);
      if (!doc) throw new Error("no such document");
      notice("");
      openDocument(doc);
    } catch (err) {
      notice(`cannot open ${label.toLowerCase()}: ${err.message}`);
    }
  });
  return b;
}

const ticketLabel = WfPanel.ticketLabel;   // shared with the panel's ticket card

function actionsBar(row) {
  const bar = el("div", "actions");
  const links = row.links || {};
  if (TOKEN && row.where.path) bar.append(actionButton("Terminal", "terminal", row.id));
  const spec = specTrace(row);
  if (spec) bar.append(fileButton("Spec", row, (docs) => docs.find((d) => d.kind === "spec" && d.path === spec.path)));
  if (hasHandoff(row)) bar.append(fileButton("Handoff", row, (docs) => docs.find((d) => d.kind === "handoff")));
  if (links.pr) bar.append(link("PR", links.pr));
  if (links.ticket) bar.append(link(ticketLabel(links.tracker), links.ticket));
  else if (links.tracker) bar.append(el("span", "dim mono", links.tracker));
  bar.append(copyButton(row.slug));
  // The row opens the workflow panel on click or Enter: neither must come from the bar.
  for (const type of ["click", "keydown"]) bar.addEventListener(type, (e) => e.stopPropagation());
  return bar;
}

function dayOf(iso) {
  return typeof iso === "string" ? iso.slice(0, 10) : "";
}

function table(rows) {
  const t = el("table");
  const head = el("tr");
  const names = state.tab === "history" ? ["Feature", "Last activity", "Phase", "Where", "Agent", "Signals"]
    : ["Feature", "Phase", "Where", "Agent", "Signals"];
  for (const name of names) head.append(el("th", "", name));
  const thead = el("thead");
  thead.append(head);
  t.append(thead);
  const body = el("tbody");
  for (const row of rows) body.append(rowNode(row));
  t.append(body);
  return t;
}

// One row of the Open or History table.
function rowNode(row) {
  const tr = el("tr", state.panel && row.id === state.panel.rowId ? "row sel" : "row");
  tr.tabIndex = 0;
  tr.dataset.id = row.id;
  tr.addEventListener("click", () => openPanel(row.id));
  tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openPanel(row.id); });
  const cells = [featureCell(row), phaseCell(row), whereCell(row), agentCell(row), signalsCell(row)];
  if (state.tab === "history") {
    const when = el("td", "mono", dayOf(row.last_activity));
    when.dataset.label = "Last activity";
    cells.splice(1, 0, when);
  }
  tr.append(...cells);
  return tr;
}

function matches(row, needle) {
  const n = needle.trim().toLowerCase();
  return !n || row.slug.toLowerCase().includes(n) || repoLabel(row).toLowerCase().includes(n)
    || dayOf(row.last_activity).includes(n);
}

// One line per document: kind, name and "open" in the pane.
function documentList(docs) {
  const ul = el("ul", "lines");
  for (const doc of docs) {
    const li = el("li", "", "");
    li.append(el("span", "pill muted", doc.kind), " ", doc.name, " ");
    const open = el("button", "act", "open");
    open.type = "button";
    open.addEventListener("click", () => openDocument(doc));
    li.append(open);
    ul.append(li);
  }
  return ul;
}

function panel(title, count) {
  const box = el("div", "panel");
  const head = el("div", "panel-h");
  head.append(el("span", "", title));
  if (count !== undefined) head.append(el("span", "", String(count)));
  const body = el("div", "panel-b");
  box.append(head, body);
  return [box, body];
}

// A panel that starts closed: one line saying what it holds and that it opens.
function foldedPanel(noun, count) {
  const box = el("details", "panel");
  const head = el("summary", "panel-h");
  const body = el("div", "panel-b");
  const label = () => { head.textContent = `${box.open ? "▾" : "▸"} ${count} ${noun} — ${box.open ? "hide" : "show"}`; };
  box.addEventListener("toggle", label);
  label();
  box.append(head, body);
  return [box, body];
}

// The worktrees that are no feature (#93): a closed group under the Open table, one line each
// with where it is and what to clean; no panel, no `wf.py next`, no Spec, PR or ticket.
function otherGroup(rows) {
  const box = el("details", "panel other");
  const head = el("summary", "panel-h");
  box.dataset.count = String(rows.length);
  box.addEventListener("toggle", () => paintOtherLabel(box));
  const t = el("table");
  const body = el("tbody");
  for (const row of rows) body.append(otherRowNode(row));
  t.append(body);
  box.append(head, t);
  paintOtherLabel(box);
  return box;
}

// The summary line of the group: the arrow follows the open state, the count follows `data-count`.
function paintOtherLabel(box) {
  const text = `${box.open ? "▾" : "▸"} ${WfPanel.otherGroupLabel(Number(box.dataset.count))}`;
  const head = box.querySelector("summary");
  if (head.textContent !== text) head.textContent = text;
}

function otherRowNode(row) {
  const first = el("td");
  first.dataset.label = "Repo";
  first.append(el("div", row.repo === null ? "repo dim" : "repo", repoLabel(row)));
  if (TOKEN && row.where.path) {
    const bar = el("div", "actions");
    bar.append(actionButton("Terminal", "terminal", row.id));
    first.append(bar);
  }
  const tr = el("tr");
  tr.dataset.id = row.id;
  tr.append(first, whereCell(row), signalsCell(row));
  return tr;
}

function loadingBlock() {
  const waited = performance.now() - STARTED;
  const block = LOADING.cloneNode(true);   // the first-paint markup of index.html, not a second copy
  if (waited > SLOW_MS) {
    block.querySelector(".loading-text").textContent =
      `Still computing the features… (${Math.round(waited / 1000)} s)`;
  }
  return block;
}

// "scan;dur=12" -> { scan: 12 }; a missing header or entry is null.
function serverTiming(header) {
  const out = { scan: null };
  for (const entry of (header || "").split(",")) {
    const [name, ...params] = entry.trim().split(";");
    const dur = params.map((p) => p.trim()).find((p) => p.startsWith("dur="));
    if (name in out && dur) {
      const ms = Number(dur.slice(4));
      if (Number.isFinite(ms)) out[name] = ms;
    }
  }
  return out;
}

// A duration: `<n> ms` below 1000 ms, `<x.y> s` from there.
function duration(ms) {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// The time the page's scripts took to start the first refresh: taken once, at that refresh, so
// the footer keeps showing the durations of the page's own load.
let scriptsMs = null;

function showTimings(t) {
  const figure = WfPanel.drawFigure(t.kind, t.drawn - t.answered, state.lastDraw);
  if (figure.word === null) state.lastDraw = figure.ms;
  const nav = performance.getEntriesByType("navigation")[0];
  const server = t.scan === null ? "" :
    ` (server ${duration(t.scan)})`;
  const page = nav ? duration(nav.responseEnd) : "?";
  const scripts = scriptsMs === null ? "?" : duration(scriptsMs);
  $("timings").textContent = `page ${page} · scripts ${scripts}` +
    ` · table ${duration(t.answered - t.asked)}${server} · draw ` +
    (figure.word === null ? duration(figure.ms) : `${figure.word} (last ${duration(figure.ms)})`);
}

function render() {
  paintChrome();
  return paintView();
}

// A write only when the value differs: the chrome is repainted every poll and must not churn the DOM.
function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

// The banner, the tabs, the counts and the filter's visibility.
function paintChrome() {
  const d = state.data;
  const messages = [];
  if (state.failure) messages.push(`cannot reach the server: ${state.failure}${d ? " — showing the last view" : ""}`);
  if (d && d.partial) {
    const n = d.errors.length;
    messages.push(`${n} source${n === 1 ? "" : "s"} unreadable — this view is partial. ` +
      d.errors.map((e) => `${e.source}: ${e.message}`).join(" · "));
  }
  setHidden($("banner"), messages.length === 0);
  setText($("banner"), messages.join(" | "));
  setHidden($("filter"), state.tab === "open" || state.tab === "workflow");
  for (const tab of ["open", "history", "workflow", "documents"]) {
    $(`tab-${tab}`).classList.toggle("on", state.tab === tab);
    const selected = String(state.tab === tab);
    if ($(`tab-${tab}`).getAttribute("aria-selected") !== selected) $(`tab-${tab}`).setAttribute("aria-selected", selected);
  }
  if (d) {
    setText($("count-open"), String(d.open.length));
    setText($("count-history"), String(d.history.length));
  }
}

// The view. Open and History keep their table in place and change only the rows that differ (window-updates);
// the other screens are drawn whole, as they only change when the owner acts or their own version does.
function paintView() {
  const view = $("view");
  const d = state.data;
  const whole = (draw = () => {}) => {
    view.replaceChildren();
    state.screen = state.table = state.other = null;
    draw();
  };
  if (!d) {
    whole(() => view.append(state.failure ? el("div", "empty", "No data yet.") : loadingBlock()));
    return;
  }
  if (state.tab === "documents") return whole(() => showDocuments(view));
  if (state.tab === "workflow") return whole(() => showWorkflow(view));
  const rows = state.tab === "open" ? d.open : d.history.filter((r) => matches(r, state.filter));
  if (state.screen !== state.tab) {
    whole();
    state.screen = state.tab;
  }
  const message = rows.length > 0 ? null
    : state.tab === "open" ? "No open feature. See History for the past ones."
    : state.filter ? "No history entry matches the filter." : "No history yet.";
  syncMessage(view, message);
  const updates = [];   // the row changes of the tables already drawn: they move together, in one transition (ticket 130)
  syncTable(view, rows, updates);
  if (state.tab === "open") syncOther(view, WfPanel.otherRows(d), updates);
  return runRowUpdates(updates);
}

// The empty-state line at the top of the view: there when `text` is, gone when it is null.
function syncMessage(view, text) {
  const node = view.querySelector(":scope > .empty");
  if (text === null) {
    if (node) node.remove();
  } else if (node) setText(node, text);
  else view.prepend(el("div", "empty", text));
}

const rowPairs = (rows) => rows.map((r) => [r.id, WfPanel.rowPrint(r)]);

// Turn the rows of `body` into the planned ones touching only what the plan names: the removed rows
// go, a drawn row replaces its old node (or is inserted), a moved row is re-inserted; every other
// row, and the focus on it, stays. Walking from the end, each node goes before the one after it.
function applyPlan(body, nodes, plan, byId, make) {
  const focused = document.activeElement;
  let refocus = null;
  for (const id of plan.remove) {
    nodes.get(id).remove();
    nodes.delete(id);
  }
  const drawn = new Set(plan.draw);
  const moved = new Set(plan.move);
  let after = null;
  for (let i = plan.order.length - 1; i >= 0; i--) {
    const id = plan.order[i];
    let node = nodes.get(id);
    if (drawn.has(id)) {
      const fresh = make(byId.get(id));
      if (node) {
        if (node === focused) refocus = fresh;
        node.remove();
      }
      body.insertBefore(fresh, after);
      node = fresh;
      nodes.set(id, fresh);
    } else if (moved.has(id)) {
      if (node === focused) refocus = node;
      body.insertBefore(node, after);
    }
    after = node;
  }
  if (refocus) refocus.focus();
}

// --- ticket 130 (design-diagrams): motion. What runs is `WfPanel.motionOf`; the durations are `WfPanel.MOTION_MS`.

const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const hasViewTransitions = () => typeof document.startViewTransition === "function";
const motionEnv = () => ({
  reduced: reducedMotion(),
  viewTransitions: hasViewTransitions(),
  interpolateSize: typeof CSS !== "undefined" && CSS.supports("interpolate-size", "allow-keywords"),
});
const motion = () => WfPanel.motionOf(motionEnv());
const canAnimate = () => typeof Element.prototype.animate === "function";
// The one easing is the CSS keyword `ease` (soft, never past its end value, no bounce); the stylesheet's `--ease` is the same keyword and the script's WAAPI calls say `ease` too.

// The stylesheet's switches (the folds' height, the diagrams' draw-in) and durations follow the same function.
function paintMotion() {
  const m = motion();
  const root = document.documentElement;
  root.dataset.motionFold = m.fold;
  root.dataset.motionDraw = m.draw;
  for (const [name, ms] of Object.entries(WfPanel.MOTION_MS)) root.style.setProperty(`--ms-${name}`, `${ms}ms`);
}
paintMotion();
matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", paintMotion);

// Each table row carries the name its view transition follows: the same id gives the same name before and after.
const rowName = (id) => `row-${id}`;
function nameRows(named) {
  for (const on of [state.table, state.other]) {
    if (on) for (const [id, tr] of on.nodes) tr.style.viewTransitionName = named ? rowName(id) : "";
  }
}

// refresh() waits for the rows' update, which a view transition applies within a frame, but never longer than this
// (ticket 130): a transition the browser holds back (a page that is hidden, a stalled frame) must not stall the poll
// or the callers of refresh() that await it. The timer is cleared as soon as the update wins.
const UPDATE_WAIT_MS = 500;
function untilDrawn(update) {
  let timer;
  const cap = new Promise((done) => { timer = setTimeout(done, UPDATE_WAIT_MS); });
  return Promise.race([update, cap]).finally(() => clearTimeout(timer));
}

let rowsPending = 0;   // transitions whose update has not finished: while one is pending every change goes through one

// Apply the row changes. Moving, drawing or removing a row runs them inside a view transition when the browser
// has them and motion is on; an empty plan, a 304 and everything else is applied at once. Returns the promise
// of the update (undefined when it was applied at once). Each update reads the LATEST target of its table, so
// updates that run late (a transition ahead of them) end on the newest state, never on an older one.
function runRowUpdates(updates) {
  const apply = () => {
    for (const u of updates) if (u.alive()) u.apply();
    markRow();
  };
  const moves = updates.some((u) => u.plan.remove.length || u.plan.draw.length || u.plan.move.length);
  if (!(moves || rowsPending) || motion().rows !== "transition") return void apply();
  nameRows(true);
  const t = document.startViewTransition(() => { apply(); nameRows(true); });
  rowsPending++;
  t.ready.catch(() => {});   // a skipped transition (a newer one, a hidden page) still runs its update
  t.finished.catch(() => {}).finally(() => { if (--rowsPending === 0) nameRows(false); });
  return t.updateCallbackDone;
}

const nodesOf = (body) => new Map([...body.children].map((tr) => [tr.dataset.id, tr]));
const byIdOf = (rows) => new Map(rows.map((r) => [r.id, r]));

// The Open / History table of `rows` (never empty here: the empty state is a message).
function syncTable(view, rows, updates) {
  const on = state.table;
  if (rows.length === 0) {
    if (on) on.wrap.remove();
    state.table = null;
    return;
  }
  const next = rowPairs(rows);
  if (!on) {
    const wrap = el("div", "list");
    const t = table(rows);
    wrap.append(t);
    const group = view.querySelector(":scope > details.other");   // the table stays above the Other worktrees group
    if (group) group.before(wrap); else view.append(wrap);
    const body = t.tBodies[0];
    state.table = { prints: next, wrap, body, nodes: nodesOf(body) };
    return;
  }
  on.target = { next, rows };
  updates.push({ plan: WfPanel.planRows(on.prints, next), alive: () => state.table === on, apply: () => {
    applyPlan(on.body, on.nodes, WfPanel.planRows(on.prints, on.target.next), byIdOf(on.target.rows), rowNode);
    on.prints = on.target.next;
  } });
}

// The Other worktrees group: the `details` element stays, so its open state does; the summary count
// and the rows follow the plan; with none the group goes, and a new one comes back closed.
function syncOther(view, rows, updates) {
  const on = state.other;
  if (rows.length === 0) {
    if (on) on.box.remove();
    state.other = null;
    return;
  }
  const next = rowPairs(rows);
  if (!on) {
    const box = otherGroup(rows);
    view.append(box);
    const body = box.querySelector("tbody");
    state.other = { prints: next, box, body, nodes: nodesOf(body) };
    return;
  }
  on.target = { next, rows };
  updates.push({ plan: WfPanel.planRows(on.prints, next), alive: () => state.other === on, apply: () => {
    const { next: latest, rows: latestRows } = on.target;
    applyPlan(on.body, on.nodes, WfPanel.planRows(on.prints, latest), byIdOf(latestRows), otherRowNode);
    on.prints = latest;
    on.box.dataset.count = String(latestRows.length);
    paintOtherLabel(on.box);
  } });
}

function age() {
  if (state.fetchedAt === null) return;
  const s = Math.max(0, Math.round((Date.now() - state.fetchedAt) / 1000));
  $("fresh").textContent = `refreshed ${s} s ago`;
}

// `force` (the panel's R key) asks every answer whole instead of by version.
async function refresh(force = false) {
  const timing = { asked: performance.now(), answered: null, drawn: 0, scan: null, kind: "failed" };
  const nav = performance.getEntriesByType("navigation")[0];
  if (scriptsMs === null && nav) scriptsMs = timing.asked - nav.responseEnd;
  let fetched = false;
  let leftSlug = null;   // the panel's feature, when the new snapshot no longer lists it
  try {
    const resp = await fetch("/api/features", { cache: "no-store",
      headers: WfPanel.conditionalHeaders(force ? null : state.etag) });
    Object.assign(timing, serverTiming(resp.headers.get("Server-Timing")));
    timing.kind = WfPanel.answerKind(resp.status, state.data !== null);
    if (timing.kind === "failed") {
      let why = `HTTP ${resp.status}`;
      try { why = (await resp.json()).error || why; } catch (e) { /* no JSON body: keep the status */ }
      throw new Error(why);
    }
    if (timing.kind === "changed") {
      const body = await resp.json();
      state.data = body;
      state.etag = resp.headers.get("ETag");
      const ids = [...body.open, ...body.history].map((r) => r.id);
      if (state.panel && WfPanel.panelAfterSnapshot(state.panel.rowId, ids) === "close") leftSlug = state.panel.slug;
    }
    state.failure = null;
    state.fetchedAt = Date.now();   // a 304 confirms the content is current: "refreshed 0 s ago" (window-updates)
    fetched = true;
  } catch (err) {
    state.failure = err.message;
    state.etag = null;   // the next poll asks whole
  }
  // before the drawing: a drawing error must not stop the polling; after a failed fetch the
  // last good snapshot says nothing about now, so the normal pace applies
  schedule(fetched ? state.data : null);
  timing.answered = performance.now();   // a failure is an answer too
  if (timing.kind === "unchanged") paintChrome();   // nothing else is drawn: the banner may clear
  else if (timing.kind === "changed" && state.tab === "workflow") paintChrome();   // its map has its own version
  else if (timing.kind === "changed" && state.tab === "documents" && repoNames().join("\n") === state.docRepoKey) paintChrome();
  else await untilDrawn(render());
  requestAnimationFrame(() => {
    timing.drawn = performance.now();
    showTimings(timing);
  });
  age();
  if (!state.addressRead && state.data !== null) {
    state.addressRead = true;   // the address of the load names a row only the first snapshot can tell
    onAddressChange("load");
  }
  if (leftSlug !== null) {
    closePanel();
    notice(`The workflow panel closed: ${leftSlug} is no longer in the table.`);
  } else if (state.panel) {
    paintPanel();           // the dot and the words follow the new snapshot at once
    loadPanelWorkflow();    // the exit checks and artefacts follow it when they land
  }
  if (state.tab === "workflow") loadWorkflow({ force });
  else if (state.tab === "documents" && state.docRepo) loadDocuments({ force });
}

// The next refresh: fast while a row of the snapshot is computing or refreshing, normal otherwise. One timer
// is ever pending: each refresh schedules exactly one, after it ended.
let nextRefresh = null;
function schedule(snapshot) {
  clearTimeout(nextRefresh);
  nextRefresh = setTimeout(refresh, WfPanel.pollFast(snapshot) ? FAST_MS : REFRESH_MS);
}

function chooseTab(tab) {
  closePanel();   // the view belongs to the table that opened it
  state.marked = null;
  showTab(tab);
}

function showTab(tab) {
  state.tab = tab;
  const documents = tab === "documents";
  $("filter").value = documents ? state.docFilter : state.filter;
  $("filter").placeholder = documents ? "filter documents by name…" : "filter slug, repo, date…";
  $("filter").setAttribute("aria-label", documents ? "filter documents by name"
    : "filter history by slug, repo or date");
  render();
  if (documents && state.docRepo) loadDocuments();   // the stale list stays until the new one lands
  if (tab === "workflow") loadWorkflow();
}

// The repos of the snapshot that a row names (a legacy handoff has none).
function repoNames() {
  const d = state.data;
  const names = new Set([...d.open, ...d.history].map((r) => r.repo).filter((r) => r !== null));
  return [...names].sort();
}

function showDocuments(view) {
  const repos = repoNames();
  if (repos.length === 0) {
    view.append(el("div", "empty", "No repository in the snapshot."));
    return;
  }
  state.docRepoKey = repos.join("\n");
  if (!repos.includes(state.docRepo)) {
    state.docRepo = repos[0];
    state.docList = null;
    loadDocuments({ force: true });
  }
  const bar = el("div", "doc-bar");
  const select = el("select", "search");
  select.setAttribute("aria-label", "repository");
  for (const name of repos) {
    const option = el("option", "", name);
    option.value = name;
    option.selected = name === state.docRepo;
    select.append(option);
  }
  select.addEventListener("change", (e) => {
    state.docRepo = e.target.value;
    state.docList = null;
    state.docFailure = null;
    render();
    loadDocuments({ force: true });   // another repo has another version
  });
  bar.append(select);
  view.append(bar);
  if (state.docFailure) {
    view.append(el("div", "rowerr", `cannot list the documents: ${state.docFailure}`));
    return;
  }
  if (state.docList === null) {
    view.append(el("div", "empty", "Loading…"));
    return;
  }
  const needle = state.docFilter.trim().toLowerCase();
  const docs = state.docList.filter((doc) => !needle || doc.name.toLowerCase().includes(needle));
  if (docs.length === 0) {
    view.append(el("div", "empty", state.docList.length === 0 ? "No document in this repository."
      : "No document matches the filter."));
    return;
  }
  const box = el("div", "panel-b");
  box.append(documentList(docs));
  view.append(box);
}

// The Documents list: whole the first time, forced or after another repo was chosen; revalidated
// by its version otherwise, and an unchanged list is not redrawn (the open document and the filter stay).
async function loadDocuments({ force = false } = {}) {
  const repo = state.docRepo;
  if (!repo) return;
  const loaded = state.docList !== null && state.docEtag.repo === repo;
  const mode = WfPanel.tabLoadMode({ loaded, shown: state.tab === "documents", force });
  if (mode === "none") return;
  try {
    const resp = await fetch(`/api/documents?repo=${encodeURIComponent(repo)}`, { cache: "no-store",
      headers: WfPanel.conditionalHeaders(mode === "revalidate" ? state.docEtag.etag : null) });
    if (resp.status === 304 && mode === "revalidate") return;
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    if (state.docRepo !== repo) return;   // another repo was chosen meanwhile
    state.docList = body.documents;
    state.docEtag = { repo, etag: resp.headers.get("ETag") };
    state.docFailure = null;
  } catch (err) {
    if (state.docRepo !== repo) return;
    state.docList = null;
    state.docEtag = { repo, etag: null };
    state.docFailure = err.message;
  }
  render();
}

// --- the workflow map (#29): phases 0-6 as the server reads them from wf.py and gen-flow.py ---

async function loadWorkflow({ force = false } = {}) {
  const mode = WfPanel.tabLoadMode({ loaded: state.wfMap !== null, shown: state.tab === "workflow", force });
  if (mode === "none") return;
  const mine = ++state.wfToken;
  try {
    const resp = await fetch("/api/workflow", { cache: "no-store",
      headers: WfPanel.conditionalHeaders(mode === "revalidate" ? state.wfEtag : null) });
    if (resp.status === 304 && mode === "revalidate") return;   // the diagram the owner reads stays
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    if (state.wfToken !== mine) return;   // a newer request was made meanwhile
    state.wfMap = body;
    state.wfEtag = resp.headers.get("ETag");
    state.wfFailure = null;
  } catch (err) {
    if (state.wfToken !== mine) return;
    state.wfMap = null;
    state.wfEtag = null;
    state.wfFailure = err.message;
  }
  render();
}

function phaseBox(phase) {
  const box = el("button", "node", "");
  box.type = "button";
  if (phase.n === state.wfPhase) box.classList.add("sel");
  box.append(el("b", "", `${phase.n} · ${phase.name}`),
    el("span", "sk", phase.skills.length ? phase.skills.join(", ") : "—"));
  box.setAttribute("aria-pressed", String(phase.n === state.wfPhase));
  if (phase.n === state.wfPhase) box.setAttribute("aria-current", "true");
  box.addEventListener("click", () => { state.wfPhase = phase.n; render(); });
  return box;
}

// What `wf.py next` prints a file as: the phase file, a skill of the phase, a referenced one, or a file.
function documentRole(phase, doc) {
  if (doc.kind === "skill") return phase.referenced.includes(doc.name) ? "referenced" : "skill";
  return doc.name === phase.rules_file ? "phase" : "file";
}

function viewButton(doc) {
  const b = el("button", "act", "view");
  b.type = "button";
  b.addEventListener("click", () => openDocument(doc));
  return b;
}

function ruleList(rules) {
  const ul = el("ul", "doclist");
  for (const rule of rules) {
    const li = el("li", "", "");
    const level = el("span", "pill", rule.level || "—");
    level.title = rule.by;
    li.append(el("span", "n prose", rule.text), level);
    ul.append(li);
  }
  return ul;
}

function phaseDetail(phase) {
  const aside = el("aside", "detail");
  aside.append(el("div", "slug", `Phase ${phase.n} · ${phase.name} — click a box to see another phase`),
    el("div", "repo", phase.condition || "always"));
  const [readBox, readBody] = panel("Read (as wf next prints it)");
  const files = el("ul", "doclist");
  for (const doc of phase.documents) {
    const li = el("li", "", "");
    li.append(el("span", "k", documentRole(phase, doc)), el("span", "n", doc.name), viewButton(doc));
    files.append(li);
  }
  readBody.append(files);
  const missing = phase.skills.filter((s) => !phase.documents.some((d) => d.kind === "skill" && d.name === s));
  if (missing.length) readBody.append(el("p", "note", `not in the skills store: ${missing.join(", ")}`));
  const [rulesBox, rulesBody] = panel("Rules and where they are enforced", phase.rules.length);
  if (phase.rules.length) rulesBody.append(ruleList(phase.rules));
  else rulesBody.append(el("span", "dim", "— no rule names this phase"));
  aside.append(readBox, rulesBox);
  return aside;
}

function legendItem(word, meaning) {
  const item = el("span", "legend-item");
  item.append(el("b", "", word), ` ${meaning}`);
  return item;
}

// One line under the map: what a rule level means.
function legend() {
  const box = el("div", "legend");
  box.append(
    legendItem("structure", "a violation is impossible ·"), legendItem("script", "a script checks it ·"),
    legendItem("prompt", "only the prompt asks for it."));
  return box;
}

function showWorkflow(view) {
  const map = state.wfMap;
  if (state.wfFailure) {
    view.append(el("div", "rowerr", `cannot read the workflow: ${state.wfFailure}`));
    return;
  }
  if (!map) {
    view.append(el("div", "empty", "Loading…"));
    return;
  }
  const row = el("div", "map");
  map.phases.forEach((phase, i) => {
    if (i) row.append(el("span", "arrow", "→"));
    row.append(phaseBox(phase));
  });
  view.append(row, legend());
  const phase = map.phases.find((p) => p.n === state.wfPhase) || map.phases[0];
  const split = el("div", "split");
  const left = el("div", "wf-left");
  const [checkBox, checkBody] = panel(`What wf.py checks to leave phase ${phase.n}`);
  checkBody.append(el("div", "pre", phase.exit_check));
  left.append(checkBox);
  split.append(left, phaseDetail(phase));
  view.append(split);
  const strip = el("div", "strip");
  const [generalBox, generalBody] = foldedPanel("general rules", map.general_rules.length);
  generalBody.append(ruleList(map.general_rules));
  const [transBox, transBody] = panel("Transversal files", map.transversal.length);
  const files = el("ul", "doclist");
  for (const t of map.transversal) {
    const li = el("li", "", "");
    li.append(el("span", "n", t.file), el("span", "when", t.when));
    if (t.document) li.append(viewButton(t.document));
    files.append(li);
  }
  transBody.append(files);
  strip.append(generalBox, transBox);
  view.append(strip);
}

// --- the document pane: markdown with Mermaid drawn, or a mockup in a sandboxed frame ---

let mermaidTheme = null;   // the theme Mermaid was last initialised for; null = stale (#87)
let diagrams = 0;
let redrawGen = 0;
const diagramSources = new WeakMap();   // figure -> the Mermaid source it was drawn from

const currentTheme = () => document.documentElement.dataset.theme || "light";

// The tokens Mermaid takes its colours from, as the theme defines them right now.
function tokenValues() {
  const css = getComputedStyle(document.documentElement);
  const out = {};
  for (const name of ["--bg", "--surface", "--surface2", "--edge", "--fg", "--dim"]) out[name] = css.getPropertyValue(name).trim();
  return out;
}

// Mermaid loaded and set up for the current theme: base theme, variables from the tokens (the flowchart's labels are
// HTML in a foreignObject and Mermaid colours each diagram with its own <style>, so CSS alone cannot recolour it).
async function ensureMermaid() {
  await loadScript("/vendor/mermaid.min.js");
  if (mermaidTheme !== currentTheme()) {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base",
      themeVariables: WfTheme.mermaidVariables(tokenValues()) });
    mermaidTheme = currentTheme();
  }
}

// One packet on every link of a flowchart and of a state diagram, travelling along the edge's own path
// (sequence diagrams have straight lines and get none). The edges themselves dash through the stylesheet.
function decorateDiagram(figure) {
  const ns = "http://www.w3.org/2000/svg";
  for (const edge of figure.querySelectorAll("path.flowchart-link, path.transition")) {
    const d = edge.getAttribute("d");
    if (!d) continue;
    const packet = document.createElementNS(ns, "circle");
    packet.setAttribute("class", "packet");
    packet.setAttribute("r", "3");
    packet.style.setProperty("offset-path", `path("${d}")`);
    edge.after(packet);
  }
}

// A diagram's first drawing (never a redraw for the theme) draws in: the nodes fade in and the edges are drawn
// along their length, by CSS under `.draw-in`. The class and the edges' `pathLength` (which would scale their
// dashes) go when the animations end, so the diagram is the one the stylesheet always draws.
function drawIn(figure) {
  if (motion().draw !== "in" || typeof figure.getAnimations !== "function") return;
  const edges = figure.querySelectorAll("path.flowchart-link, path.transition");
  for (const edge of edges) edge.setAttribute("pathLength", "1");
  figure.classList.add("draw-in");
  const settled = figure.getAnimations({ subtree: true }).map((a) => a.finished.catch(() => {}));
  Promise.all(settled).then(() => {
    figure.classList.remove("draw-in");
    for (const edge of edges) edge.removeAttribute("pathLength");
  });
}

// The theme changed: every drawn diagram is rendered again IN PLACE from its kept source, so the scroll, the
// stage and the selection do not move. A newer switch supersedes an older redraw.
async function redrawDiagrams() {
  const mine = ++redrawGen;
  const figures = [...document.querySelectorAll(".diagram")].filter((f) => diagramSources.has(f));
  if (!figures.length || typeof mermaid === "undefined") return;
  try { await ensureMermaid(); } catch (err) { return; }
  for (const figure of figures) {
    if (mine !== redrawGen) return;
    if (!figure.isConnected) continue;
    try {
      const { svg } = await mermaid.render(`wf-mermaid-${++diagrams}`, diagramSources.get(figure));
      if (mine !== redrawGen) return;
      figure.innerHTML = svg;
      decorateDiagram(figure);
    } catch (err) { /* the old drawing stays */ }
  }
}

// A block that does not parse shows its source and the error, never a blank.
async function drawDiagram(code, source = code.textContent) {
  const holder = code.closest("pre") || code;
  const id = `wf-mermaid-${++diagrams}`;
  const theme = currentTheme();
  try {
    const { svg } = await mermaid.render(id, source);
    const figure = el("div", "diagram");
    figure.innerHTML = svg;
    diagramSources.set(figure, source);
    decorateDiagram(figure);
    holder.replaceWith(figure);
    drawIn(figure);
    if (theme !== currentTheme()) redrawDiagrams();   // the theme changed while this one was drawn
  } catch (err) {
    for (const stray of [id, `d${id}`]) { const node = document.getElementById(stray); if (node) node.remove(); }
    const failed = el("div", "diagram-error");
    failed.append(el("div", "rowerr", `Mermaid parse error: ${err && err.message ? err.message : err}`),
      el("pre", "", source));
    holder.replaceWith(failed);
  }
}

// One <script> element per same-origin src, loaded on first use (the CSP allows 'self', not inline).
// Only a success is memoised: a failed load leaves no trace, so the next document tries again.
const scriptLoads = new Map();
function loadScript(src) {
  if (!scriptLoads.has(src)) {
    scriptLoads.set(src, new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => {
        scriptLoads.delete(src);
        tag.remove();
        reject(new Error(`cannot load ${src}`));
      };
      document.head.append(tag);
    }));
  }
  return scriptLoads.get(src);
}

async function renderMarkdown(text, into) {
  await loadScript("/vendor/marked.umd.js");
  into.innerHTML = marked.parse(text);
  const blocks = [...into.querySelectorAll("code.language-mermaid")];
  if (!blocks.length) return;
  await ensureMermaid();
  for (const code of blocks) await drawDiagram(code);
}

// A report: `.jsonl` is a table (one row per line, columns = the keys in first-seen order, a long
// cell clipped with its full text on hover); `.json` is indented text. Every cell is textContent.
const CELL_MAX = 80;

function cellText(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function jsonlTable(text) {
  const rows = text.split("\n").filter((line) => line.trim() !== "").map((line) => {
    try {
      const v = JSON.parse(line);
      return v !== null && typeof v === "object" && !Array.isArray(v) ? v : { value: v };
    } catch (e) {
      return { "(not JSON)": line };
    }
  });
  const columns = [];
  for (const r of rows) for (const k of Object.keys(r)) if (!columns.includes(k)) columns.push(k);
  const t = el("table");
  const head = el("tr");
  for (const c of columns) head.append(el("th", "", c));
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  for (const r of rows) {
    const tr = el("tr");
    for (const c of columns) {
      const full = c in r ? cellText(r[c]) : "";
      const td = el("td", "mono", full.length > CELL_MAX ? full.slice(0, CELL_MAX) + "…" : full);
      if (full.length > CELL_MAX) td.title = full;
      tr.append(td);
    }
    tbody.append(tr);
  }
  t.append(thead, tbody);
  return t;
}

function renderReport(name, text, into) {
  if (name.endsWith(".jsonl")) {
    into.append(text.trim() === "" ? el("div", "dim", "— empty report") : jsonlTable(text));
    return;
  }
  let shown = text;
  try { shown = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* not JSON: show it as it is */ }
  into.append(el("pre", "", shown));
}

function closeDocument() {
  state.docToken++;
  const pane = $("docpane");
  pane.hidden = true;
  pane.replaceChildren();
}

// Fills `body` with a document: a mockup in a sandboxed frame, a report as a table, anything else as
// Markdown with its Mermaid diagrams. `token` is `{ get, mine }`: the answer is dropped when `get()` is no
// longer `mine` (another document was opened, or the panel moved on). Every failure is a line in `body`.
async function fillDocument(doc, body, token) {
  if (doc.kind === "mockup") {
    const frame = el("iframe", "mockup");
    frame.setAttribute("sandbox", "");
    frame.title = doc.name;
    frame.src = `/api/documents/${encodeURIComponent(doc.id)}`;
    body.append(frame);
    return;
  }
  body.append(el("div", "dim", "Loading…"));
  try {
    const resp = await fetch(`/api/documents/${encodeURIComponent(doc.id)}`, { cache: "no-store" });
    if (!resp.ok) {
      let why = `HTTP ${resp.status}`;
      try { why = (await resp.json()).error || why; } catch (e) { /* no JSON body: keep the status */ }
      throw new Error(why);
    }
    const text = await resp.text();
    if (token.get() !== token.mine) return;   // another document was opened meanwhile
    body.replaceChildren();
    if (doc.kind === "report") renderReport(doc.name, text, body);
    else await renderMarkdown(text, body);
  } catch (err) {
    if (token.get() !== token.mine) return;
    body.replaceChildren(el("div", "rowerr", `cannot open the document: ${err.message}`));
  }
}

async function openDocument(doc) {
  const mine = ++state.docToken;
  const pane = $("docpane");
  pane.hidden = false;
  const bar = el("div", "docpane-h");
  bar.append(el("span", "pill muted", doc.kind), el("span", "slug", doc.name));
  const close = el("button", "act", "close");
  close.type = "button";
  close.addEventListener("click", closeDocument);
  bar.append(close);
  const body = el("div", "doc");
  pane.replaceChildren(bar, body);
  pane.scrollIntoView();
  await fillDocument(doc, body, { get: () => state.docToken, mine });
}

// --- the workflow side panel (#86): opened by a row, painted from WfPanel.panelView ---

let tokens = 0;   // one counter for every panel request and artefact, so a new panel never reuses a token

function panelRow() {
  const d = state.data;
  return d && state.panel ? [...d.open, ...d.history].find((r) => r.id === state.panel.rowId) || null : null;
}

function markRow() {
  const id = state.panel ? state.panel.rowId : state.marked;
  for (const tr of document.querySelectorAll("tr[data-id]")) tr.classList.toggle("sel", tr.dataset.id === id);
}

// The frame of the panel, built once: top bar, banner, stepper, and the regions of the stage.
let panelFrame = null;

function buildPanelFrame() {
  const aside = $("wfpanel");
  const top = el("div", "wfp-top");
  const back = el("button", "wfp-back-pill");   // the way back to the table, at every width (row-view)
  back.type = "button";
  back.setAttribute("aria-label", "Back to the table");
  back.title = "Back to the table (Esc)";
  back.append(el("span", "wfp-back-arrow", "←"), " Back");
  back.addEventListener("click", leaveView);
  const prev = el("button", "wfp-move wfp-row-prev", "‹");
  prev.type = "button";
  prev.title = "Previous row (k)";
  prev.setAttribute("aria-label", "Previous row");
  prev.addEventListener("click", () => moveRow(-1));
  const next = el("button", "wfp-move wfp-row-next", "›");
  next.type = "button";
  next.title = "Next row (j)";
  next.setAttribute("aria-label", "Next row");
  next.addEventListener("click", () => moveRow(1));
  const slug = el("span", "slug wfp-slug");
  const agent = el("span", "wfp-agent", "agent active");
  top.append(back, prev, next, slug, agent);
  const banner = el("div", "wfp-banner");
  const nav = el("nav");
  nav.setAttribute("aria-label", "Phases");
  nav.className = "wfp-nav";
  const steps = el("ol", "wfp-steps");
  const buttons = PHASES.map((_, n) => {
    const li = el("li");
    const b = el("button", "wfp-step");
    b.type = "button";
    b.addEventListener("click", () => choosePhase(n));
    li.append(b);
    steps.append(li);
    return b;
  });
  nav.append(steps);
  const stage = el("section", "wfp-stage");
  const head = el("div", "wfp-head");
  const art = el("div", "wfp-art");
  const idea = el("div", "wfp-idea");
  const foot = el("div", "wfp-foot");
  stage.append(head, art, idea, foot);
  const body = el("div", "wfp-body");
  body.append(nav, stage);
  const hints = el("div", "wfp-keys");
  aside.replaceChildren(top, banner, body, hints);
  syncSwitches();
  return { aside, slug, agent, prev, next, banner, buttons, head, art, idea, foot, stage, hints };
}

function paintTop(view) {
  panelFrame.slug.textContent = view.slug;
  panelFrame.agent.hidden = !view.agent;
  panelFrame.aside.setAttribute("aria-label", `Workflow of ${view.slug}`);
  const ids = shownIds();   // the ends have no neighbour: the button is grey (Decision 2)
  panelFrame.prev.disabled = WfPanel.neighbourRow(ids, state.panel.rowId, -1) === null;
  panelFrame.next.disabled = WfPanel.neighbourRow(ids, state.panel.rowId, 1) === null;
}

// Where the dot was and which step is being stamped, decided from the view before anything is painted (#87). The
// stamp is the passed phase the dot just left: it lives until its animation ends, not until the next paint,
// because every refresh paints twice (the snapshot, then the workflow answer).
function followDot(panel, view) {
  const live = view.steps.find((s) => s.live);
  const left = WfPanel.dotMove(panel.dot, live ? live.n : null, view.steps);
  panel.dot = live ? live.n : null;
  if (left !== null) panel.stamped = left;
}

function paintStepper(view) {
  const panel = state.panel;
  view.steps.forEach((step, i) => {
    const b = panelFrame.buttons[i];
    const stamping = panel.stamped === step.n;
    const fresh = stamping && !b.classList.contains("stamped");
    b.className = `wfp-step ${step.tone}${step.live ? " live" : ""}${view.agent && step.live ? " pulse" : ""}`
      + `${step.current ? " current" : ""}${step.lit ? " lit" : ""}${step.flows ? " flows" : ""}`
      + `${stamping ? " stamped" : ""}`;
    if (step.current) b.setAttribute("aria-current", "step");
    else b.removeAttribute("aria-current");
    const word = b.querySelector(".wfp-word");
    if (stamping && !step.live && word && word.textContent === step.word) {
      // the word keeps being the same node, or its animation would start again
      b.querySelector(".wfp-dot")?.remove();
    } else {
      const bits = [el("span", "wfp-no", String(step.n)), el("span", "wfp-nm", step.name)];
      if (step.word) bits.push(el("span", "wfp-word", step.word));
      if (step.live) {
        const dot = el("span", "wfp-dot");
        dot.append(el("span", "vh", "live phase"));
        bits.push(dot);
      }
      b.replaceChildren(...bits.flatMap((n) => [n, " "]));
    }
    if (fresh) endStampWith(panel, b, step.n);
  });
}

// The stamp ends when the word's animations end (at once where there are none: reduced motion).
function endStampWith(panel, b, n) {
  const animations = b.querySelector(".wfp-word").getAnimations();
  const over = () => {
    if (state.panel === panel && panel.stamped === n) panel.stamped = null;
    b.classList.remove("stamped");
  };
  Promise.all(animations.map((a) => a.finished)).then(over, over);
}

function paintHints(view) {
  panelFrame.hints.replaceChildren(...view.hints.map((hint) => {
    const item = el("span");
    hint.keys.forEach((key, i) => item.append(i ? " " : "", el("kbd", "", key)));
    item.append(` ${hint.text}`);
    return item;
  }));
}

function paintBanner(view) {
  const box = panelFrame.banner;
  box.hidden = view.banner === null;
  box.className = `wfp-banner ${view.banner ? view.banner.tone : ""}`;
  box.textContent = view.banner ? view.banner.text : "";
  if (view.banner) box.setAttribute("role", view.banner.tone === "bad" ? "alert" : "status");
  else box.removeAttribute("role");
}

function stepButton(label, n, className) {
  const b = el("button", `act ${className}`, label);
  b.type = "button";
  b.addEventListener("click", () => choosePhase(n));
  return b;
}

// The head of the stage, the key idea and the foot: redrawn when the head key moves.
function paintHead(view) {
  const f = panelFrame;
  const s = view.stage;
  if (!s) {
    f.head.replaceChildren();
    f.idea.replaceChildren();
    f.idea.className = "wfp-idea";
    f.foot.replaceChildren();
    return;
  }
  const tags = el("div", "wfp-tags");
  for (const tag of s.tags) tags.append(el("span", `wfp-tag ${WfPanel.TONES[tag] || (tag.startsWith("●") ? "live" : "")}`, tag), " ");
  const stats = el("div", "wfp-stats");
  for (const { label, value, bad } of s.stats) {
    const box = el("div", bad ? "wfp-stat bad" : "wfp-stat");
    box.append(el("b", "", label), el("span", "", value));
    stats.append(box);
  }
  const head = [el("div", "wfp-crumb", s.crumb), el("h3", "wfp-title", `${s.n} · ${s.title}`)];
  if (s.lede) head.push(el("p", "wfp-lede", s.lede));
  f.head.replaceChildren(...head, tags, stats);
  f.idea.className = `wfp-idea${s.word === "SKIP" ? " skipped" : s.idea.heading === "delivered" ? " done" : ""}`;
  const idea = [el("b", "", s.idea.heading), el("p", "", s.idea.text)];
  for (const problem of s.idea.problems) idea.push(el("div", "wfp-problem", `${s.word} · ${problem}`));
  f.idea.replaceChildren(...idea);
  const foot = [];
  if (s.back) foot.push(stepButton(`← ${s.back.name}`, s.back.n, "wfp-back"));
  if (s.next) foot.push(stepButton(`Next: ${s.next.name} →`, s.next.n, "wfp-next"));
  f.foot.replaceChildren(...foot);
}

// The cards and the open artefact: redrawn only when the art key moves (so a refresh leaves the
// drawn diagrams, the scroll and the iframe alone).
function paintArtefacts(view) {
  const f = panelFrame;
  const panel = state.panel;
  panel.artToken = ++tokens;
  const mine = panel.artToken;
  const s = view.stage;
  if (!s) {
    f.art.replaceChildren();
    return;
  }
  closeFullScreen();   // the stage is drawn again: a diagram held full screen belongs to the old one
  const parts = [];
  let fills = null;   // the section bodies of the plain layout, filled one after the other
  const token = { get: () => (state.panel === panel ? panel.artToken : -1), mine };
  const draws = [];
  if (s.layout === "design") {
    const blocks = { invariants: () => invariantsBlock(s), diagrams: () => diagramsBlock(s, token, draws),
      folds: () => foldsBlock(s, token, draws) };
    for (const name of s.blocks) parts.push(blocks[name]());
  } else {
    fills = [];
    for (const sec of s.sections) parts.push(sectionBlock(sec, fills));
  }
  if (s.cards.length) {
    const cards = el("div", "wfp-cards");
    for (const card of s.cards) {
      if (card.kind === "link") {
        const a = el("a", "wfp-card link", card.name);
        a.href = card.href;
        a.target = "_blank";
        a.rel = "noopener";
        cards.append(a);
      } else {
        const b = el("button", `wfp-card ${card.docKind}${card.open ? " on" : ""}`, card.name);
        b.type = "button";
        b.title = card.docKind;
        b.setAttribute("aria-pressed", String(card.open));
        b.addEventListener("click", () => showArtefact(card.id));
        cards.append(b);
      }
    }
    parts.push(cards);
  }
  if (s.nothing) parts.push(el("div", "empty wfp-nothing", s.nothing));
  let body = null;
  const open = s.cards.find((c) => c.open);
  if (open) {
    body = el("div", "wfp-doc doc");
    parts.push(body);
  }
  // a new phase cross-fades (ticket 130); the same phase redrawn, or the first stage of a row, does not
  const changed = panel.shown !== undefined && panel.shown !== s.n;
  panel.shown = s.n;
  const fade = changed && motion().stage === "fade" && canAnimate();
  const leaving = fade ? snapshotOf(f.art) : null;
  f.art.replaceChildren(...parts);   // the DOM is the new stage when this function returns, with or without motion
  if (fade) crossFadeStage(f.art, leaving);
  for (const draw of draws) draw();
  if (open) fillDocument({ id: open.id, kind: open.docKind, name: open.name }, body, token);
  // each section body is its own request, asked when the stage draws it, one after the other
  if (fills) (async () => {
    for (const [sec, bodyBox] of fills) await fillDocument({ id: sec.id, kind: "section", name: sec.title }, bodyBox, token);
  })();
}

// The old stage fades out as the new one fades in (ticket 130, within MOTION_MS.stage), with the DOM already the
// new one. The old content is copied into a shadow root of an overlay (`.wfp-art-out`: aria-hidden, inert, not
// clickable) over the art region, so no selector or id of the page can reach the copy; the app's stylesheet is
// adopted into that root so the copy looks as it did. The overlay is removed when its fade ends, is cancelled or
// is replaced by the next phase change. No overlay where the browser cannot build one: the new stage then only fades in.
let appSheet = null;
let leavingStage = null;
function snapshotOf(art) {
  if (!art.childNodes.length || typeof art.attachShadow !== "function" || typeof CSSStyleSheet !== "function"
      || !("adoptedStyleSheets" in Document.prototype)) return null;
  if (!appSheet) {
    const css = [...document.styleSheets].map((sheet) => {
      try { return [...sheet.cssRules].map((rule) => rule.cssText).join("\n"); } catch (err) { return ""; }
    }).join("\n");
    appSheet = new CSSStyleSheet();
    appSheet.replaceSync(css);
  }
  const host = el("div", "wfp-art-out");
  host.setAttribute("aria-hidden", "true");
  host.inert = true;
  Object.assign(host.style, { top: `${art.offsetTop}px`, left: `${art.offsetLeft}px`, width: `${art.offsetWidth}px`, height: `${art.offsetHeight}px` });
  const copy = el("div", "wfp-art");
  copy.append(...[...art.childNodes].map((node) => node.cloneNode(true)));
  copy.querySelectorAll("iframe").forEach((frame) => frame.remove());   // a copy would load the document again
  const root = host.attachShadow({ mode: "open" });
  root.adoptedStyleSheets = [appSheet];
  root.append(copy);
  return host;
}

function crossFadeStage(art, leaving) {
  const timing = { duration: WfPanel.MOTION_MS.stage, easing: "ease" };
  art.animate([{ opacity: 0 }, { opacity: 1 }], timing);
  if (!leaving) return;
  if (leavingStage) leavingStage.remove();   // a phase change while one runs: the older copy goes first
  leavingStage = leaving;
  art.parentElement.append(leaving);
  const gone = () => { leaving.remove(); if (leavingStage === leaving) leavingStage = null; };
  const fade = leaving.animate([{ opacity: 1 }, { opacity: 0 }], timing);
  fade.addEventListener("finish", gone);
  fade.addEventListener("cancel", gone);
}

// One gate section of the stage: its bare title and its source, then the rendered body (filled later, by
// `paintArtefacts`), or, where there is no body, the line the gate (or the read error) gives.
function sectionBlock(sec, fills) {
  const box = el("section", `wfp-sec ${sec.state}`);
  const head = el("div", "wfp-sec-h");
  head.append(el("b", "wfp-sec-t", sec.title), el("span", "wfp-sec-src", sec.source));
  box.append(head);
  if (sec.state === "present") {
    const body = el("div", "wfp-sec-body doc");
    box.append(body);
    fills.push([sec, body]);
  } else if (sec.state === "missing") {
    const line = el("div", "rowerr wfp-sec-miss", `The section ${sec.title} is missing.`);
    line.title = sec.tip;
    box.append(line);
  } else {
    box.append(el("div", "rowerr wfp-sec-miss", `cannot read the section ${sec.title}: ${sec.tip}`));
  }
  return box;
}

// --- design-diagrams (ticket 127): the stage of a design phase -- its diagrams, then its texts folded ---

// The heading of a block of the stage: its name, a count or a hint at the right.
function stageHeading(name, note) {
  const h = el("div", "wfp-h");
  h.append(el("b", "", name), el("span", "wfp-h-note", note));
  return h;
}

// The Diagrams block: one card per Mermaid source, captioned with its heading path and its source. A card
// holds a placeholder of its own size until Mermaid has drawn it (or says why it cannot); a drawn one opens
// full screen on a click. `draws` collects what runs once the block is in the page.
function diagramsBlock(s, token, draws) {
  const box = el("section", "wfp-dgs");
  box.append(stageHeading("Diagrams", s.diagrams.length ? String(s.diagrams.length) : ""));
  if (!s.diagrams.length) {
    box.append(el("div", "empty wfp-nothing", "No diagram in this phase's documents."));
    return box;
  }
  const grid = el("div", "wfp-dg-grid");
  const waiting = [];
  for (const d of s.diagrams) {
    const card = el("figure", "wfp-dg waiting");
    const cap = el("figcaption", "wfp-dg-cap");
    cap.append(el("b", "", d.title), el("span", "", d.source));
    if (!d.mermaid) {   // a generated diagram that could not be made (ticket 128): a failed card that says why, never an empty box
      card.className = "wfp-dg failed";
      card.append(cap, el("div", "diagram-error rowerr", "The json design block could not be turned into a diagram (see the title); nothing is drawn."));
      grid.append(card);
      continue;
    }
    const wait = el("div", "wfp-dg-wait");
    card.append(cap, wait);
    grid.append(card);
    waiting.push([card, wait, d.mermaid]);
  }
  box.append(grid);
  draws.push(async () => {
    try { await ensureMermaid(); } catch (err) {
      for (const [card, wait] of waiting) {
        card.className = "wfp-dg failed";
        wait.replaceWith(el("div", "diagram-error rowerr", `cannot load the diagram renderer: ${err.message}`));
      }
      return;
    }
    for (const [card, wait, source] of waiting) {
      if (token.get() !== token.mine) return;   // the stage was drawn again meanwhile
      await drawDiagram(wait, source);
      const drawn = card.querySelector(".diagram");
      card.className = drawn ? "wfp-dg drawn" : "wfp-dg failed";
      if (drawn) {
        const open = el("button", "wfp-dg-open");
        open.type = "button";
        open.title = "Open full screen";
        open.addEventListener("click", () => openFullScreen(card, open));
        drawn.replaceWith(open);
        open.append(drawn);
      }
    }
  });
  return box;
}

// The Invariants block (ticket 129): a heading with the count of each status, then one card per law that always
// shows its negation, its test and a status chip with the evidence line. A table that could not be read is shown as
// written with the reason; a spec with no table says so in one line. No signal outside the stage (owner's answer).
function invariantsBlock(s) {
  const box = el("section", "wfp-invs");
  const v = s.invariants;
  if (!v) {
    box.append(stageHeading("Invariants", ""), el("div", "empty wfp-nothing", s.noInvariants));
    return box;
  }
  const head = stageHeading(v.heading, "");
  const note = head.querySelector(".wfp-h-note");
  for (const c of v.counts) note.append(el("span", `wfp-inv-count ${c.status}`, `${c.n} ${c.label}`));
  box.append(head);
  box.append(el("div", "wfp-inv-src", `from ${v.source}`));
  if (v.tree) box.append(el("div", "wfp-inv-tree", v.tree));
  if (v.raw === null && v.why) box.append(el("div", "rowerr wfp-sec-miss", v.why));
  if (v.raw !== null) {
    box.append(el("div", "rowerr wfp-sec-miss", `The columns of this table are not law · negation · test (${v.why}). It is shown as written.`),
      el("pre", "wfp-inv-raw", v.raw));
    return box;
  }
  if (!v.cards.length) return box;
  const list = el("div", "wfp-inv-list");
  for (const c of v.cards) {
    const card = el("div", "wfp-inv");
    card.append(el("span", "wfp-inv-n", String(c.n)), el("span", "wfp-inv-law", c.law),
      el("span", `wfp-chip ${c.status}`, c.word));
    if (c.negation) card.append(el("span", "wfp-inv-neg", c.negation));
    card.append(el("span", "wfp-inv-test", c.test || "no test named"));
    card.append(el("span", "wfp-inv-ev", c.evidence.join(" · ")));
    list.append(card);
  }
  box.append(list);
  return box;
}

let fullScreen = null;   // { dialog, opener } while a diagram is open full screen

// A drawn card, enlarged: a copy of its drawing over the page. Esc and a click outside the drawing close it.
function openFullScreen(card, opener) {
  closeFullScreen();
  const dialog = el("dialog", "wfp-full");
  const sheet = el("figure", "wfp-full-sheet");
  const cap = card.querySelector("figcaption").cloneNode(true);
  const close = el("button", "act wfp-full-close", "close · Esc");
  close.type = "button";
  close.addEventListener("click", closeFullScreen);
  const drawing = card.querySelector(".diagram").cloneNode(true);
  drawing.classList.remove("draw-in");
  for (const edge of drawing.querySelectorAll("[pathLength]")) edge.removeAttribute("pathLength");   // a copy of a card still drawing in
  sheet.append(cap, drawing, close);
  dialog.append(sheet);
  dialog.addEventListener("click", (e) => { if (!sheet.contains(e.target)) closeFullScreen(); });
  dialog.addEventListener("close", () => { if (fullScreen && fullScreen.dialog === dialog) closeFullScreen(); });   // Esc closes it natively
  document.body.append(dialog);
  fullScreen = { dialog, opener };
  dialog.showModal();
  zoomFrom(card, sheet);
}

// The sheet grows from the card's rectangle to its own place (ticket 130); nothing runs without motion.
function zoomFrom(card, sheet) {
  if (reducedMotion() || !canAnimate()) return;   // its own check: the zoom is WAAPI, which no `motionOf` key names
  const from = card.getBoundingClientRect();
  const to = sheet.getBoundingClientRect();
  if (!to.width || !to.height) return;
  const dx = from.left + from.width / 2 - (to.left + to.width / 2);
  const dy = from.top + from.height / 2 - (to.top + to.height / 2);
  sheet.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${from.width / to.width}, ${from.height / to.height})`, opacity: 0.4 },
    { transform: "none", opacity: 1 }], { duration: WfPanel.MOTION_MS.zoom, easing: "ease" });
}

function closeFullScreen() {
  if (!fullScreen) return;
  const { dialog, opener } = fullScreen;
  fullScreen = null;
  dialog.close();
  dialog.remove();
  if (opener.isConnected) opener.focus();
}

// The Texts block: each gate section and each document of the phase is a `<details>` row (title, source,
// length). A body is fetched on its first open. What the owner opened is `panel.openFolds`: a toggle updates
// it in place, and the stage is not drawn again for it (stageKeys leaves the open flag out).
function foldsBlock(s, token, draws) {
  const panel = state.panel;
  const box = el("section", "wfp-texts");
  if (!s.folds.length) return box;
  box.append(stageHeading("Texts", "folded · click to open"));
  const list = el("div", "wfp-folds");
  const opened = [];
  for (const f of s.folds) {
    if (f.state !== "present") {
      const row = el("div", "wfp-fold gone");
      row.append(el("b", "wfp-fold-t", f.title), el("span", "wfp-fold-src", f.source),
        el("div", "rowerr wfp-sec-miss", f.state === "missing" ? `The section ${f.title} is missing.` : `cannot read the section ${f.title}: ${f.tip}`));
      if (f.tip) row.title = f.tip;
      list.append(row);
      continue;
    }
    const fold = el("details", `wfp-fold ${f.kind}`);
    const head = el("summary", "wfp-fold-h");
    head.append(el("span", "wfp-fold-kind", f.kind === "section" ? "§" : f.kind), el("b", "wfp-fold-t", f.title),
      el("span", "wfp-fold-src", f.source));
    if (f.lines !== null) head.append(el("span", "wfp-fold-len", `${f.lines} line${f.lines === 1 ? "" : "s"}`));
    const body = el("div", "wfp-fold-body wfp-doc doc");
    fold.append(head, body);
    let filled = false;
    const fill = () => {
      if (filled) return;
      filled = true;
      return fillDocument({ id: f.id, kind: f.kind, name: f.title }, body, token);
    };
    fold.open = f.open;
    fold.addEventListener("toggle", () => {
      if (state.panel !== panel) return;
      const was = (panel.openFolds[s.n] || new Set()).has(f.id);
      if (fold.open === was) return;   // the stage's own opening of a fold the owner left open: its body is asked in sequence
      panel.openFolds = WfPanel.foldToggled(panel.openFolds, s.n, f.id);
      if (fold.open) fill();
    });
    if (f.open) opened.push(fill);
    list.append(fold);
  }
  // the folds the owner left open come back one after the other, as the sections did (gate-sections: one at a time)
  if (opened.length) draws.push(async () => { for (const fill of opened) await fill(); });
  box.append(list);
  return box;
}

function panelInputs(row) {
  const panel = state.panel;
  return { row, map: panel.map, failure: panel.failure, chosen: panel.chosen, openIds: panel.openIds, openFolds: panel.openFolds, now: new Date() };
}

function paintPanel() {
  const panel = state.panel;
  const row = panelRow();
  if (!panel || !row) return;
  const view = WfPanel.panelView(panelInputs(row));
  paintTop(view);
  paintHints(view);
  followDot(panel, view);
  paintStepper(view);
  paintBanner(view);
  const keys = WfPanel.stageKeys(view);
  if (keys.head !== panel.keys.head) {
    panel.keys.head = keys.head;
    paintHead(view);
  }
  if (keys.art !== panel.keys.art) {
    panel.keys.art = keys.art;
    paintArtefacts(view);
  }
}

async function loadPanelWorkflow() {
  const panel = state.panel;
  if (!panel) return;
  const mine = panel.token = ++tokens;
  const rowId = panel.rowId;
  try {
    const resp = await fetch(`/api/workflow?feature=${encodeURIComponent(rowId)}`, { cache: "no-store" });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    if (state.panel !== panel || panel.token !== mine) return;   // closed, replaced or asked again meanwhile
    panel.map = body;
    panel.failure = null;
  } catch (err) {
    if (state.panel !== panel || panel.token !== mine) return;
    panel.failure = err.message;   // the last map stays: the stage is not thrown away for a failed refresh
  }
  paintPanel();
}

// `how` says where the call comes from: "click" (the table: one history entry, the table's scroll saved),
// "move" (prev/next: the entry is replaced, Decision 3), "load" (the address of the first load) or "history"
// (the browser moved through its entries: nothing is written).
function openPanel(id, how = "click") {
  if (state.panel && state.panel.rowId === id) return;
  const row = [...state.data.open, ...state.data.history].find((r) => r.id === id);
  if (!row) return;
  const was = state.panel;
  const tableScroll = was ? was.tableScroll : how === "click" ? window.scrollY : null;   // hiding the table resets the scroll (P3)
  const pushed = was ? was.pushed : how === "click" || how === "history";   // the first load's entry has no table behind it
  if (!panelFrame) panelFrame = buildPanelFrame();
  state.panel = { rowId: id, slug: row.slug, chosen: null, openIds: {}, openFolds: {}, map: null, failure: null,
    token: 0, keys: { head: null, art: null }, artToken: 0, dot: null, stamped: null, tableScroll, pushed };
  state.marked = null;
  if (how === "click") history.pushState(null, "", WfPanel.addressOf(id));
  else if (how === "move") history.replaceState(null, "", WfPanel.addressOf(id));
  $("body").classList.add("has-panel");
  panelFrame.aside.hidden = false;
  if (was) window.scrollTo(0, 0);   // another row starts at its top
  markRow();
  paintPanel();
  loadPanelWorkflow();
  if (how !== "move") panelFrame.aside.focus();   // a move keeps the focus on the button that was pressed
}

// The ids of the rows the table shows, in its order: Open as it is, History after the filter (Decision 4).
function shownIds() {
  const d = state.data;
  if (!d) return [];
  const rows = state.tab === "open" ? d.open : state.tab === "history" ? d.history.filter((r) => matches(r, state.filter)) : [];
  return rows.map((r) => r.id);
}

// The row `step` places away in the table the view was opened from; false when there is none.
function moveRow(step) {
  if (!state.panel) return false;
  const next = WfPanel.neighbourRow(shownIds(), state.panel.rowId, step);
  if (next === null) return false;
  openPanel(next, "move");
  const at = document.activeElement;   // the pressed button is disabled at the end of the list: the other one takes the focus
  if (at && at.disabled) document.querySelector(at.classList.contains("wfp-row-next") ? ".wfp-row-prev" : ".wfp-row-next").focus();
  return true;
}

// The address without a row: a replaced entry, never a new one.
function clearAddress() {
  if (WfPanel.rowFromAddress(location.hash) !== null) history.replaceState(null, "", location.pathname + location.search);
}

// Back and Esc: the entry the click pushed is undone (the hashchange closes the view), so one step reaches
// the table however many rows were read; a view opened by the address of a load has no such entry.
function leaveView() {
  const panel = state.panel;
  if (!panel) return;
  if (panel.pushed) history.back();
  else closePanel();
}

function closePanel() {
  const panel = state.panel;
  if (!panel) return;
  closeFullScreen();
  panel.token = ++tokens;
  panel.artToken = ++tokens;
  state.panel = null;
  state.marked = panel.rowId;
  clearAddress();
  $("body").classList.remove("has-panel");
  $("wfpanel").hidden = true;
  $("wfpanel").replaceChildren();
  panelFrame = null;
  markRow();
  if (panel.tableScroll !== null) window.scrollTo(0, panel.tableScroll);   // where the owner was, not where focus() would go
  const here = document.activeElement;   // focus goes back to the row only when it was in the panel
  if (!here || here === document.body || here.closest("tr")) {
    const tr = [...document.querySelectorAll("tr[data-id]")].find((t) => t.dataset.id === panel.rowId);
    if (tr) tr.focus();   // the browser scrolls it into view only when it is off screen (a row read after moving)
  }
}

// The address of the page names the row to show: on a load, once the first snapshot lists the rows, and at each
// move of the browser through its entries (the back gesture). A row no snapshot lists gives the table and a notice.
function onAddressChange(how = "history") {
  const id = WfPanel.rowFromAddress(location.hash);
  if (id === null) {
    closePanel();
    return;
  }
  if (!state.data || (state.panel && state.panel.rowId === id)) return;
  const tab = WfPanel.tabOfRow(state.data, id);
  if (tab === null) {
    closePanel();
    clearAddress();
    notice(`The row ${id} named by the address is no longer in the table.`);
    return;
  }
  if (tab !== state.tab) showTab(tab);
  openPanel(id, how);
}

function choosePhase(n) {
  if (!state.panel) return;
  const row = panelRow();
  if (!row || !WfPanel.panelView(panelInputs(row)).chooses) return;   // no stage to show: keep following the dot
  state.panel.chosen = n;   // the owner has chosen: the panel stops following the dot
  paintPanel();
}

function showArtefact(id) {
  const panel = state.panel;
  const row = panelRow();
  if (!panel || !row) return;
  panel.openIds[WfPanel.followedPhase(panel.chosen, row)] = id;
  paintPanel();
}

function onPanelKey(e) {
  if (!state.panel) return;
  if (e.key === "Escape" && fullScreen) return;   // the full-screen dialog closes itself; the panel stays
  const t = e.target || {};
  const action = WfPanel.keyAction(e.key, { tag: t.tagName || "", editable: Boolean(t.isContentEditable) },
    { ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey });
  if (action === null) return;
  const row = panelRow();
  const at = row ? WfPanel.followedPhase(state.panel.chosen, row) : 0;
  let acted = true;
  if (action === "back" && at > 0) choosePhase(at - 1);
  else if (action === "next" && at < PHASES.length - 1) choosePhase(at + 1);
  else if (action === "refresh") refresh(true);
  else if (action === "close") leaveView();
  else if (action === "nextRow") acted = moveRow(1);
  else if (action === "prevRow") acted = moveRow(-1);
  else acted = false;   // Back at phase 0 or Next at phase 6: nothing happened, the key is left alone
  if (acted) e.preventDefault();
}

document.addEventListener("keydown", onPanelKey);
window.addEventListener("hashchange", () => onAddressChange("history"));

// --- the theme (#87): theme.js decided the first paint; here the window's switch (the only one, row-view) and what follows a change ---

// The click of the switch: the other theme, remembered when the browser lets us, applied at once.
function toggleTheme() {
  const next = WfTheme.nextTheme(currentTheme());
  WfTheme.saveTheme(WfTheme.storageOf(window), next);
  WfTheme.applyTheme(document, next);
}

// The switch shows the effective theme, however it was reached (a click, or the system).
function syncSwitches() {
  const dark = currentTheme() === "dark";
  for (const sw of document.querySelectorAll(".theme-switch")) {
    sw.setAttribute("aria-pressed", String(dark));
    sw.title = `Theme: ${dark ? "dark" : "light"}. Switch to ${dark ? "light" : "dark"}`;
  }
}

function onThemeChanged() {
  syncSwitches();
  mermaidTheme = null;   // Mermaid's colours are stale
  redrawDiagrams();
}

document.addEventListener("wf-theme", onThemeChanged);
$("theme-switch").addEventListener("click", toggleTheme);
syncSwitches();

$("tab-open").addEventListener("click", () => chooseTab("open"));
$("tab-history").addEventListener("click", () => chooseTab("history"));
$("tab-workflow").addEventListener("click", () => chooseTab("workflow"));
$("tab-documents").addEventListener("click", () => chooseTab("documents"));
$("filter").addEventListener("input", (e) => {
  if (state.tab === "documents") state.docFilter = e.target.value;
  else state.filter = e.target.value;
  paintView();   // the input stays; History redraws only the rows that enter or leave the list
});
render();
setTimeout(() => { if (state.data === null) render(); }, SLOW_MS);   // the slow message appears without waiting for a refresh; once the data are there nothing is drawn again for it (a 304 draws nothing, ticket 105)
refresh();
setInterval(age, 1000);
