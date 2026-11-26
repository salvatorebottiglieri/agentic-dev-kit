# app — the workflow desktop window

A local window that lists every feature across the local repos, built only from
what is on disk (worktrees, claims, specs, handoffs, presence records).
Read-only: it opens things, it never writes to a repo or a tracker.
A worktree joins the row of the feature the last `wf.py next <slug>` run in it named (else the feature whose spec `branch:` is its branch, else its directory name), and that row's phase is derived on that tree.


    python3 ~/agentic-dev-kit/app/desk.py serve [--port 8765]

Options: `--root DIR` (repeatable; the children of each are scanned for repos,
default `$HOME`) and `--workflow-dir DIR` (default: the parent of `app/`).
Exit codes: 0 on Ctrl-C or SIGTERM, 2 when the port is taken.

    python3 ~/agentic-dev-kit/app/desk.py open [--port 8765] [--edge PATH] [--state-dir DIR]
    python3 ~/agentic-dev-kit/app/desk.py stop [--port 8765] [--state-dir DIR]
    python3 ~/agentic-dev-kit/app/desk.py shortcut [--desktop DIR] [--port 8765]

`open` reuses a server that already answers on the port (this app's CSP),
otherwise starts `serve` detached (pid in `<state-dir>/desk-<port>.pid`, output
in `<state-dir>/desk.log`; state dir default `~/.local/state/agentic-dev-kit`),
waits up to 15 s, then launches `msedge.exe --app=http://localhost:<port>/`.
Exit 1: no answer in time; 2: the port is held by another program, or Edge is
missing. `stop` SIGTERMs only a pid whose cmdline is our `serve` on that port
(exit 1 when nothing of ours runs). `shortcut` writes `Workflow.lnk` on the
Windows desktop (target `wsl.exe -d $WSL_DISTRO_NAME -- python3 <this desk.py>
open`, minimized, Edge icon); run it once from the primary tree.

The page is at `http://localhost:8765/`: the Open and History tabs, refreshed
every 10 s from `GET /api/features`. Each row shows the agent working on it
(from the presence records `wf.py next/status` writes under
`$XDG_STATE_HOME/agentic-dev-kit/presence`) and the signals that follow from
existing rules (hover a pill for the rule). History filters on slug, repo or
date; handoffs with no `Repo` line are listed under "repo unknown". Click a row
(or press Enter on it) to open the workflow panel beside the table: the seven phases as a stepper with the
live phase marked, and for the chosen phase its verdict, counters, artefacts (read in the stage) and exit check
(`GET /api/workflow?feature=<id>`; keys: left/right arrows move between phases, R refreshes, Esc closes).
The stage of the design phases (1, 3, 5 and 6; phases 0, 2 and 4 stay as they were) draws, in phases 3 and 6, an **Invariants** block, then a **Diagrams** block, then the **texts** folded below them (phases 1 and 5 have no Invariants block). Invariants shows one card per law of the spec's `### System invariants` table (law, negation, test) with a status chip and an evidence line. The statuses are `pinned`, `blind`, `never mutated`, `test missing` and `not checkable`. Each is derived at every read from the mutation matrices in the report store (read only, cached per store change) joined on the row's worktree, or on the spec's own repo when the row has none: the newest matrix that targets the test in that tree decides (a blind test beats a pinned one), a test no matrix targets is `never mutated` if it exists and `test missing` if not, and a cell with no test is `not checkable`. The heading names the spec and the tree. The answer carries it as `phases[].invariants`. Diagrams lists every Mermaid block of the phase's documents in document order, each card captioned with its heading path and its source (`file § section`): phase 1 reads the spec's `## Model`, phase 3 its `## Model` and `## System Architecture`, phase 6 the as-built model (phase 5 has no hand-written block yet). A card holds a placeholder of its own size until Mermaid has drawn it, shows the parse error and the source when it cannot, and opens full screen on a click (or Enter); Esc or a click outside closes it. A phase with no diagram says "No diagram in this phase's documents." in one line. Below it each gate section and each document of the phase is a folded row (title, source, length in lines) that opens and closes on a click; the body is fetched on its first open and draws its diagrams in place. A fold the owner opened stays open across refreshes while they stay on that row, and closes when they open another row. The answer carries this as `phases[].diagrams` (`title`, `source`, `doc`, `generated`, `mermaid`) and `lines` on each section and artefact, in the same versioned (ETag/304) read-only answer.
Motion is short (180-280 ms, one soft easing, and no motion of the whole window bounces: the round buttons' press included) and feature-detected: table rows that move, appear or leave go through a view transition (none for a 304 or an unchanged list; `refresh()` waits for that update at most `UPDATE_WAIT_MS`, 500 ms, so a transition the browser holds back never stalls the poll), a fold opens and closes in height, a new phase cross-fades (the DOM is the new stage at once; a copy of the old one, in the shadow root of an inert overlay, fades out over it as the new one fades in), a diagram draws in on its first drawing and full screen zooms from its card. `prefers-reduced-motion`, or a browser without the API a motion needs, runs none of it and leaves the same page (`WfPanel.motionOf`, `WfPanel.MOTION_MS`).
`GET /api/features/{id}` (the traces, the Next session text of the newest handoff and the attributed reports,
answered from the last snapshot) is no longer shown as a detail pane: the row's **Spec** and **Handoff** buttons use it.
A refresh with nothing changed reuses the last phase of each tree; a restart starts empty.
A row whose files changed since its phase was derived is checked again in the background: it keeps its last known phase and its list (Open or History) with the word `refreshing` (a pulsing dot that `prefers-reduced-motion` stops; the word stays) until the new phase lands, and its panel says so. A row never derived yet stays Open as `computing…`. The page polls every second while any row is computing or refreshing.
The reports also come from the report store (`$AGENT_REPORT_STORE`, else `$XDG_STATE_HOME/agentic-dev-kit/reports.sqlite`): the phase is derived with it, its reports are listed beside the files (the store wins on a name) and open in the pane; the window only reads it and never creates it.
Only `GET` and `POST /api/open` are served (an unknown `GET` path answers 404, every other method 405), `Host` must be
`127.0.0.1:<port>` or `localhost:<port>`, and every response is `Cache-Control: no-store`.

## What is portable

Only the server and the page are needed to read the window; everything else is optional.

| Part | Needs | Without it |
|---|---|---|
| Server and page (`serve`, then any browser at `http://localhost:8765/`) | Python 3 and a browser | not optional |
| `open` and `shortcut` (the Edge app window, `Workflow.lnk`) | Windows with WSL, `msedge.exe`, `powershell.exe`, `wsl.exe` | start `serve` and open the URL yourself |
| The row's **Terminal** button | Windows Terminal (`wt.exe`) with `wsl.exe` | answers 503; use the copy button |
| `wf.py end` (reads the PRs and the Delivery tickets) and `wf.py post-spec` (posts the spec to the tickets); the window's PR link is built from `origin` and needs no `gh` | the `gh` CLI | `end` reports no PR or ticket state; `post-spec` stops with an error |
| Remote view (phone) | `--remote-host` and a forwarder such as `tailscale serve` | the window answers `localhost` only |
| Jira links | `--jira-url` | a Jira key in `tracker:` is not linked |
| Diagrams and markdown, offline | nothing: Mermaid and marked are vendored in `app/vendor/` | not needed |

## Themes, buttons, motion and the phone (#87)

The window has two themes, light (warm paper) and dark (graphite `#131413`). `app/static/theme.js` is a plain
blocking script in `<head>`, before the stylesheet (the CSP forbids the inline script that is the usual way), and
sets `data-theme` on `<html>` before the first paint: the stored choice when there is one, else the system's
`prefers-color-scheme` (followed live while nothing is stored). The switch `◐` is in the window's header and in the
panel's header (one state); it toggles light/dark, stores the choice in `localStorage["wf-theme"]` (per browser and
origin: the phone, another origin, keeps its own) and applies it at once; a browser that refuses storage still works
for the session. Nothing returns to "follow the system" except clearing the site data. Changing the theme with a panel
open changes colours only: the diagrams are redrawn in place.

`app/static/app.css` carries every colour in two token blocks (`:root` and `:root[data-theme="dark"]`); no rule writes a
colour of its own, except the sandbox frame of an authored mockup (`.mockup` keeps `white`; the mockup inside brings its
own colours, an approved exception). `tests/app/test_theme_contrast.py` measures the declared pairs (text 4.5:1, outlines and dots 3:1)
in both themes and fails on a colour literal (hex, rgb, hsl or a named colour) outside the blocks. Five values differ from the approved mockup so that
every text reaches 4.5:1 (design Decision 8). Mermaid takes the theme through `themeVariables` built from the tokens
(`WfTheme.mermaidVariables`); each diagram sits on a faint isometric grid and each link of a flowchart or state
diagram carries a travelling packet. Motion belongs to what is alive (the live dot, the progress line, the links, the
caret of a problem line, the pulsing dot of a refreshing row, the panel's slide-in, the stamp of a passed phase) and one `prefers-reduced-motion` block stops
all of it without losing anything: the live phase and the agent are words in the tags (and `refreshing` a word), the line stays lit.

At 600 px or less (the table already turns into cards there) the panel takes the whole screen: the stepper is a strip of
seven numbers (name and word stay in the accessible text), the Back pill replaces the ×, and nothing scrolls sideways.
From 601 to 900 px the panel sits under the table, one column.

## Actions

The window only opens things; it never writes to a repo, a claim or a tracker. On a row:
**Terminal** (a Windows Terminal in the row's worktree or primary tree: `wt.exe -w new wsl.exe -d $WSL_DISTRO_NAME --cd <path>`),
**Spec** and **Handoff** (the newest) open in the document pane, as does each document of the workflow panel
(a `.jsonl` report is a table, a `.json` one indented text); nothing starts VS Code. **PR** and **ticket** links, and `copy: wf.py next <slug>`. The buttons call `POST /api/open
{action: "terminal", id, token}`: the id is a row of the last snapshot,
the path is the server's own, and the request must carry the page's own `Origin`, the JSON content type
and the token the server embeds in the page it serves to a loopback `Host`. Answers: 204 started,
403 (host, remote host, origin or token), 404 unknown id, 415, 400, 422 action not valid for that id, 503 tool missing.
The page served for `--remote-host` has no token, so from the phone there are links and the copy button only.

`--jira-url URL` (on `serve`, `open` and `shortcut`) turns a Jira key in a spec's `tracker:` into
`<URL>/browse/<KEY>`; an `http(s)` tracker is linked as it is. The PR link is built from the repo's
`origin` (GitHub, or a Bitbucket Server `…/scm/<proj>/<repo>`) and the row's branch, without any
credential of the URL. After adding the flag, rewrite the shortcut once
(`desk.py shortcut --jira-url https://example.atlassian.net`) and stop the running server.

## Documents and diagrams

A feature's detail lists its documents by exact slug: the spec's `## Model`, `docs/models/<slug>.md`
and the `*.html` in `.scratch/product-review/<slug>/`.
The **Documents** tab lists, for a repo of the snapshot, every `*.md` of `docs/adr/` and `docs/models/`
and every mockup, filterable by name.
"open" renders the file in the window: markdown with `marked`,
each ```` ```mermaid ```` block drawn by Mermaid (strict mode); a block that does not parse shows its
source and the parse error. A mockup opens in a sandboxed frame. `GET /api/documents?repo=` lists a
repo (404 when it is not in the last snapshot), `GET /api/documents/{id}` serves a file (404 unless a
listing returned the id since the server started, 503 when the file cannot be read now); no request
carries a path. A row's spec, handoffs and reports are listed by its detail as documents of kinds
`spec`, `handoff` and `report` (reports as `application/x-ndjson` or `application/json`).
Documents are readable from `--remote-host` too.

The renderers are vendored in `app/vendor/` (the window works offline) and served only by the names
of `app/vendor/MANIFEST.md` (`name · version · source URL · SHA-256`, one row per file, checked by
`tests/app/test_vendor.py`): `mermaid.min.js` 12.3.0 and `marked.umd.js` 18.2.1, each with its
license. To update one: replace the file, edit its row (version, URL, hash). The page CSP admits
inline styles (Mermaid colours diagrams with `<style>`); scripts stay `'self'` only.

## The workflow map

The **Workflow** tab draws phases 0-6 from `wf.py` (`pipeline`, the bundle `wf.py next` prints,
the docstring of `check_phase<N>` as the exit check) and `gen-flow.py` (names, conditions, rules
with their level); nothing is copied, so a change to `WORKFLOW.md` shows at the next request.
Clicking a phase lists the files `wf.py next` prints for it (each opens in the document pane), its
exit check and the rules whose `Where` names it; the rest of the rules and the transversal files sit
in a strip under the map. With a feature chosen, its derived phase is marked HERE with its FAIL/TODO
lines, and skipped phases show their reason; the overlay is the last snapshot's verdicts, never a new
derivation. `GET /api/workflow[?feature=id]` serves it (404 for an id not in the last snapshot, 503
when the install cannot be read). Skills are read from `serve --skills-dir DIR` (default
`$AGENT_SKILLS_DIR`, else `~/.agents/skills`, as `wf.py`). Readable from `--remote-host` too.

## From the phone

The server still binds 127.0.0.1 only; `tailscale serve` forwards the tailnet to it. Once:

    tailscale serve --bg --https=8443 http://127.0.0.1:8765

Then start the server with the name the phone uses, in any of these ways:

    python3 ~/agentic-dev-kit/app/desk.py shortcut --remote-host desk.example.ts.net:8443
    python3 ~/agentic-dev-kit/app/desk.py open --remote-host desk.example.ts.net:8443
    python3 ~/agentic-dev-kit/app/desk.py serve --remote-host desk.example.ts.net:8443

(`shortcut` rewrites `Workflow.lnk` with the flag; an already-running server must be
stopped first, `open` reuses it.) Open `https://desk.example.ts.net:8443/` on the phone.
`--remote-host H` adds exactly that `Host` (name and port) to the accepted ones; without
the flag a request with that `Host` gets 403. Actions are read-only from the phone (no token in
that page, `POST /api/open` is 403 for that `Host`); the `Tailscale-User-Login` check is deferred.

Tests: `python3 -m pytest tests/app` (real temporary git repos and a real socket; the logic of `panel.js` and
`theme.js` runs through `node` and is skipped, saying why, where it is missing).

The browser smoke of the whole window (S1-S84: panel, themes, buttons, motion, phone, the design stage) is not part of pytest. From the
worktree, on ports of your own:

    python3 app/desk.py serve --port 8791 --state-dir "$(mktemp -d)" &          # note its PID
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --remote-debugging-port=9333 \
       --user-data-dir='C:\Windows\Temp\wfsmoke' about:blank &
    cp tests/app/smoke/panel_smoke.mjs /mnt/c/Users/<you>/AppData/Local/Temp/
    powershell.exe -NoProfile -Command "node 'C:\Users\<you>\AppData\Local\Temp\panel_smoke.mjs' http://localhost:8791/ 9333"

It prints `ok S<n> <name>` or `FAIL S<n> <name>: <why>` per check and exits 1 on any failure or page error. Stop the
server by its PID and the Edge by its `--user-data-dir` (never `pkill -f`).
