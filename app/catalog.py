"""Documents of the workflow window: what a feature or a repo lists, the workflow map, and the
vendored renderers.

Pure reads of files (and of the report store, opened only if it exists); no function here writes. The server keeps the ids a listing returned
(its catalog) and serves a document only through one of them.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

if "traces" not in sys.modules:  # run as a script or loaded by path: siblings are importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import traces

if TYPE_CHECKING:
    from traces import Row

DocKind = Literal["adr", "design", "model", "mockup", "spec-model", "workflow", "skill",
                  "spec", "handoff", "report", "section"]


@dataclass(frozen=True)
class Document:
    id: str
    kind: DocKind
    name: str
    path: str
    stored: tuple[str, ...] | None = None   # the store key of a report that lives in the store
    section: tuple[str, str] | None = None   # (parser, heading) of a gate section of the file at `path`


@dataclass(frozen=True)
class GateSection:
    heading: str
    source: str
    state: Literal["present", "missing", "unreadable"]
    doc: Document | None
    rev: str | None
    note: str | None


MODEL_HEADING = "## Model"
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
PHASE_NAMED = re.compile(r"\bphases?\s+(\d+(?:\s*,\s*\d+)*)")
PHASE_ZERO = "Start"   # the Pipeline table starts at phase 1
MANIFEST_ROW = re.compile(r"^\|\s*(?P<name>[^|\s]+\.js)\s*\|.*\|\s*(?P<sha>[0-9a-f]{64})\s*\|\s*$")


def doc_id(kind: str, path: str) -> str:
    """The wire id of a document: what a listing returns and `/api/documents/{id}` is asked with."""
    return hashlib.sha1(f"doc\0{kind}\0{path}".encode()).hexdigest()[:12]


def make(kind: DocKind, name: str, path: Path) -> Document:
    return Document(doc_id(kind, str(path)), kind, name, str(path))


def files(directory: Path, suffix: str) -> list[Path]:
    """The files directly in `directory` ending in `suffix`; none when it is not a directory."""
    try:
        return sorted(p for p in directory.iterdir() if p.suffix == suffix and p.is_file())
    except (FileNotFoundError, NotADirectoryError):
        return []


def model_section(text: str) -> str | None:
    """From the `## Model` heading to the next level-2 heading (code fences do not start or end it),
    keeping its `### ` subsections; None when the text has no such section."""
    lines = text.splitlines(keepends=True)
    start = end = None
    fence = None   # the opening run of backticks or tildes while inside a code fence
    for number, line in enumerate(lines):
        marker = FENCE.match(line)
        if marker:
            run, rest = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
        elif fence is None and start is None and line.rstrip() == MODEL_HEADING:
            start = number
        elif fence is None and start is not None and line.startswith("## "):
            end = number
            break
    if start is None:
        return None
    return "".join(lines[start:end])


def feature_documents(slug: str, tree: Path | None) -> list[Document]:
    """The documents of the feature `slug` in `tree`, by exact slug: its spec's `## Model`,
    `docs/models/<slug>.md`, `docs/design/<slug>.md` and every `*.html` directly in
    `.scratch/product-review/<slug>/`. Raises OSError when the spec exists but cannot be read."""
    if tree is None or slug in ("", ".", "..") or "/" in slug or "\\" in slug:
        return []
    found: list[Document] = []
    spec = tree / "docs" / "specs" / f"{slug}.md"
    try:
        text = spec.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        text = ""
    if model_section(text) is not None:
        found.append(make("spec-model", f"docs/specs/{slug}.md#Model", spec))
    for kind, directory in (("model", "models"), ("design", "design")):
        path = tree / "docs" / directory / f"{slug}.md"
        if path.is_file():
            found.append(make(kind, f"docs/{directory}/{slug}.md", path))
    for path in files(tree / ".scratch" / "product-review" / slug, ".html"):
        found.append(make("mockup", f".scratch/product-review/{slug}/{path.name}", path))
    return found


def row_documents(row: Row, stored: Iterable[tuple[str, tuple[str, ...]]] = (),
                  store: Path | None = None) -> list[Document]:
    """The row's own documents: its spec traces (`spec`, named by path), its handoff traces newest
    first (`handoff`, named by file name) and the stored reports `stored` ((name, key) pairs of
    `store`; their id combines the store path and the key). No report is a file (#70)."""
    found = [make("spec", t.path, Path(t.path)) for t in row.traces if t.kind == "spec"]
    handoffs = sorted((t for t in row.traces if t.kind == "handoff"),
                      key=lambda t: (t.at, Path(t.path).name), reverse=True)
    found += [make("handoff", Path(t.path).name, Path(t.path)) for t in handoffs]
    found += [Document(doc_id("report", f"{store}#{'/'.join(key)}"), "report", name, str(store), key)
              for name, key in stored]
    return found


PHASE_KINDS: dict[int, tuple[DocKind, ...]] = {
    0: ("spec",), 1: ("spec", "spec-model"), 2: ("mockup",), 3: ("spec", "spec-model"),
    4: ("spec",), 5: ("design",), 6: ("spec", "model", "report", "handoff")}


def phase_artefacts(n: int, docs: Iterable[Document]) -> list[Document]:
    """The documents of phase `n` by `PHASE_KINDS`: for each of its kinds in order, the documents
    of `docs` of that kind in the order given. A phase the mapping does not know has none."""
    docs = list(docs)
    return [d for kind in PHASE_KINDS.get(n, ()) for d in docs if d.kind == kind]


def section_body(text: str, parser: Literal["spec", "model"], heading: str, wf: ModuleType,
                 check_model: ModuleType) -> str | None:
    """The body of `heading` in `text`, cut by the parser the gate uses: `wf.parse_sections` for a
    `spec` (the exact heading, code fences skipped), `check_model.split_sections` for a `model` (the
    heading lower-cased). Subsections stay in the body. None when the text has no such heading."""
    if parser == "spec":
        return wf.parse_sections(text).get(heading)
    return check_model.split_sections(text).get(heading.lower())


def _source_name(doc: Document) -> str:
    """The document's place in its tree: `docs/<specs|models>/<file>`."""
    return "/".join(Path(doc.path).parts[-3:])


def _capital(heading: str) -> str:
    return heading[:1].upper() + heading[1:]


def phase_sections(n: int, docs: Iterable[Document], wf: ModuleType, check_model: ModuleType) -> list[GateSection]:
    """The gate sections of phase `n` in the gate's order: one per heading of `wf.GATE_SECTIONS[n]`
    cut from the first `spec` of `docs`, and at phase 6 one per heading `check_model.contracted_sections`
    names for the first `model` of `docs` (shown with a capital). Each is `present` (a `section`
    document and the first 12 hex digits of the sha1 of its body), `missing` (the file has no such
    heading) or `unreadable` (`note` is the OSError text). No heading, or no source among `docs`,
    gives none. A pure read."""
    docs = list(docs)
    sources: list[tuple[Document, Literal["spec", "model"]]] = []
    spec = next((d for d in docs if d.kind == "spec"), None)
    if spec is not None and wf.GATE_SECTIONS.get(n):
        sources.append((spec, "spec"))
    model = next((d for d in docs if d.kind == "model"), None) if n == 6 else None
    if model is not None:
        sources.append((model, "model"))
    found: list[GateSection] = []
    for source, parser in sources:
        try:
            text = Path(source.path).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            headings = (wf.GATE_SECTIONS[n] if parser == "spec"
                        else tuple(map(_capital, check_model.REQUIRED_SECTIONS)))
            found += [GateSection(h, _source_name(source), "unreadable", None, None, str(error)) for h in headings]
            continue
        headings = (wf.GATE_SECTIONS[n] if parser == "spec"
                    else tuple(map(_capital, check_model.contracted_sections(text))))
        for heading in headings:
            body = section_body(text, parser, heading, wf, check_model)
            if body is None:
                found.append(GateSection(heading, _source_name(source), "missing", None, None, None))
                continue
            doc = Document(doc_id("section", f"{source.path}#{parser}#{heading}"), "section",
                           f"{_source_name(source)}#{heading}", source.path, section=(parser, heading))
            found.append(GateSection(heading, _source_name(source), "present", doc,
                                     hashlib.sha1(body.encode()).hexdigest()[:12], None))
    return found


@dataclass(frozen=True)
class Diagram:
    title: str
    source: str
    doc: Document | None
    generated: Literal["data_flow", "call_stack"] | None
    mermaid: str


DIAGRAM_SECTIONS: dict[int, tuple[str, ...]] = {1: ("Model",), 3: ("Model", "System Architecture"), 6: ()}


HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")


def mermaid_blocks_with_headings(text: str) -> list[tuple[tuple[str, ...], str]]:
    """Every fenced ```mermaid block of `text` with the stack of markdown headings it sits under
    (level 1 left out, so a spec's title is not in every caption), in document order. A code fence of
    another language is skipped whole: a `## ` line inside it opens no heading. A pure scan."""
    found: list[tuple[tuple[str, ...], str]] = []
    stack: list[tuple[int, str]] = []
    fence: str | None = None   # the opening run of the fence we are inside
    block: list[str] | None = None   # the lines of the mermaid block being read
    for line in text.splitlines():
        marker = FENCE.match(line)
        if marker:
            run, rest = marker.groups()
            if fence is None:
                fence = run
                block = [] if rest.strip().split(" ")[0].lower() == "mermaid" else None
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                if block is not None:
                    found.append((tuple(title for _, title in stack), "\n".join(block)))
                fence, block = None, None
            elif block is not None:
                block.append(line)
        elif fence is not None:
            if block is not None:
                block.append(line)
        else:
            heading = HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if level > 1:
                    stack.append((level, heading.group(2)))
    return found


def _read(doc: Document) -> str | None:
    try:
        return Path(doc.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


DESIGN_BLOCK = re.compile(r"```json design\s*\n(.*?)\n```", re.S)   # the one the design guard reads (guards/check_design.load_design)
KINDS = ("add", "mod", "keep", "ghost")


def design_block(text: str) -> dict | None:
    """The parsed ```json design block of a design document, None when it has none. Raises ValueError
    on JSON that does not parse. (The guard's `load_design` takes a path and exits on a miss, so it is not
    called; the fence pattern is the same.)"""
    hit = DESIGN_BLOCK.search(text)
    if hit is None:
        return None
    return json.loads(hit.group(1))   # json.JSONDecodeError is a ValueError


def _label(text: object) -> str:
    """A node's text as a Mermaid quoted label: the characters that end or reshape a label become numeric entities."""
    return re.sub(r'[#";<>]', lambda m: f"#{ord(m[0])};", str(text)).replace("\n", " ")


def _kind(kind: object) -> str:
    """The class a `data_flow` kind is drawn with: `new` as `add` (Decision 4), `add`/`mod`/`keep`/`ghost` as they are, any other `keep`."""
    return "add" if kind == "new" else kind if kind in KINDS else "keep"


def _flowchart(direction: str, nodes: list[tuple[str, str]], edges: list[tuple[int, int]]) -> str:
    """A Mermaid flowchart of `nodes` (label, class) and `edges` (node index pairs), one classDef per class present."""
    lines = [f"flowchart {direction}"]
    lines += [f'  n{i}["{_label(name)}"]' for i, (name, _) in enumerate(nodes)]
    lines += [f"  n{a} --> n{b}" for a, b in edges]
    lines += [f"  class n{i} {kind}" for i, (_, kind) in enumerate(nodes)]
    lines += [f"  classDef {kind} stroke-width:1.5px" for kind in dict.fromkeys(k for _, k in nodes)]
    return "\n".join(lines)


def data_flow_mermaid(design: dict) -> str:
    """`flowchart LR` of the design's `data_flow`: one node per entry (Mermaid id `n<i>`, the design's id in
    a quoted label), one edge per edge, a `ghost` node for an edge end that is no node, each node classed by
    its kind and a classDef per kind present (stroke width only: the colours are the page's tokens). Pure."""
    flow = design.get("data_flow") or {}
    nodes = [(n["id"], _kind(n.get("kind"))) for n in flow.get("nodes", [])]
    index = {name: i for i, (name, _) in enumerate(nodes)}
    edges = []
    for edge in flow.get("edges", []):
        for end in (edge["from"], edge["to"]):
            if end not in index:
                index[end] = len(nodes)
                nodes.append((end, "ghost"))
        edges.append((index[edge["from"]], index[edge["to"]]))
    return _flowchart("LR", nodes, edges)


def call_stack_mermaid(design: dict) -> str:
    """`flowchart TD` of the design's `call_stack`, children in order. Each call is classed like the
    `data_flow` node of the same id, `keep` when there is none. A design without `call_stack` is one node. Pure."""
    kinds = {n["id"]: _kind(n.get("kind")) for n in (design.get("data_flow") or {}).get("nodes", [])}
    nodes: list[tuple[str, str]] = []
    edges: list[tuple[int, int]] = []

    def walk(call: dict, parent: int | None) -> None:
        here = len(nodes)
        nodes.append((call["name"], kinds.get(call["name"], "keep")))
        if parent is not None:
            edges.append((parent, here))
        for child in call.get("children", []):
            walk(child, here)

    walk(design.get("call_stack") or {"name": "no call_stack"}, None)
    return _flowchart("TD", nodes, edges)


def _generated(source: Document) -> list[Diagram]:
    """The two diagrams of a design's json block, or one failed `Diagram` (empty `mermaid`, the error in the
    title) when the block cannot be read or has the wrong shape. No block, no diagram."""
    where = f"{_source_name(source)} § json design"
    text = _read(source)
    try:
        design = design_block(text) if text is not None else None
        if design is None:
            return []
        return [Diagram("Data flow (generated)", where, None, "data_flow", data_flow_mermaid(design)),
                Diagram("Call stack (generated)", where, None, "call_stack", call_stack_mermaid(design))]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return [Diagram(f"json design: {type(error).__name__} {error}", where, None, None, "")]


def phase_diagrams(n: int, docs: Iterable[Document]) -> list[Diagram]:
    """The Mermaid blocks the stage of phase `n` draws, in document order. Phases 1 and 3 take the
    first `spec` of `docs` and keep the blocks under a heading of `DIAGRAM_SECTIONS[n]`; phase 6 takes
    the whole of the first `model`; phase 5 takes the first `design`: the two diagrams generated from its
    json block (`_generated`), then its hand-written blocks. Any other phase, a missing source and an unreadable
    file give `[]` (the text fold of the same document shows the read error). A pure read."""
    docs = list(docs)
    if n == 6:
        source = next((d for d in docs if d.kind == "model"), None)
    elif n == 5:
        source = next((d for d in docs if d.kind == "design"), None)
    elif n in DIAGRAM_SECTIONS:
        source = next((d for d in docs if d.kind == "spec"), None)
    else:
        return []
    text = _read(source) if source is not None else None
    if text is None:
        return []
    keep = DIAGRAM_SECTIONS.get(n)
    where = _source_name(source)
    generated = _generated(source) if n == 5 else []
    return generated + [
        Diagram(" › ".join(path) or Path(source.path).name, f"{where} § {path[0]}" if path else where,
                source, None, body)
        for path, body in mermaid_blocks_with_headings(text)
        if not keep or (path and path[0] in keep)]


# --- ticket 129 (design-diagrams): the invariants of a spec with a live status from the matrices ---------

Status = Literal["pinned", "blind", "never-mutated", "test-missing", "not-checkable"]


@dataclass(frozen=True)
class InvariantRow:
    n: int
    law: str
    negation: str | None
    verified: str
    tests: tuple[str, ...]
    status: Status
    evidence: tuple[dict, ...]


@dataclass(frozen=True)
class InvariantTable:
    source: str
    readable: bool
    rows: tuple[InvariantRow, ...]
    raw: str | None
    why: str | None
    tree: str | None = None   # the tree the statuses were joined against (the row's worktree or its spec's repo)


@dataclass(frozen=True)
class MutantRow:
    matrix: str
    recorded_at: str
    mutant: str
    kind: str
    test: str
    outcome: str
    file: str | None


TEST_ID = re.compile(r"(?P<path>[\w./-]+\.py|…|\.\.\.)?::(?P<name>\w+(?:::\w+)*)(?:\[[^\]]*\])?")
PARAM = re.compile(r"\[[^\]]*\]$")
STATUS_ORDER = ("blind", "test-missing", "never-mutated", "pinned")   # worst first


def normalised_tests(cell: str) -> tuple[str, ...]:
    """Every `path.py::name` (or `path.py::Class::name`) of a table cell, in order. Backticks, a `(to write)` /
    `(da scrivere …)` marker and a `[param]` suffix are dropped; a `…::name` or `::name` shorthand takes the path of
    the id before it (and is skipped when none came before). A cell with none gives `()`. A pure scan."""
    cell = re.sub(r"\((?:to write|da scrivere)[^)]*\)", " ", cell.replace("`", ""))
    found: list[str] = []
    path: str | None = None
    for hit in TEST_ID.finditer(cell):
        if hit.group("path") not in (None, "…", "..."):
            path = hit.group("path")
        if path is not None:
            found.append(f"{path}::{hit.group('name')}")
    return tuple(found)


def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [c.replace("\\|", "|").strip() for c in parts[1:-1 if line.rstrip().endswith("|") else None]]


def invariant_table(text: str, source: str) -> InvariantTable | None:
    """The first markdown table after a `### System invariants` heading (before the next heading of level 3 or
    above). Columns are read by meaning: law = the first header holding "law" or "invariant", negation = the header
    holding "negation", verified = the one holding "verified" (optional). Without law and negation the table
    comes back unreadable, its own lines as `raw` and the reason in `why`. No heading or no table gives None.
    Every row starts `not-checkable` with no evidence. A pure scan."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r"^ {0,3}###[ \t]+System invariants\s*$", line, re.I)), None)
    if start is None:
        return None
    table: list[str] = []
    for line in lines[start + 1:]:
        if re.match(r"^ {0,3}#{1,3}[ \t]", line):
            break
        if line.strip().startswith("|"):
            table.append(line.rstrip())
        elif table:
            break
    if len(table) < 2:
        return None
    headers = [h.lower() for h in _cells(table[0])]

    def column(*words: str, after: int = -1) -> int | None:
        return next((i for i, h in enumerate(headers) if i != after and any(w in h for w in words)), None)

    law, negation, verified = column("law", "invariant"), column("negation"), column("verified")
    if law is None or negation is None:
        why = f"no column reads as law and negation (columns: {', '.join(_cells(table[0]))})"
        return InvariantTable(source, False, (), "\n".join(table), why)

    def cell(cells: list[str], i: int | None) -> str:
        return cells[i] if i is not None and i < len(cells) else ""

    rows = []
    for k, line in enumerate(table[2:], start=1):
        cells = _cells(line)
        said = cell(cells, verified)
        rows.append(InvariantRow(k, cell(cells, law), cell(cells, negation) or None, said,
                                 normalised_tests(said), "not-checkable", ()))
    return InvariantTable(source, True, tuple(rows), None, None)


def mutant_rows(store: Path | None) -> tuple[MutantRow, ...]:
    """The mutant rows of every matrix of `store` that went red or stayed green (outcome `pinned` or `blind-test`;
    controls, errors and the rest are left out), with five columns and the first 300 characters of the diff, whose
    first `--- a/` line gives `file`. The store is opened read-only (`mode=ro`) and nothing is written. `()` for no
    store or a file that is not there; `sqlite3.Error` for a store that cannot be read."""
    if store is None or not store.is_file():
        return ()
    conn = sqlite3.connect(f"file:{quote(str(store))}?mode=ro", uri=True)
    try:
        found = conn.execute(
            "SELECT name, recorded_at, mutant_id, kind, COALESCE(test, ''), outcome, substr(diff, 1, 300)"
            " FROM mutation_result WHERE kind != 'control' AND outcome IN ('pinned', 'blind-test')").fetchall()
    finally:
        conn.close()
    rows = []
    for name, at, mutant, kind, test, outcome, diff in found:
        hit = re.search(r"^--- a/([^\t\r\n]+)", diff or "", re.M)
        rows.append(MutantRow(name, at, mutant, kind, test, outcome, hit.group(1).rstrip() if hit else None))
    return tuple(rows)


def defines_test(tree: Path, test: str) -> bool:
    """Whether the file of `test` (`path.py::[Class::]name`) in `tree` has `def name(`, `async def name(` or
    `class name` at the start of a line (indent allowed). False when the file cannot be read."""
    path, _, rest = test.partition("::")
    name = rest.rsplit("::", 1)[-1]
    try:
        text = (tree / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    found = re.search(rf"^[ \t]*(?:(?:async[ \t]+)?def[ \t]+{re.escape(name)}[ \t]*\(|class[ \t]+{re.escape(name)}\b)", text, re.M)
    return found is not None


def _test_status(tree: Path, test: str, rows: Sequence[MutantRow]) -> tuple[Status, dict]:
    """The status of one test id and its evidence: the newest matrix of the rows that count decides."""
    counting = [r for r in rows if r.kind != "control" and r.outcome in ("pinned", "blind-test")
                and PARAM.sub("", r.test) == test and r.file and (tree / r.file).exists()]
    if not counting:
        status: Status = "never-mutated" if defines_test(tree, test) else "test-missing"
        return status, {"test": test, "matrix": None, "mutant": None, "outcome": None, "at": None}
    newest = max((r.recorded_at, r.matrix) for r in counting)
    decided = [r for r in counting if (r.recorded_at, r.matrix) == newest]
    pick = next((r for r in decided if r.outcome == "blind-test"), decided[0])
    return ("blind" if pick.outcome == "blind-test" else "pinned",
            {"test": test, "matrix": pick.matrix, "mutant": pick.mutant, "outcome": pick.outcome, "at": pick.recorded_at})


def with_status(table: InvariantTable, tree: Path, rows: Sequence[MutantRow]) -> InvariantTable:
    """`table` with each row's status and evidence. A mutant row counts for a test when its `[param]`-stripped test
    equals the id and the file its diff mutates exists in `tree`; per test the newest matrix decides (a `blind-test`
    there is blind, else pinned); with no counting row the test is test-missing or never-mutated by `defines_test`;
    an invariant takes its worst test (`STATUS_ORDER`) and stays not-checkable with no test. An unreadable table is
    returned as it is."""
    if not table.readable:
        return table
    done = []
    for row in table.rows:
        judged = [_test_status(tree, t, rows) for t in row.tests]
        status: Status = (min((s for s, _ in judged), key=STATUS_ORDER.index) if judged else "not-checkable")
        done.append(replace(row, status=status, evidence=tuple(e for _, e in judged)))
    return replace(table, rows=tuple(done))


def spec_repo(spec: Path) -> Path | None:
    """The nearest parent of `spec` that holds a `.git` (a directory or a file), or None."""
    return next((p for p in spec.resolve().parents if (p / ".git").exists()), None)


def phase_invariants(n: int, docs: Iterable[Document], tree: Path | None, rows: Sequence[MutantRow]) -> InvariantTable | None:
    """The invariants of the first `spec` of `docs` with their status, for phases 3 and 6 only. None for any other
    phase, with no spec, or with a spec that has no `### System invariants` table. A spec that cannot be read gives an
    unreadable table carrying the OSError text. With no `tree` (a row with no worktree) it joins against the repo that
    holds the spec (`spec_repo`); in no repo every row stays not-checkable and `why` says so. `tree` of the answer names the tree used."""
    if n not in (3, 6):
        return None
    source = next((d for d in docs if d.kind == "spec"), None)
    if source is None:
        return None
    where = _source_name(source)
    try:
        text = Path(source.path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return InvariantTable(where, False, (), None, f"{error.strerror or error}")
    table = invariant_table(text, where)
    if table is None or not table.readable:
        return table
    if tree is None:
        tree = spec_repo(Path(source.path))
    if tree is None:
        return replace(table, why="the spec is in no git repo, so there is no tree to check the tests against")
    return replace(with_status(table, tree, rows), tree=str(tree))


def text_lines(doc: Document, parsers: tuple[ModuleType, ModuleType] | None = None) -> int | None:
    """The number of lines of a document, or of a gate section's body (cut with `parsers`, the ones it
    was listed with), for the fold's length. None for a stored report and when it cannot be read."""
    if doc.stored is not None:
        return None
    try:
        body = document_body(doc, None, parsers)
    except (OSError, ValueError):
        return None
    return len(body.decode("utf-8", errors="replace").splitlines())


def repo_documents(primary: Path) -> list[Document]:
    """Every `*.md` directly in `docs/adr/`, `docs/design/`, `docs/models/` and every `*.html`
    directly in `.scratch/product-review/*/` of `primary`, sorted by name (the path in the tree)."""
    found = [make(kind, f"docs/{directory}/{path.name}", path)
             for kind, directory in (("adr", "adr"), ("design", "design"), ("model", "models"))
             for path in files(primary / "docs" / directory, ".md")]
    try:
        slugs = sorted(p for p in (primary / ".scratch" / "product-review").iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        slugs = []
    found += [make("mockup", f".scratch/product-review/{d.name}/{path.name}", path)
              for d in slugs for path in files(d, ".html")]
    return sorted(found, key=lambda d: d.name)


CONTENT_TYPES = {".jsonl": "application/x-ndjson", ".json": "application/json"}


def stored_body(key: tuple[str, ...], store: Path | None) -> bytes:
    """A stored report as the file would have been: a review report as its `row_json` lines, a
    brief as its one-line manifest, a matrix one JSON object per row, a manifest as its
    `manifest_json`. Raises OSError when the
    store is gone, is not a SQLite file or holds no such item."""
    rs = traces.load_report_store()
    try:
        conn = rs.open_existing(store)
        if conn is None:
            raise OSError(f"{store}: no report store")
        try:
            kind, *names = key
            if kind == "review":
                text = rs.review_report_text(conn, *names)
            elif kind == "brief":
                stored = rs.brief_text(conn, *names)
                text = None if stored is None else stored + "\n"
            elif kind == "matrix":
                rows = rs.matrix_rows(conn, names[0])
                text = None if rows is None else "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            else:
                stored = rs.manifest_text(conn, names[0])
                text = None if stored is None else stored + "\n"
        finally:
            conn.close()
    except sqlite3.Error as error:  # a corrupt store is a 503 like a vanished one
        raise OSError(f"{store}: {error}") from error
    if text is None:
        raise OSError(f"{store}: no stored report {'/'.join(key)}")
    return text.encode()


def document_body(doc: Document, store: Path | None = None,
                  parsers: tuple[ModuleType, ModuleType] | None = None) -> bytes:
    """The file's bytes; for `spec-model` the text of the `## Model` section of the spec; for a
    `section` the body of that heading, cut with `parsers` (`(wf, check_model)`, the ones the section
    was listed with; a section without them is a ValueError); for a stored report its body from
    `store`. Raises OSError when the file cannot be read (or no longer has the section), or the
    store is gone or no longer holds the report."""
    if doc.stored is not None:
        return stored_body(doc.stored, store)
    raw = Path(doc.path).read_bytes()
    if doc.section is not None:
        parser, heading = doc.section
        if parsers is None:
            raise ValueError(f"{doc.path}: a section is cut with the parsers it was listed with")
        body = section_body(raw.decode("utf-8", errors="replace"), parser, heading, *parsers)
        if body is None:
            raise OSError(f"{doc.path}: no `## {heading}` section")
        return body.encode()
    if doc.kind != "spec-model":
        return raw
    section = model_section(raw.decode("utf-8", errors="replace"))
    if section is None:
        raise OSError(f"{doc.path}: no `{MODEL_HEADING}` section")
    return section.encode()


def vendor_files(vendor_dir: Path) -> dict[str, str]:
    """`{name: sha256}` of the `.js` rows of `MANIFEST.md`. Raises OSError when it cannot be read,
    UnicodeDecodeError (a ValueError) when it is not UTF-8."""
    rows = (MANIFEST_ROW.match(line) for line in
            (vendor_dir / "MANIFEST.md").read_text(encoding="utf-8").splitlines())
    return {m["name"]: m["sha"] for m in rows if m}


# --- the workflow map (#29) -------------------------------------------------

def load_genflow(workflow_dir: Path) -> ModuleType:
    """`gen-flow.py` of the workflow install, loaded by path (its `HERE` is then the install).
    Writes no bytecode next to it. Raises OSError, or what the module raises while loading."""
    found = importlib.util.spec_from_file_location("genflow_desk", workflow_dir / "gen-flow.py")
    if found is None or found.loader is None:
        raise OSError(f"{workflow_dir / 'gen-flow.py'}: cannot be loaded")
    module = importlib.util.module_from_spec(found)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        found.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def load_check_model(workflow_dir: Path) -> ModuleType:
    """`guards/check_model.py` of the workflow install, loaded by path (it imports only the standard
    library). Writes no bytecode next to it. Raises OSError, or what the module raises while loading."""
    path = workflow_dir / "guards" / "check_model.py"
    found = importlib.util.spec_from_file_location("check_model_desk", path)
    if found is None or found.loader is None:
        raise OSError(f"{path}: cannot be loaded")
    module = importlib.util.module_from_spec(found)
    with traces.no_bytecode():
        found.loader.exec_module(module)
    return module


def phase_skills(phase: int, workflow_dir: Path, skills_dir: Path | None,
                 wf: ModuleType) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(own, included)`: the skills the pipeline table gives `phase` and the referenced ones
    `wf.resolve_skills` includes. Phase 0 has none; no skills store (`None`) includes none."""
    if phase == 0:
        return (), ()
    table = wf.pipeline(workflow_dir)
    own = table[phase]
    if skills_dir is None:
        return own, ()
    others = frozenset(s for n, names in table.items() if n != phase for s in names) - frozenset(own)
    return own, wf.resolve_skills(own, skills_dir, others)[0]


def rules_file(phase: int, workflow_dir: Path, wf: ModuleType) -> str:
    """The name, in `workflow_dir`, of the file holding `phase`'s rules (as `wf next` reads it)."""
    if phase == 0:
        return f"phases/{min((workflow_dir / 'phases').glob('0-*.md')).name}"
    return "WORKFLOW.md" if phase == 4 else wf._rules_file(workflow_dir, phase)


def phase_documents(phase: int, workflow_dir: Path, skills_dir: Path | None, wf: ModuleType) -> list[Document]:
    """The files `wf.bundle(phase, ...)` reads, in its order: `workflow` for those under
    `workflow_dir` (named by their path in it), `skill` for `<skills_dir>/<name>/SKILL.md` of the
    phase's skills and of the referenced ones `wf.resolve_skills` includes (named by the skill).
    A missing skill, or no skills store (`skills_dir` None), is no document. Raises OSError,
    ValueError as `wf.bundle` does."""
    def workflow_file(name: str) -> Document:
        return make("workflow", name, workflow_dir / name)

    if phase == 0:
        return [workflow_file("WORKFLOW.md"),
                *(workflow_file(n) for n in wf.phase0_files(workflow_dir)),
                *(workflow_file(f"phases/{p.name}") for p in sorted((workflow_dir / "phases").glob("0-*.md")))]
    found = [workflow_file(rules_file(phase, workflow_dir, wf))]
    if skills_dir is not None:
        own, included = phase_skills(phase, workflow_dir, skills_dir, wf)
        found += [make("skill", name, skills_dir / name / "SKILL.md")
                  for name in (*own, *included) if (skills_dir / name / "SKILL.md").is_file()]
    found += [workflow_file(name) for name in wf.transversal_files(workflow_dir, phase)]
    return found


def rule_phases(where: str) -> tuple[int, ...]:
    """The phase numbers a `Where` cell names as `phase N` or `phases N, M`, ascending and
    without repeats; empty for a general rule."""
    named = {int(n) for group in PHASE_NAMED.findall(where) for n in re.findall(r"\d+", group)}
    return tuple(sorted(named))


def workflow_map(workflow_dir: Path, skills_dir: Path | None, wf: ModuleType, genflow: ModuleType) -> dict:
    """`{phases, transversal, general_rules}` of the endpoint contract, `overlay` null in every
    phase. The phases, files and skills come from `wf`'s own functions, the names, conditions,
    transversal files and rules from `genflow.build_model()`, the exit check from the docstring of
    `wf.check_phase<N>`: nothing is copied. Raises OSError, ValueError when the install cannot be
    read."""
    model = genflow.build_model()
    names = {int(p["n"]): p["name"] for p in model["phases"]}
    rules = [{"text": r["rule"], "level": r["level"], "by": r["by"], "phases": rule_phases(r["where"])}
             for r in model["rules"]]
    phases = []
    for n in wf.PHASES:
        documents = phase_documents(n, workflow_dir, skills_dir, wf)
        name, condition = genflow.name_and_condition(names[n]) if n in names else (PHASE_ZERO, "")
        own, referenced = phase_skills(n, workflow_dir, skills_dir, wf)
        phases.append({
            "n": n, "name": name, "condition": condition or None,
            "rules_file": rules_file(n, workflow_dir, wf),
            "skills": list(own), "referenced": list(referenced),
            "exit_check": inspect.getdoc(getattr(wf, f"check_phase{n}")) or "",
            "rules": [{k: r[k] for k in ("text", "level", "by")} for r in rules if n in r["phases"]],
            "documents": documents, "overlay": None})
    transversal = [{"file": t["file"], "when": t["when"],
                    "document": make("workflow", t["file"], workflow_dir / t["file"])
                    if (workflow_dir / t["file"]).is_file() else None}
                   for t in model["transversal"]]
    return {"phases": phases, "transversal": transversal,
            "general_rules": [{k: r[k] for k in ("text", "level", "by")} for r in rules if not r["phases"]]}


def overlay(phase: object, n: int) -> dict | None:
    """What a row of the last snapshot says about phase `n`: the verdict of `phase.verdicts` for it
    plus `here` (`n` is the row's derived phase); None for a phase with no verdict."""
    verdict = next((v for v in phase.verdicts if v["phase"] == n), None)
    if verdict is None:
        return None
    return {"status": verdict["status"], "problems": list(verdict["problems"]),
            "reason": verdict["reason"], "here": n == phase.phase}
