#!/usr/bin/env python3
"""gen-flow — generates the workflow map from the sources, and checks that nothing is missing.

    python3 gen-flow.py            # writes FLOW.md and prints the summary
    python3 gen-flow.py --check    # writes nothing: exits != 0 if anything does not add up

Why generated and not drawn by hand: a hand-drawn map is a second copy of the
facts, and the second copy drifts. Here every node comes from the files
(`WORKFLOW.md`, `phases/*.md`, `INVARIANTS.md`, `REVIEW-CRITERIA.md`,
`audit.py`), and **coverage is a check**: if an extracted element does not
appear in `FLOW.md`, the script exits != 0.

The same check is the root of the levels rule: **every load-bearing rule
declares the level at which it is enforced** (structure / script / prompt).
A row without a level cannot be drawn, so it is not allowed.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
WORKFLOW = HERE / "WORKFLOW.md"
INVARIANTS = HERE / "INVARIANTS.md"
CRITERIA = HERE / "REVIEW-CRITERIA.md"
AUDIT = HERE / "audit.py"
OUT = HERE / "FLOW.md"
RENDER = HERE / "render-flow.html"

LEVELS = ("structure", "script", "prompt")
LEVEL_STYLE = {
    "structure": "#eef3ff|#0b5cad",
    "script": "#eaf7ee|#1a7f37",
    "prompt": "#fff8e6|#8a5a00",
}


def section(text: str, title: str) -> str:
    """The text between `## <title>` and the next `##` at the same level."""
    hit = re.search(rf"^##+ {re.escape(title)}\s*$", text, re.M)
    if not hit:
        return ""
    rest = text[hit.end():]
    return re.split(r"^##+ ", rest, flags=re.M)[0]


def table_rows(chunk: str) -> list[list[str]]:
    rows = []
    for line in chunk.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def labelled_table(text: str, label: str) -> str:
    """The table introduced by a **plain text line** `Label:` (not a heading).
    In `WORKFLOW.md` the transversal files are announced this way; `section()`
    only looks for `## <title>` and found nothing on that line, so the map
    counted zero of them and declared 'coverage: complete'."""
    hit = re.search(rf"^{re.escape(label)}:\s*$", text, re.M)
    if not hit:
        return ""
    out = []
    for line in text[hit.end():].splitlines():
        if line.startswith("|"):
            out.append(line)
        elif out or line.strip():
            break
    return "\n".join(out)


def level_of(cell: str) -> str:
    """The level declared in the 'Enforced by' column."""
    low = cell.lower()
    if "structure" in low:
        return "structure"
    if "script" in low:
        return "script"
    if "prompt" in low:
        return "prompt"
    return ""


def backticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def tables(chunk: str) -> list[list[list[str]]]:
    """The tables of a block, in order: a block can hold more than one (the
    transversal files follow the pipeline table), and taking 'every line with
    a |' would mix two different extractions."""
    out, cur = [], []
    for line in chunk.splitlines():
        if line.startswith("|"):
            cur.append(line)
        elif cur:
            out.append(table_rows("\n".join(cur)))
            cur = []
    if cur:
        out.append(table_rows("\n".join(cur)))
    return out


def first_table(chunk: str) -> list[list[str]]:
    t = tables(chunk)
    return t[0] if t else []


def count_rows(misses: list[str], what: str, in_source: int, in_map: int) -> None:
    """The independent count: the source has N rows, the map draws M.

    If N == 0 the anchor produced nothing (heading or label renamed, or a parse
    that no longer matches); if N != M the map lost rows along the way. The
    **partial** loss is the most insidious: no error, just a lower number that
    the old check compared with itself and called 'coverage: complete'."""
    if in_source == 0:
        misses.append(f"{what}: the anchor produced no row "
                     f"(heading or label renamed?)")
    elif in_source != in_map:
        misses.append(f"{what}: the source has {in_source} rows, the map has {in_map}")


def build_model() -> dict:
    wf = WORKFLOW.read_text(encoding="utf-8")
    inv = INVARIANTS.read_text(encoding="utf-8")
    crit = CRITERIA.read_text(encoding="utf-8")
    audit = AUDIT.read_text(encoding="utf-8")
    # Anchors and counts: collected here, where the extraction happens, and
    # reported among the misses in `emit`. Coverage is not 'the model has no
    # dangling references' but 'the model has everything the sources contain'.
    misses: list[str] = []

    rows = first_table(section(wf, "Pipeline"))
    rows = [c for c in rows if not c[0].lower().startswith("#")]
    phases = []
    for cells in rows:
        if not cells[0].isdigit():
            continue
        path = (backticks(cells[3]) or [""])[0]
        phases.append({
            "n": cells[0], "name": cells[1], "skills": backticks(cells[2]),
            "rules": path or "inline in `WORKFLOW.md`",
            "inline": not path,
            "exists": (HERE / path).exists() if path else True,
        })
    count_rows(misses, "phases (`## Pipeline`)", len(rows), len(phases))

    rows = first_table(labelled_table(wf, "Transversal files"))
    rows = [c for c in rows if c[0].lower() != "file"]
    transversal = []
    for cells in rows:
        when = cells[1]
        # Where to attach the file in the drawing: the 'Read when' line names
        # the phase ('phase 6') or the moment ('session end'). If it names
        # neither, the map does not invent one: it declares it among the misses.
        phase_match = re.search(r"phase\s+(\d+)", when)
        target = f"P{phase_match.group(1)}" if phase_match else ("SE" if "session end" in when else "")
        transversal.append({"file": (backticks(cells[0]) or [cells[0]])[0],
                            "when": when, "target": target})
    count_rows(misses, "transversal files (`Transversal files:`)", len(rows), len(transversal))

    rows = first_table(section(wf, "Where each rule is enforced"))
    rows = [c for c in rows if c[0].lower() != "rule"]
    rules = [{"rule": c[0], "where": c[1], "level": level_of(c[2]), "by": c[2]}
             for c in rows]
    count_rows(misses, "load-bearing rules (`## Where each rule is enforced`)", len(rows), len(rules))

    headings = re.findall(r"^##+ (I\d+) — (.+)$", inv, re.M)
    invs = [{"id": i, "title": re.sub(r"\s*—.*$", "", t).strip()}
            for i, t in headings if "REMOVED" not in t]
    removed = sum(1 for _, t in headings if "REMOVED" in t)
    count_rows(misses, "invariants (`## I<n> — ...`)", len(headings) - removed, len(invs))

    checks = re.findall(r"^def (check_\w+)\(", audit, re.M)
    if not checks:
        misses.append("checks (`def check_*` in `audit.py`): no function found "
                     "(convention renamed?)")
    block = section(wf, "General rules")
    items = re.findall(r"^- (.+?)(?=\n- |\n\n|\Z)", block, re.M | re.S)
    general = [re.sub(r"\s+", " ", b).strip("*- ") for b in items]
    general = [g for g in general if g]
    count_rows(misses, "general rules (`## General rules`)", len(items), len(general))

    axes = [a for a in ("Standards", "Spec", "Ponytail") if re.search(a, crit, re.I)]

    return {"phases": phases, "transversal": transversal, "rules": rules,
            "invariants": invs, "checks": checks, "general": general, "axes": axes,
            "misses": misses}


def skippable(m: dict) -> list[dict]:
    """The skippable phases, derived from the name in the pipeline table: that
    is where the workflow declares the condition ('only when ...', 'near-always')."""
    hints = ("only when", "near-always")
    return [p for p in m["phases"] if any(h in p["name"].lower() for h in hints)]


def name_and_condition(name: str) -> tuple[str, str]:
    """'Product review (only when the feature touches UI)' ->
    ('Product review', 'only when the feature touches UI').

    The condition sits in parentheses in the phase name: that is where the
    workflow declares it, so the decision node takes it from there (one copy),
    and the phase rectangle goes without it."""
    hit = re.match(r"^(.*?)\s*\((.+)\)\s*$", name)
    return (hit.group(1), hit.group(2)) if hit else (name, "")


def mermaid_pipeline(m: dict) -> str:
    """The pipeline with flow-diagram semantics: a conditional point is a
    **decision diamond with labelled exits**, not a dashed arrow running next
    to the chain. Before, two edges left the same phase (a solid 'P1 -> P2' and
    a dashed 'P1 -> P3') without the node that decides between them: the
    drawing asserted an order without stating the condition. Now every
    skippable phase has its own diamond, the 'yes' exit enters the phase and
    the 'no' exit goes to the next phase naming the annotation (I3).

    Visual hierarchy: the primary node is the **phase** (rectangle, three lines
    — name, condition, skills), the diamond is secondary and short ('phase N?').
    The direction matters: a diamond bigger than the phase it decides
    overshadows the one thing the reader has to remember. The only dashed line
    left is the dependencies one (transversal files read), which is not a
    control edge."""
    phases = m["phases"]
    if not phases:
        return ("```mermaid\nflowchart TD\n"
                '  EMPTY["no phase extracted: see the Misses at the end of the file"]\n```')
    skippable_ns = {p["n"] for p in skippable(m)}

    def entry(i: int) -> str:
        """Where phase i is entered: the diamond if it is skippable, the phase
        itself otherwise. Every branch (phase run, phase skipped) points here."""
        p = phases[i]
        return f'Q{p["n"]}' if p["n"] in skippable_ns else f'P{p["n"]}'

    lines = ["```mermaid", "flowchart TD"]
    for p in phases:
        name, cond = name_and_condition(p["name"])
        rows = [f'{p["n"]} · {name}']
        if cond:
            rows.append(cond)
        rows.append(", ".join(p["skills"]))
        lines.append(f'  P{p["n"]}["' + "<br/>".join(rows) + '"]')
        if p["n"] in skippable_ns:
            lines.append(f'  Q{p["n"]}{{"phase {p["n"]}?"}}')

    previous = entry(0)
    for i in range(1, len(phases)):
        p = phases[i]
        n = p["n"]
        lines.append(f"  {previous} --> {entry(i)}")
        if n in skippable_ns:
            lines.append(f'  Q{n} -->|"yes"| P{n}')
            after = entry(i + 1) if i + 1 < len(phases) else "DONE[(done)]"
            lines.append(f'  Q{n} -->|"no: annotate \'Skipped - reason\' (I3)"| {after}')
        previous = f"P{n}"

    lines.append('  subgraph F6["inside phase 6"]')
    lines.append("    direction LR")
    lines.append('    A["implement subagent<br/>TDD, middle-out"]')
    lines.append(f'    R["{len(m["axes"])} axes: {" · ".join(m["axes"])}<br/>numbered brief, coverage counted"]')
    lines.append('    MUT["mutation prover<br/>coverage is run, not read"]')
    lines.append('    Z{"zero findings<br/>and red mutants? (I6)"}')
    lines.append('    GATE["pre-push gate<br/>canonical test + lint"]')
    lines.append("    A --> R --> MUT --> Z")
    lines.append('    Z -->|"no: fix loop"| A')
    lines.append('    Z -->|"yes"| GATE')
    lines.append("  end")
    lines.append("  P6 --> A")
    lines.append('  GATE --> MG{"CI green? (I1)"}')
    lines.append('  MG -->|"yes: merge"| SE["session-end gate<br/>audit.py · handoff (I5) · claim · worktree cleanup"]')
    lines.append('  MG -->|"no: do not merge"| A')
    lines.append("")
    for i, tf in enumerate(m["transversal"], start=1):
        lines.append(f'  T{i}["{tf["file"]}"]')
        lines.append(f'  T{i} -.->|"read"| {tf["target"]}')
    lines.append('  classDef phase fill:#eef3ff,stroke:#0b5cad,stroke-width:2px,font-size:15px')
    lines.append('  class ' + ",".join(f'P{p["n"]}' for p in phases) + ' phase')
    lines.append('  classDef decision fill:#ffffff,stroke:#8a5a00,font-size:11px')
    diamonds = [f'Q{p["n"]}' for p in phases if p["n"] in skippable_ns] + ["Z", "MG"]
    lines.append('  class ' + ",".join(diamonds) + ' decision')
    lines.append('  classDef transversal fill:#f6f6f6,stroke:#888,font-size:11px')
    if m["transversal"]:
        lines.append('  class ' + ",".join(f'T{i}' for i in range(1, len(m["transversal"]) + 1)) + ' transversal')
    lines.append("```")
    return "\n".join(lines)


def mermaid_responsibilities(m: dict) -> str:
    out = ["```mermaid", "flowchart LR"]
    for nid, label in (("ORCH", "orchestrator<br/>(the main agent)"),
                       ("IMPL", "implement subagent"), ("REV", "reviewers (3 axes)"),
                       ("PROV", "mutation prover"), ("SESS", "session (end)")):
        out.append(f'  {nid}["{label}"]')
    out.append('  ORCH -->|"phases 1-5, does not touch the code"| IMPL')
    out.append("  IMPL --> PROV --> SESS")
    out.append('  ORCH -->|"pastes the criteria into the brief"| REV')
    out.append('  REV -.->|"findings, never fixed in place"| IMPL')
    out.append("```")
    return "\n".join(out)


def mermaid_levels(m: dict) -> str:
    """The shape of the debt: how many rules sit at each level. The complete
    inventory is the table below, which omits nothing."""
    counts = {lv: sum(1 for r in m["rules"] if r["level"] == lv) for lv in LEVELS}
    out = ["```mermaid", "flowchart LR",
           f'  ST["structure<br/>{counts["structure"]} rules<br/><i>violation is impossible</i>"]',
           f'  SC["script<br/>{counts["script"]} rules<br/><i>a command can fail</i>"]',
           f'  PR["prompt only<br/>{counts["prompt"]} rules<br/><i>holds as long as the agent reads and obeys</i>"]',
           "  ST --> SC --> PR"]
    for lv in LEVELS:
        fill, stroke = LEVEL_STYLE[lv].split("|")
        out.append(f"  classDef {lv}_cls fill:{fill},stroke:{stroke}")
    out.append("  class ST structure_cls;")
    out.append("  class SC script_cls;")
    out.append("  class PR prompt_cls;")
    out.append("```")
    return "\n".join(out)


def emit(m: dict) -> tuple[str, list[str]]:
    misses: list[str] = list(m["misses"])
    for p in m["phases"]:
        if not p["exists"]:
            misses.append(f"phase {p['n']}: the rules file `{p['rules']}` does not exist")
    for r in m["rules"]:
        if not r["level"]:
            misses.append(f"rule without an enforcement level: «{r['rule'][:60]}»")
    for c in m["checks"]:
        if not any(c.replace("check_", "") in r["by"] for r in m["rules"]):
            misses.append(f"`{c}` in audit.py is not cited by any rule")
    for t in m["transversal"]:
        if not t["target"]:
            misses.append(f"transversal file `{t['file']}`: the 'Read when' line names "
                          "neither a phase nor the session end, so the map "
                          "does not know where to attach it")
    if len(m["axes"]) < 3:
        misses.append("the three review axes are not all found in REVIEW-CRITERIA.md")
    if not m["invariants"]:
        misses.append("no invariant extracted from INVARIANTS.md")

    by_level = {lv: [r for r in m["rules"] if r["level"] == lv] for lv in LEVELS}
    head = f"""# Workflow map — generated

> **Generated by `gen-flow.py`** from `WORKFLOW.md`, `phases/*.md`,
> `INVARIANTS.md`, `REVIEW-CRITERIA.md`, `audit.py`. Do not edit by hand: the
> map is a view of the sources, and every node comes from them.
> Regenerate: `python3 gen-flow.py`. Verify: `python3 gen-flow.py --check`.
> To look at it: `python3 -m http.server` and open `render-flow.html` (needs the
> network: mermaid comes from a CDN).

**Today's numbers** — phases: {len(m["phases"])} · transversal files: {len(m["transversal"])} ·
load-bearing rules: {len(m["rules"])} (structure {len(by_level["structure"])},
**script {len(by_level["script"])}**, **prompt only {len(by_level["prompt"])}**) ·
invariants: {len(m["invariants"])} · checks in `audit.py`: {len(m["checks"])} ·
general rules: {len(m["general"])} · review axes: {len(m["axes"])}

## 1. Pipeline

{mermaid_pipeline(m)}

## 2. Who does what

{mermaid_responsibilities(m)}

## 3. Where each check is enforced

{mermaid_levels(m)}

The complete inventory, rule by rule, is the table below: **no rule is
omitted**.

| rule | where | enforced by | level |
|---|---|---|---|
"""
    for lv in LEVELS:
        for r in by_level[lv]:
            head += f"| {r['rule']} | {r['where']} | {r['by']} | **{lv}** |\n"

    head += "\n## 4. Invariants and what verifies them\n\n| invariant | title | verified by |\n|---|---|---|\n"
    for i in m["invariants"]:
        hit = [c for c in m["checks"] if c.replace("check_", "") in
               " ".join(r["by"] for r in m["rules"]) and i["id"].lower() in
               " ".join(r["rule"] + r["by"] for r in m["rules"]).lower()]
        head += f"| {i['id']} | {i['title']} | {'`' + hit[0] + '`' if hit else 'prompt (no script)'} |\n"

    head += "\n## 5. Transversal files and phases\n\n| what | when / skills | rules |\n|---|---|---|\n"
    for p in m["phases"]:
        head += f"| phase {p['n']} — {p['name']} | {', '.join(p['skills'])} | {('`' + p['rules'] + '`') if not p['inline'] else p['rules']} |\n"
    for t in m["transversal"]:
        head += f"| transversal | {t['when']} | `{t['file']}` |\n"

    head += "\n## 6. General rules (verbatim from `WORKFLOW.md`)\n\n"
    for g in m["general"]:
        head += f"- {g}\n"

    head += "\n## 7. Coverage — what was extracted and where it went\n\n"
    head += "| element | how many | in this map |\n|---|---|---|\n"
    head += f"| phases from the pipeline | {len(m['phases'])} | sections 1 and 5 |\n"
    head += f"| phase rules files | {sum(1 for p in m['phases'] if p['exists'])}/{len(m['phases'])} existing | section 5 |\n"
    head += f"| transversal files | {len(m['transversal'])} | sections 1 and 5 |\n"
    head += f"| load-bearing rules | {len(m['rules'])} | section 3 (all, with level) |\n"
    head += f"| invariants | {len(m['invariants'])} | section 4 |\n"
    head += f"| checks of `audit.py` | {len(m['checks'])} | section 3 (cited) and 4 |\n"
    head += f"| general rules | {len(m['general'])} | section 6 |\n"
    head += f"| review axes | {len(m['axes'])} | section 1 (inside phase 6) |\n"

    if misses:
        head += "\n## Misses\n\n"
        for x in misses:
            head += f"- ⚠ {x}\n"
    return head, misses


TMP_PAGE = """<!doctype html><meta charset="utf-8">
<style>body{{font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0}}</style>
<body><pre id="out"></pre>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
const blocks = {sources};
mermaid.initialize({{ startOnLoad: false }});
const out = [];
for (let i = 0; i < blocks.length; i++) {{
  try {{ out.push("%%%DIAG" + i + "%%%" + (await mermaid.render("m" + i, blocks[i])).svg + "%%%END" + i + "%%%"); }}
  catch (e) {{ out.push("%%%DIAG" + i + "%%%MERMAID ERROR: " + e.message + "%%%END" + i + "%%%"); }}
}}
document.getElementById("out").textContent = out.join("\\n");
</script></body>"""

CDN_PAGE = """<!doctype html><meta charset="utf-8"><title>Workflow map</title>
<style>body{{font:14px/1.5 ui-sans-serif,system-ui;margin:0;padding:16px;background:#fbfaf7;color:#1c1c1c}}
.diag{{margin:0 0 24px;overflow:auto;background:#fff;border:1px solid #d8d8d8;border-radius:8px;padding:10px}}
.note{{color:#6b6b6b;font-size:12.5px}} code{{background:#f1f1f1;padding:1px 4px;border-radius:4px}}</style>
<h1>Workflow map</h1>
<p class="note">Generated by <code>gen-flow.py</code>. This copy draws in the browser
(mermaid from a CDN): <b>it needs the network</b>. If nothing shows, open
<code>FLOW.md</code> and serve the folder with <code>python3 -m http.server</code>.</p>
<div id="out"></div>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
const blocks = {sources};
mermaid.initialize({{ startOnLoad: false }});
for (let i = 0; i < blocks.length; i++) {{
  const box = document.createElement("div"); box.className = "diag";
  document.getElementById("out").append(box);
  try {{ box.innerHTML = (await mermaid.render("m" + i, blocks[i])).svg; }}
  catch (e) {{ box.textContent = blocks[i]; }}
}}
</script></body>"""


def _chrome() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    return None


def render_page(blocks: list[str], meta: str) -> str:
    """The view is a file that opens with a double click: the SVGs are drawn
    HERE, once, with headless Chrome. No server, no network. If Chrome or the
    network are missing, it falls back to a page that draws in the browser."""
    chrome = _chrome()
    if chrome:
        tmp = HERE / ".flow-render-tmp.html"
        tmp.write_text(TMP_PAGE.format(sources=json.dumps(blocks)), encoding="utf-8")
        try:
            dom = subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=15000", "--dump-dom", tmp.as_uri()],
                capture_output=True, text=True, timeout=180).stdout
        except Exception:
            dom = ""
        finally:
            tmp.unlink(missing_ok=True)
        svgs = []
        for i in range(len(blocks)):
            hit = re.search(rf"%%%DIAG{i}%%%([\s\S]*?)%%%END{i}%%%", dom)
            svgs.append(html.unescape(hit.group(1)).strip() if hit else "")
        if all("<svg" in s for s in svgs) and svgs:
            body = "\n".join(
                f'<h2>{"-" if not t else t}</h2>\n<div class="diag">{s}</div>'
                for t, s in zip(_TITLES[:len(svgs)], svgs))
            return (f'<!doctype html><meta charset="utf-8"><title>Workflow map</title>\n'
                    f'<style>body{{font:14px/1.5 ui-sans-serif,system-ui;margin:0;padding:16px;'
                    f'background:#fbfaf7;color:#1c1c1c}} h1{{font-size:18px;margin:0 0 4px}}'
                    f' h2{{font-size:14px;margin:22px 0 6px}} .diag{{overflow:auto;background:#fff;'
                    f'border:1px solid #d8d8d8;border-radius:8px;padding:10px}}'
                    f' .note{{color:#6b6b6b;font-size:12.5px}} code{{background:#f1f1f1;padding:1px 4px;'
                    f'border-radius:4px}}</style>\n<h1>Workflow map</h1>\n'
                    f'<p class="note">Generated by <code>gen-flow.py</code> — SVG inside the page: '
                    f'it opens with a double click, no server and no network. {meta}</p>\n{body}\n')
    return CDN_PAGE.format(sources=json.dumps(blocks))


_TITLES = ["1. Pipeline", "2. Who does what", "3. Where each check is enforced"]


def main(argv: list[str]) -> int:
    m = build_model()
    text, misses = emit(m)
    by_level = {lv: sum(1 for r in m["rules"] if r["level"] == lv) for lv in LEVELS}
    if "--check" in argv:
        # FLOW.md is a derivative: if it stays old, it is again a second copy
        # that drifts from the sources — exactly what this map must avoid
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            misses.append("FLOW.md is stale compared with the sources: regenerate it (`python3 gen-flow.py`)")
        # the view is a derivative of a derivative: at least the number of diagrams
        # must match (a byte-for-byte comparison depends on the renderer)
        n_blocks = len(re.findall(r"```mermaid", text))
        if not RENDER.exists():
            misses.append("render-flow.html is missing: regenerate it (`python3 gen-flow.py`)")
        else:
            rendered = RENDER.read_text(encoding="utf-8")
            n_svg = rendered.count("<svg")
            # Without Chrome the page falls back to the CDN renderer: the diagrams
            # are there, but as sources in the `const blocks` block, not as SVG.
            fallback = re.search(r"const blocks = (\[[\s\S]*?\]);", rendered)
            n_fallback = 0
            if fallback:
                try:
                    n_fallback = len(json.loads(fallback.group(1)))
                except json.JSONDecodeError:
                    n_fallback = 0
            if n_svg < n_blocks and n_fallback < n_blocks:
                misses.append(
                    f"render-flow.html has {max(n_svg, n_fallback)} diagrams "
                    f"out of {n_blocks}: regenerate it"
                )
    else:
        OUT.write_text(text, encoding="utf-8")
        blocks = re.findall(r"```mermaid\n([\s\S]*?)```", text)
        meta = (f"{len(m['phases'])} phases · {len(m['rules'])} rules "
                f"(structure {by_level['structure']}, script {by_level['script']}, "
                f"prompt only {by_level['prompt']}) · {len(m['invariants'])} invariants.")
        RENDER.write_text(render_page(blocks, meta), encoding="utf-8")
    print(f"  phases {len(m['phases'])} · rules {len(m['rules'])} "
          f"(structure {by_level['structure']}, script {by_level['script']}, "
          f"prompt only {by_level['prompt']}) · invariants {len(m['invariants'])} · "
          f"checks {len(m['checks'])} · general rules {len(m['general'])}")
    if "--check" not in argv:
        print(f"  wrote {OUT.name}")
    if misses:
        print(f"  {len(misses)} misses:")
        for x in misses:
            print("    · " + x)
        return 1
    print("  coverage: complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
