"use strict";
// The decisions of the workflow side panel (#86), which #113 turned into the view of a row, as pure functions of plain data: no DOM, no window,
// so `node` can run them (tests/app/test_panel.py). app.js paints what `panelView` returns.
// `Row` is a row of /api/features, `Map` the answer of /api/workflow?feature=, `n` a phase 0..6.
(function (root) {
  const PHASES = ["Start", "Design & alignment", "Product review", "System architecture",
    "Tickets", "Program design", "Implement-loop"];
  const TONES = { PASS: "pass", SKIP: "skip", FAIL: "fail", TODO: "todo" };
  const LAST = PHASES.length - 1;
  const DASH = "—";
  const HINTS = [{ keys: ["←", "→"], text: "phase" }, { keys: ["R"], text: "refresh" }, { keys: ["Esc"], text: "close" }];

  // The ticket button reads #N for a GitHub issue or pull URL (trailing slash, query or fragment
  // tolerated), the key as it is for a non-URL tracker such as EXAMPLE-5166, and "Ticket" for any
  // other URL.
  function ticketLabel(tracker) {
    if (!tracker) return "Ticket";
    const gh = /^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/(?:issues|pull)\/(\d+)\/?(?:[?#].*)?$/.exec(tracker);
    if (gh) return "#" + gh[1];
    return /^[a-z][a-z0-9+.-]*:\/\//i.test(tracker) ? "Ticket" : tracker;
  }

  const kindOf = (row) => row.phase.kind;

  // The phase the panel opens on: the derived one, 6 when delivered, 0 when there is nothing to point at.
  function opensOn(row) {
    if (kindOf(row) === "derived") return row.phase.phase;
    return kindOf(row) === "delivered" ? LAST : 0;
  }

  // The panel follows the dot until the owner chooses a phase (Decision 3).
  function followedPhase(chosen, row) {
    return chosen !== null && chosen !== undefined ? chosen : opensOn(row);
  }

  // The red dot: on the derived phase only; it pulses while an agent is active.
  function liveDot(row) {
    if (kindOf(row) !== "derived") return { phase: null, pulse: false };
    return { phase: row.phase.phase, pulse: row.agent !== null && row.agent !== undefined };
  }

  // The harness word of each phase. A delivered feature passes every phase, whatever the list holds.
  function stepWords(row) {
    const kind = kindOf(row);
    return PHASES.map((_, n) => {
      let word = null;
      if (kind === "delivered") word = "PASS";
      else if (kind === "derived") {
        const found = row.phase.verdicts.find((v) => v.phase === n);
        word = found ? String(found.status).toUpperCase() : null;
      }
      return { n, word, tone: word ? TONES[word] || "none" : "none" };
    });
  }

  const two = (n) => String(n).padStart(2, "0");
  const dayOf = (d) => `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;

  // HH:MM for a time today (local), YYYY-MM-DD otherwise, a dash when there is none.
  function activityLabel(iso, now) {
    if (typeof iso !== "string") return DASH;
    const d = new Date(iso);
    if (isNaN(d)) return DASH;
    return dayOf(d) === dayOf(now) ? `${two(d.getHours())}:${two(d.getMinutes())}` : dayOf(d);
  }

  // The cards of a phase: its documents in the server's order, then the row's links (the ticket in
  // phase 4, the PR in phase 6). A link with no URL is not a card.
  function phaseArtefacts(phase, row) {
    const cards = ((phase && phase.artefacts) || []).map((a) =>
      ({ kind: "doc", id: a.id, name: a.name, docKind: a.kind, lines: a.lines }));
    const links = row.links || {};
    if (phase && phase.n === 4 && links.ticket) {
      cards.push({ kind: "link", id: null, name: ticketLabel(links.tracker), docKind: null, href: links.ticket });
    }
    if (phase && phase.n === LAST && links.pr) {
      cards.push({ kind: "link", id: null, name: "PR", docKind: null, href: links.pr });
    }
    return cards;
  }

  // --- #100 (gate-sections): the sections a gate contracts, shown above the cards of a phase.

  // A heading as a title: the name without the markdown markers.
  const sectionTitle = (heading) => String(heading).replace(/^\s*#{1,6}\s*/, "").trim();

  // The exit-check sentence as the owner reads it: the markers of the headings it quotes are dropped
  // (a run of # and spaces at the start or after whitespace, a back-tick or a parenthesis); `#100` stays.
  const exitSentence = (text) => String(text).replace(/(^|[\s`(])#{1,6}[ \t]+(?=\S)/g, "$1");

  // The entries of `phase.sections` as the stage draws them. A missing section is shown only when a problem
  // of the verdict names its `## heading` (the gate's own line is then its tip); one the gate is silent about
  // is not. An unreadable one carries the read error as its tip.
  function phaseSections(phase, problems) {
    const shown = [];
    for (const s of (phase && phase.sections) || []) {
      const title = sectionTitle(s.heading);
      const entry = { title, source: s.source, state: s.state, id: s.id, rev: s.rev, tip: null };
      if (s.state === "missing") {
        const heading = new RegExp(`## ${title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}\\p{N}_])`, "iu");
        const named = problems.find((p) => heading.test(String(p)));
        if (named === undefined) continue;
        entry.tip = named;
      } else if (s.state === "unreadable") {
        entry.tip = s.note;
      }
      shown.push(entry);
    }
    return shown;
  }

  // --- #127 (design-diagrams): the stage of the design phases draws their diagrams, then folds the texts.

  // The phases whose stage is the design layout; the others keep the plain one (cards, one open document).
  const DESIGN_PHASES = [1, 3, 5, 6];

  // `openFolds` (a Set of fold ids per phase) with `id` toggled in phase `n`; the input is not touched.
  function foldToggled(openFolds, n, id) {
    const now = new Set(openFolds[n] || []);
    if (now.has(id)) now.delete(id); else now.add(id);
    return { ...openFolds, [n]: now };
  }

  // --- ticket 130 (design-diagrams): motion. Pure: the page reads the browser into `env` and draws what this says.

  // The one source of the durations, each within the spec's 180-280 ms: app.js writes them as `--ms-<name>` for the
  // stylesheet and reads `zoom` itself. The easing (the keyword `ease`, soft, no overshoot) is named by the stylesheet's
  // `--ease` and by app.js, not here.
  const MOTION_MS = { rows: 240, fold: 220, stage: 200, draw: 280, zoom: 220 };

  // Which motions run, by the four switches `rows`, `fold`, `stage` and `draw`. Reduced motion silences all four;
  // rows need View Transitions, folds need interpolate-size. The stage's cross-fade also needs View Transitions and
  // the diagram's draw-in is CSS, which the page guards where it uses them. The full-screen zoom (the fifth duration,
  // `zoom`) has no switch of its own: it is WAAPI, which app.js detects, and reduced motion silences it.
  function motionOf(env) {
    if (env.reduced) return { rows: "none", fold: "none", stage: "none", draw: "none" };
    return { rows: env.viewTransitions ? "transition" : "none", fold: env.interpolateSize ? "height" : "none",
      stage: "fade", draw: "in" };
  }

  // The diagrams of a phase as the stage draws them, each with the key that tells a redraw from a refresh.
  const stageDiagrams = (phase) => ((phase && phase.diagrams) || []).map((d) =>
    ({ key: `${d.doc || d.generated || ""}:${d.title}:${d.mermaid}`, title: d.title,
      source: d.source, doc: d.doc, generated: d.generated, mermaid: d.mermaid }));

  // The folded texts of a design phase: the gate sections, then the documents, each with its length.
  // A section the gate has no body for (missing, unreadable) is a row that cannot open, with its tip.
  function stageFolds(phase, sections, docs, openFolds, n) {
    const opened = new Set((openFolds && openFolds[n]) || []);
    const lengths = new Map(((phase && phase.sections) || []).map((s) => [sectionTitle(s.heading), s.lines]));
    const fold = (f) => ({ ...f, open: f.state === "present" && opened.has(f.id) });
    const shortName = (name) => String(name).split("/").slice(-3).join("/");
    return [
      ...sections.map((x) => fold({ id: x.id || `sec:${x.title}`, title: x.title, source: x.source, kind: "section",
        lines: lengths.get(x.title) ?? null, state: x.state, tip: x.tip })),
      ...docs.map((c) => fold({ id: c.id, title: shortName(c.name).split("/").pop(), source: shortName(c.name),
        kind: c.docKind, lines: c.lines ?? null, state: "present", tip: null }))];
  }

  // --- ticket 129 (design-diagrams): the Invariants block of phases 3 and 6, one card per law with its live status.

  // The phases whose stage opens on the invariants of the spec.
  const INVARIANT_PHASES = [3, 6];

  // The five statuses in the fixed order of the heading, each with the words the owner reads.
  const STATUSES = [["pinned", "pinned"], ["blind", "blind"], ["never-mutated", "never mutated"],
    ["test-missing", "test missing"], ["not-checkable", "not checkable"]];
  const STATUS_WORD = Object.fromEntries(STATUSES);

  // What a test says about a status when no matrix counts for it: the fact, in words.
  const NO_MATRIX = { "never-mutated": "the test exists, no matrix row targets it", "test-missing": "no def in the file today" };

  // The Invariants section as data: a heading with the total, the count of each status present (in `STATUSES`
  // order), one card per law in the spec's order, and for a table the page could not read, its text and the reason.
  // `matrix mutant outcome` is the evidence line of a status that comes from a matrix. null for no table.
  // A spec file that could not be opened has no raw text (the wire sends ""): its view has the reason only.
  function invariantView(inv) {
    if (!inv) return null;
    const source = `${inv.source} § System invariants`;
    if (!inv.readable) return { heading: "Invariants", counts: [], cards: [], raw: inv.raw || null, why: inv.why, source };
    const evidence = (row) => {
      const lines = row.evidence.filter((e) => e.matrix !== null).map((e) => `${e.matrix} ${e.mutant} ${e.outcome}`);
      if (lines.length) return lines;
      return [NO_MATRIX[row.status] || "names no test: shown as written"];
    };
    return {
      heading: `Invariants · ${inv.rows.length}`,
      counts: STATUSES.map(([status, label]) => ({ status, label, n: inv.rows.filter((r) => r.status === status).length }))
        .filter((c) => c.n > 0),
      cards: inv.rows.map((r) => ({ n: r.n, law: r.law, negation: r.negation, test: r.verified, status: r.status,
        word: STATUS_WORD[r.status], evidence: evidence(r) })),
      raw: null, why: inv.why || null, tree: inv.tree ? `status from ${inv.tree}` : null, source,
    };
  }

  const ERROR_TEXT = (why) => `Cannot load the workflow: ${why}. Trying again at the next refresh.`;

  const REFRESHING_TEXT = "Checking this phase again. It shows the last known phase until the check ends.";

  function banner(row, mode, failure) {
    if (mode === "computing") return { tone: "info", text: "Checking the phases. This panel fills in by itself." };
    if (mode === "no-spec") {
      return { tone: "info", text: `${row.slug} has no spec: there is no workflow to lay on the phases.` };
    }
    if (failure) return { tone: "bad", text: ERROR_TEXT(failure) };
    if (mode === "loading") return { tone: "info", text: "Loading the workflow…" };
    if ((mode === "ready" || mode === "skipped") && row.phase.refreshing) return { tone: "info", text: REFRESHING_TEXT };
    return null;
  }

  // #111: a row whose phase is checked again shows its last known phase; the page keeps polling
  // fast while any row of either list is computing or refreshing.
  function pollFast(snapshot) {
    if (!snapshot) return false;
    return [...snapshot.open, ...snapshot.history].some((r) => r.phase.kind === "computing" || r.phase.refreshing === true);
  }

  function stageOf(n, row, map, words, dot, openIds, openFolds, now, name) {
    const phase = map.phases.find((p) => p.n === n);
    const word = words[n].word;
    const verdict = kindOf(row) === "derived" ? row.phase.verdicts.find((v) => v.phase === n) : null;
    const skipped = word === "SKIP";
    const all = skipped ? [] : phaseArtefacts(phase, row);
    const docs = all.filter((c) => c.kind === "doc");
    const design = DESIGN_PHASES.includes(n);
    const cards = design ? all.filter((c) => c.kind === "link") : all;
    const problems = verdict && !skipped && word !== "PASS" ? verdict.problems.slice() : [];
    const sections = skipped ? [] : phaseSections(phase, problems);
    const asked = openIds ? openIds[n] : undefined;
    // with sections the whole document opens only on a click; without, the first one opens by itself
    // (a design phase has no open document: its texts are folds)
    const open = design ? null : docs.find((c) => c.id === asked) || (sections.length ? null : docs[0] || null);
    const folds = design && !skipped ? stageFolds(phase, sections, docs, openFolds, n) : [];
    const diagrams = design && !skipped ? stageDiagrams(phase) : [];
    const invariantsHere = design && !skipped && INVARIANT_PHASES.includes(n);
    const invariants = invariantsHere ? invariantView(phase && phase.invariants) : null;
    const delivered = kindOf(row) === "delivered";
    const tags = [];
    if (word) tags.push(word);
    if (delivered) tags.push("delivered");
    if (dot.phase === n) tags.push("● live phase");
    if (dot.phase === n && dot.pulse) tags.push("agent active");
    let nothing = null;
    if (skipped) nothing = "Nothing to read: the phase was skipped.";
    else if (all.length === 0) nothing = "Nothing to read here yet.";
    else if (docs.length === 0) nothing = "Nothing to read here: only links.";
    let idea = { heading: `what leaves phase ${n}`, text: phase ? exitSentence(phase.exit_check) : "", problems, reason: null };
    if (skipped) {
      const reason = verdict ? verdict.reason : null;
      idea = { heading: "why it was skipped", text: reason || "No reason was recorded.", problems: [], reason: reason || null };
    } else if (delivered && n === LAST) {
      idea.heading = "delivered";
    }
    let counted = null;
    if (word === "PASS") counted = 0;
    else if (word === "TODO" || word === "FAIL") counted = problems.length;
    const activity = activityLabel(row.last_activity, now);
    // The three counters of the stage, labelled as Decision 4 signs them; none on a skipped phase.
    const stats = skipped ? [] : [
      { label: "artefacts", value: String(all.length), bad: false },
      { label: "open problems", value: counted === null ? DASH : String(counted), bad: counted !== null && counted > 0 },
      { label: "feature last activity", value: activity, bad: false }];
    return {
      n, title: name(n), word, tags, stats,
      crumb: `${row.slug} / phase ${n} of ${PHASES.length}`,
      lede: (phase && phase.condition) || "",
      counters: { artefacts: all.length, problems: counted, activity },
      layout: design ? "design" : "plain", diagrams, folds, invariants,
      // two absences, told apart from the phase's own documents (the wire is null for both)
      noInvariants: invariantsHere && !invariants
        ? (docs.some((c) => c.docKind === "spec") ? "The spec has no § System invariants." : "No spec among this phase's documents.")
        : null,
      // the blocks of a design stage, in the order they are drawn (invariants first where the phase has them)
      blocks: design ? [...(invariantsHere ? ["invariants"] : []), "diagrams", "folds"] : [],
      cards: cards.map((c) => ({ ...c, open: c.kind === "doc" && open !== null && c.id === open.id,
        href: c.kind === "link" ? c.href : null })),
      sections, openId: open ? open.id : null, nothing, idea,
      back: n > 0 ? { n: n - 1, name: name(n - 1) } : null,
      next: n < LAST ? { n: n + 1, name: name(n + 1) } : null,
    };
  }

  // The progress line and the stamp are data, not DOM guesses (#87). Each step owns the segment above it:
  // `lit` fills it, `flows` animates it toward the live step. The line is lit up to the live phase (to phase 6
  // for a delivered row), flows only on the live step's segment and only while an agent is active, and is absent
  // while the row is computing or has no spec. Phase 0 has nothing above it.
  function trackOf(steps, row) {
    const kind = kindOf(row);
    const upTo = kind === "derived" ? row.phase.phase : kind === "delivered" ? LAST : -1;
    const agent = row.agent !== null && row.agent !== undefined;
    return steps.map((s) => ({ ...s, lit: s.n >= 1 && s.n <= upTo,
      flows: kind === "derived" && agent && s.n >= 1 && s.n === upTo }));
  }

  // The phase to stamp: `prev` when the dot moved forward past a phase that passed (`words[prev].word` is PASS),
  // else null. `words` is a list indexed by phase of objects with a `word` (the view's steps).
  function dotMove(prev, next, words) {
    if (typeof prev !== "number" || typeof next !== "number" || next <= prev) return null;
    const at = words[prev];
    return at && at.word === "PASS" ? prev : null;
  }

  // The whole panel as data. Precedence: (1) the row says computing or no spec; (2) no map yet is
  // an error when a request failed, else loading; (3) a skipped phase; (4) ready.
  function panelView({ row, map, failure, chosen, openIds, openFolds, now }) {
    const kind = kindOf(row);
    const words = stepWords(row);
    const dot = liveDot(row);
    const at = followedPhase(chosen, row);
    const name = (n) => (map && map.phases[n] ? map.phases[n].name : PHASES[n]);
    let mode;
    if (kind === "computing" || kind === "no-spec") mode = kind;
    else if (!map) mode = failure ? "error" : "loading";
    else mode = words[at].word === "SKIP" ? "skipped" : "ready";
    const bare = mode === "computing" || mode === "no-spec";
    return {
      mode, banner: banner(row, mode, failure), slug: row.slug, agent: dot.pulse, hints: HINTS,
      steps: trackOf(PHASES.map((_, n) => ({ n, name: name(n), word: words[n].word, tone: words[n].tone,
        live: dot.phase === n, current: !bare && n === at })), row),
      chosen: at,
      chooses: mode === "ready" || mode === "skipped",   // a phase can be chosen only where there is a stage to show
      stage: mode === "ready" || mode === "skipped" ? stageOf(at, row, map, words, dot, openIds, openFolds, now, name) : null,
    };
  }

  // What the two regions of the stage were drawn from. The artefact key leaves out the verdict, the
  // counters and the activity, so a refresh that moves only those does not redraw the open document.
  function stageKeys(view) {
    const s = view.stage;
    if (!s) return { head: `-|${view.mode}`, art: `-|${view.mode}` };
    const head = [s.n, s.word, s.tags.join(","), s.counters.artefacts, s.counters.problems, s.counters.activity,
      s.crumb, s.lede, s.idea.heading, s.idea.text, s.idea.reason, s.idea.problems.join("\n"),
      s.back ? s.back.n + s.back.name : "", s.next ? s.next.n + s.next.name : ""].join("|");
    const art = [s.n, s.cards.map((c) => `${c.kind}:${c.id || c.href}:${c.name}`).join(","), s.openId, view.mode,
      s.sections.map((x) => `${x.id}:${x.state}:${x.rev}:${x.title}:${x.tip}`).join(",")];
    // a design stage also draws by its diagrams' sources and by the ids, states and lengths of its texts; a text being expanded is no reason to redraw
    if (s.layout === "design") {
      art.push(s.diagrams.map((d) => d.key).join(","), s.folds.map((f) => `${f.id}:${f.state}:${f.lines}:${f.tip}`).join(","),
        JSON.stringify(s.invariants), s.noInvariants);
    }
    return { head, art: art.join("|") };
  }

  // What a key does while the panel is open; `null` leaves the event alone.
  function keyAction(key, target, mods) {
    if (mods.ctrl || mods.alt || mods.meta) return null;
    if (key === "Escape") return "close";
    if (target.editable || ["input", "textarea", "select"].includes(String(target.tag).toLowerCase())) return null;
    if (key === "ArrowLeft") return "back";
    if (key === "ArrowRight") return "next";
    if (key === "r" || key === "R") return "refresh";
    if (key === "j") return "nextRow";
    if (key === "k") return "prevRow";
    return null;
  }

  // A panel for a feature the table no longer lists closes.
  function panelAfterSnapshot(rowId, ids) {
    return new Set(ids).has(rowId) ? "keep" : "close";
  }

  // "362 ahead of develop": the commits still to integrate and the branch they are counted against (#91).
  function aheadLabel(where) {
    return `${where.ahead} ahead of ${String(where.base).replace(/^origin\//, "")}`;
  }

  // The reason of `base unknown`, shown under its pill (#95); "" for any other signal or an old server.
  function baseNoteLine(signal, where) {
    return signal.name === "base-unknown" && typeof where.base_note === "string" ? where.base_note : "";
  }

  // The worktrees that are no feature (#93); a server older than the key sends none.
  function otherRows(data) {
    return Array.isArray(data.other_worktrees) ? data.other_worktrees : [];
  }

  function otherGroupLabel(n) {
    return `Other worktrees (${n})`;
  }

  // --- #105 (window-updates): what the page decides about a conditional poll and about the rows it keeps.

  // The header that asks "has it changed since this version": none until the page holds a tag.
  function conditionalHeaders(etag) {
    return etag ? { "If-None-Match": etag } : {};
  }

  // A 304 is "unchanged" only when the page holds a view to keep; without one it cannot be an answer
  // to a request the page sent with a tag, so it is a failure. 200 is a new content.
  function answerKind(status, haveView) {
    if (status === 304 && haveView) return "unchanged";
    return status === 200 ? "changed" : "failed";
  }

  // The fingerprint of a row as the server sent it, whole: no hand-picked "drawn fields" to go stale.
  // The selection is the page's state, not the row's, so it is not in it.
  function rowPrint(row) {
    return JSON.stringify(row);
  }

  // The indexes of one longest strictly increasing run of `seq` (patience sorting).
  function longestIncreasing(seq) {
    const tails = [];
    const before = new Array(seq.length).fill(-1);
    seq.forEach((value, i) => {
      let lo = 0;
      let hi = tails.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (seq[tails[mid]] < value) lo = mid + 1; else hi = mid;
      }
      before[i] = lo > 0 ? tails[lo - 1] : -1;
      tails[lo] = i;
    });
    const run = new Set();
    for (let i = tails.length ? tails[tails.length - 1] : -1; i !== -1; i = before[i]) run.add(i);
    return run;
  }

  // What the DOM must do to turn the rows on screen (`prev`) into the new ones (`next`), both lists of
  // [id, print] in display order: remove the ids that left, draw the new or changed ones, and move the
  // fewest unchanged survivors for `order` to hold (those outside a longest increasing run of old places).
  function planRows(prev, next) {
    const old = new Map(prev.map(([id, print], place) => [id, { print, place }]));
    const now = new Set(next.map(([id]) => id));
    const draw = next.filter(([id, print]) => !old.has(id) || old.get(id).print !== print).map(([id]) => id);
    const drawn = new Set(draw);
    const kept = next.map(([id]) => id).filter((id) => old.has(id) && !drawn.has(id));
    const stay = longestIncreasing(kept.map((id) => old.get(id).place));
    return {
      remove: prev.map(([id]) => id).filter((id) => !now.has(id)),
      draw,
      move: kept.filter((_, i) => !stay.has(i)),
      order: next.map(([id]) => id),
    };
  }

  // The footer's draw figure: a 304 draws nothing, so it says so and keeps the last measured value.
  function drawFigure(kind, drawMs, lastMs) {
    return kind === "unchanged" ? { word: "unchanged", ms: lastMs } : { word: null, ms: drawMs };
  }

  // A tab is loaded whole the first time or when forced, revalidated while it is shown, and left alone
  // while it is not (the next visit revalidates).
  function tabLoadMode({ loaded, shown, force }) {
    if (!loaded || force) return "full";
    return shown ? "revalidate" : "none";
  }

  // --- #113 (row-view): the address of the open row and the rows around it.

  // The address that names a row; none for no row.
  const addressOf = (rowId) => (rowId === null || rowId === undefined ? "" : `#row=${rowId}`);

  // The row an address names: `#row=` and the 12 hex digits of a row id; null for any other hash.
  function rowFromAddress(hash) {
    const found = /^#row=([0-9a-f]{12})$/.exec(String(hash));
    return found ? found[1] : null;
  }

  // The id `step` places from `rowId` in `ids`; null past either end and for an id that is not listed
  // (Decision 2: no wrap-around).
  function neighbourRow(ids, rowId, step) {
    const at = ids.indexOf(rowId);
    if (at === -1) return null;
    return ids[at + step] === undefined ? null : ids[at + step];
  }

  // The tab that lists a row: Open before History, none when neither does.
  function tabOfRow(data, rowId) {
    if (data.open.some((r) => r.id === rowId)) return "open";
    return data.history.some((r) => r.id === rowId) ? "history" : null;
  }

  const api = { DESIGN_PHASES, MOTION_MS, motionOf, foldToggled, invariantView, addressOf, rowFromAddress, neighbourRow, tabOfRow, sectionTitle, exitSentence, phaseSections, conditionalHeaders, answerKind, rowPrint, planRows, drawFigure, tabLoadMode,
    otherRows, otherGroupLabel, aheadLabel, baseNoteLine, PHASES, TONES, ticketLabel, opensOn, followedPhase, liveDot, stepWords, activityLabel,
    phaseArtefacts, panelView, stageKeys, keyAction, panelAfterSnapshot, trackOf, dotMove, pollFast };
  root.WfPanel = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
