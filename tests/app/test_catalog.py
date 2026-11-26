"""Tests for app/catalog.py: the documents a feature and a repo list, over real files in tmp_path."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
APP = ROOT / "app"


def load(name: str, path: Path):
    """Import a file by path (the app has no package); first loader wins."""
    if name in sys.modules:
        return sys.modules[name]
    found = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module
    found.loader.exec_module(module)
    return module


traces = load("traces", APP / "traces.py")
catalog = load("catalog", APP / "catalog.py")


def put(tree: Path, rel: str, text: str = "x\n") -> Path:
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


SPEC = """\
---
feature: f
---

## Phase plan

| a | b |

## Model

```mermaid
flowchart LR
  A --> B
```

### States

Text under a subsection.

```text
## not a heading, it is inside a fence
```

## Other section

### Not the model

secret
"""


def only(docs):
    assert len(docs) == 1, docs
    return docs[0]


def test_doc_id_hashes_kind_and_path():
    """Witness the same path as `design` and as `model`, and two paths of one kind - distinguish an
    id of (kind, path) from an id of the path alone or of the kind alone."""
    expected = hashlib.sha1(b"doc\0design\0/r/docs/design/f.md").hexdigest()[:12]
    assert catalog.doc_id("design", "/r/docs/design/f.md") == expected
    assert catalog.doc_id("model", "/r/docs/design/f.md") != expected
    assert catalog.doc_id("design", "/r/docs/design/g.md") != expected


def test_feature_documents_by_exact_slug(tmp_path):
    """Witness `f-old.md`, `f-25.md` and `.scratch/product-review/f-2/` beside slug `f` - distinguish
    an exact slug from a prefix (`startswith`/glob `f*`) match."""
    put(tmp_path, "docs/specs/f.md", SPEC)
    put(tmp_path, "docs/models/f.md")
    put(tmp_path, "docs/design/f.md")
    put(tmp_path, ".scratch/product-review/f/index.html")
    put(tmp_path, ".scratch/product-review/f/second.html")
    put(tmp_path, ".scratch/product-review/f/notes.txt")
    put(tmp_path, ".scratch/product-review/f/deeper/nested.html")
    put(tmp_path, "docs/design/f-old.md")
    put(tmp_path, "docs/models/f-25.md")
    put(tmp_path, ".scratch/product-review/f-2/x.html")
    put(tmp_path, "docs/specs/f-old.md", SPEC)
    docs = catalog.feature_documents("f", tmp_path)
    assert sorted((d.kind, d.name) for d in docs) == [
        ("design", "docs/design/f.md"), ("mockup", ".scratch/product-review/f/index.html"),
        ("mockup", ".scratch/product-review/f/second.html"), ("model", "docs/models/f.md"),
        ("spec-model", "docs/specs/f.md#Model")]
    for d in docs:
        assert d.id == catalog.doc_id(d.kind, d.path)
    assert {d.kind: d.path for d in docs if d.kind != "mockup"}["spec-model"] == str(
        tmp_path / "docs/specs/f.md")
    assert catalog.feature_documents("f", None) == []
    assert catalog.feature_documents("nothing", tmp_path) == []


def test_feature_documents_refuses_slugs_that_leave_the_tree(tmp_path):
    """Witness `.scratch/x.html`, `docs/{design,models}/a/b.md`, `docs/design/a\\b.md` and
    `.scratch/product-review/a/b/y.html` - distinguish a guarded slug (`..`, `.`, empty, `a/b`,
    `a\\b`) from an unguarded one, which would find those files."""
    tree = tmp_path / "tree"
    put(tree, ".scratch/x.html")
    put(tree, "docs/design/a/b.md")
    put(tree, "docs/models/a/b.md")
    put(tree, "docs/design/a\\b.md")
    put(tree, ".scratch/product-review/a/b/y.html")
    put(tree, "docs/design/ok.md")
    assert [d.name for d in catalog.feature_documents("ok", tree)] == ["docs/design/ok.md"]
    for slug in ("..", ".", "", "a/b", "a\\b"):
        assert catalog.feature_documents(slug, tree) == [], slug


def test_repo_documents_lists_every_kind(tmp_path):
    """Witness a `.txt` in docs/adr, a `.md` in product-review, an `.html` in docs/design, a nested
    adr and a file directly in product-review - distinguish 'each kind has its own extension and
    directory depth' from a blanket `*` or recursive glob; sorted by name, not by kind."""
    put(tmp_path, "docs/adr/0002-b.md")
    put(tmp_path, "docs/adr/0001-a.md")
    put(tmp_path, "docs/adr/notes.txt")
    put(tmp_path, "docs/adr/sub/0003.md")
    put(tmp_path, "docs/design/zeta.md")
    put(tmp_path, "docs/design/page.html")
    put(tmp_path, "docs/models/m.md")
    put(tmp_path, ".scratch/product-review/f/a.html")
    put(tmp_path, ".scratch/product-review/f/a.md")
    put(tmp_path, ".scratch/product-review/loose.html")
    put(tmp_path, "docs/specs/f.md", SPEC)
    docs = catalog.repo_documents(tmp_path)
    assert [(d.kind, d.name) for d in docs] == [
        ("mockup", ".scratch/product-review/f/a.html"),
        ("adr", "docs/adr/0001-a.md"), ("adr", "docs/adr/0002-b.md"),
        ("design", "docs/design/zeta.md"), ("model", "docs/models/m.md")]
    assert all(d.path == str(tmp_path / d.name) and d.id == catalog.doc_id(d.kind, d.path) for d in docs)
    assert catalog.repo_documents(tmp_path / "missing") == []


def test_spec_model_is_the_model_section(tmp_path):
    """Witness a `## Model` followed by `## Other section` with its own `### Not the model`, and a
    `## ` line inside a code fence - distinguish 'to the next level-2 heading' from 'to the next
    heading' (drops `### States`), 'to the end of the file' (keeps `secret`) and 'to the next
    `## ` line' without fences; a spec without the section lists no `spec-model`."""
    put(tmp_path, "docs/specs/f.md", SPEC)
    put(tmp_path, "docs/specs/g.md", "## Models\n\ntext\n\n## Model notes\n\nmore\n")
    put(tmp_path, "docs/specs/h.md", "## Model\n\nlast section, no heading after it\n")
    doc = only(catalog.feature_documents("f", tmp_path))
    body = catalog.document_body(doc).decode()
    assert body.startswith("## Model\n") and "### States" in body
    assert "## not a heading, it is inside a fence" in body
    assert "Other section" not in body and "secret" not in body and "Phase plan" not in body
    assert catalog.feature_documents("g", tmp_path) == []
    last = only(catalog.feature_documents("h", tmp_path))
    assert catalog.document_body(last) == b"## Model\n\nlast section, no heading after it\n"


def test_model_section_fences_follow_commonmark(tmp_path):
    """Witness a four-backtick fence holding a three-backtick mermaid block and a `## ` line, then a
    tilde fence holding a backtick line - distinguish a fence closed by any line of the same
    character from one closed only by a run at least as long, with no info string."""
    put(tmp_path, "docs/specs/f.md",
        "## Model\n\n````md\n```mermaid\nA --> B\n## inside\n```\n````\n\nafter fence\n\n"
        "~~~\n```\n## tilde inside\n~~~\n\n## Other\n\nsecret\n")
    body = catalog.document_body(only(catalog.feature_documents("f", tmp_path))).decode()
    assert "## inside" in body and "after fence" in body and "## tilde inside" in body
    assert "Other" not in body and "secret" not in body


def test_document_body_is_the_files_bytes_and_raises_oserror_when_gone(tmp_path):
    """Witness bytes that are not valid UTF-8 and a vanished file - distinguish 'the file's bytes'
    from a decoded-and-recoded text, and a swallowed error from an OSError (the server's 503)."""
    path = put(tmp_path, "docs/design/f.md")
    path.write_bytes(b"caf\xc3\xa9 \xff\n")
    doc = only(catalog.feature_documents("f", tmp_path))
    assert catalog.document_body(doc) == b"caf\xc3\xa9 \xff\n"
    path.unlink()
    with pytest.raises(OSError):
        catalog.document_body(doc)
    spec = put(tmp_path, "docs/specs/f.md", SPEC)
    model = only(catalog.feature_documents("f", tmp_path))
    spec.write_text("## Phase plan\n")       # the section vanished after the listing
    with pytest.raises(OSError):
        catalog.document_body(model)


def test_vendor_files_reads_names_and_hashes_from_the_manifest_rows(tmp_path):
    """Witness a manifest with a header row, a separator, a prose line mentioning a `.js` name and
    a non-js row - distinguish 'table rows whose name ends in .js' from 'every line' or a regex over
    the whole text."""
    digest = "a" * 64
    (tmp_path / "MANIFEST.md").write_text(
        "# Vendored\n\nSee x.js for details.\n\n"
        "| name | version | source URL | SHA-256 |\n|---|---|---|---|\n"
        f"| x.js | 1.0.0 | https://e/x.tgz `package/x.js` | {digest} |\n"
        f"| LICENSE | 1 | u | {'b' * 64} |\n")
    assert catalog.vendor_files(tmp_path) == {"x.js": digest}
    with pytest.raises(OSError):
        catalog.vendor_files(tmp_path / "nowhere")


# --- the workflow map (#29) -------------------------------------------------

PIPELINE_SKILLS = ("grill-with-docs", "domain-modeling", "to-spec", "uml-modeling", "product-review",
                   "system-architecture", "modular-monolith", "to-tickets", "program-design",
                   "implement-loop", "code-review", "ponytail-lean")


def stub_skills(tmp_path: Path) -> Path:
    """A store of stubs of the pipeline's skills, whoever's machine the test runs on (the map is read
    from whichever store the test is given)."""
    for name in PIPELINE_SKILLS:
        put(tmp_path / "skills", f"{name}/SKILL.md", f"# {name}\n")
    return tmp_path / "skills"


def install(tmp_path: Path) -> Path:
    """A workflow install of its own: the real scripts linked, the real prose copied, and a stub
    `LESSONS.md` (the real one is private and not part of an exported tree)."""
    wdir = tmp_path / "install"
    wdir.mkdir()
    for name in ("wf.py", "gen-flow.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    for name in ("WORKFLOW.md", "INVARIANTS.md", "REVIEW-CRITERIA.md", "SESSION-ISOLATION.md"):
        shutil.copy(ROOT / name, wdir / name)
    (wdir / "LESSONS.md").write_text("# Lessons\n\nstub\n")
    shutil.copytree(ROOT / "phases", wdir / "phases")
    return wdir


def bundle_names(text: str) -> list[str]:
    """The file names of the headers `wf.bundle` prints, in its order."""
    names = []
    for label in re.findall(r"^=== (.*) ===$", text, re.MULTILINE):
        if label.startswith("WORKFLOW.md §"):
            names.append("WORKFLOW.md")
        else:
            names.append(re.sub(r"^(phase file|skill \(referenced\)|skill|file): ", "", label))
    return names


def test_phase_zero_documents_list_lessons_only_when_the_file_exists(tmp_path):
    """Witness an install with and without `LESSONS.md` - distinguishes the window listing exactly
    the files `wf.phase0_files` names (the Lessons document between INVARIANTS.md and the phase-0
    file when it is there, no row when it is not) from a copied tuple that always lists it."""
    wdir = install(tmp_path)
    wf = traces.load_wf(wdir)
    names = [d.name for d in catalog.phase_documents(0, wdir, None, wf)]
    assert names[:4] == ["WORKFLOW.md", "SESSION-ISOLATION.md", "INVARIANTS.md", "LESSONS.md"]
    assert names[1:4] == list(wf.phase0_files(wdir))
    (wdir / "LESSONS.md").unlink()
    names = [d.name for d in catalog.phase_documents(0, wdir, None, wf)]
    assert "LESSONS.md" not in names and names[1:3] == list(wf.phase0_files(wdir))
    assert names[0] == "WORKFLOW.md" and names[3].startswith("phases/0-")


def test_rule_phases_reads_every_phase_named():
    """Witness `phase 6 (script) + phases 1, 3 (prompt)` - distinguishes a reader of every `phase
    N` / `phases N, M` in the cell, ascending, from one that stops at the first number (6) or
    drops the list after the comma (1, 6); `phase 5 + phase 6 (Spec axis)` two separate mentions;
    `General rules` and a file name hold no phase."""
    assert catalog.rule_phases("phase 6") == (6,)
    assert catalog.rule_phases("phase 5 + phase 6 (Spec axis)") == (5, 6)
    assert catalog.rule_phases("phase 6 (script) + phases 1, 3 (prompt)") == (1, 3, 6)
    assert catalog.rule_phases("General rules") == ()
    assert catalog.rule_phases("`INVARIANTS.md`") == ()
    assert catalog.rule_phases("this file, `phases/6-implement-loop.md`") == ()


def test_the_rules_attach_by_their_where_cell(tmp_path):
    """Witness the real install's 46 rules - distinguish the per-phase counts of P22 (0:1, 1:3,
    2:1, 3:2, 5:5, 6:14; phase 4 none) with 25 general ones from an attachment by rule text or by
    first number only (the `phases 1, 3` rule would leave phase 1 and 3 short)."""
    wf = traces.load_wf(ROOT)
    built = catalog.workflow_map(ROOT, stub_skills(tmp_path), wf, catalog.load_genflow(ROOT))
    assert [len(p["rules"]) for p in built["phases"]] == [1, 3, 1, 2, 0, 5, 14]
    assert len(built["general_rules"]) == 25
    rule = built["phases"][6]["rules"][0]
    assert set(rule) == {"text", "level", "by"} and rule["level"] in ("structure", "script", "prompt")


def test_map_follows_the_pipeline_table(tmp_path):
    """An install of its own (the real scripts and prose, a stub `LESSONS.md`; stub skills): for each
    phase 0-6 the names of `phase_documents` equal, in order, the headers `wf.bundle` prints. Then a
    temp install whose Pipeline table gains a skill. Witness: a skill
    added ONLY to the table (and a missing one) - distinguishes a map read from the table at each
    call from a copied list of skills, and a missing skill (named, no document) from a dropped one."""
    wdir = install(tmp_path)
    wf = traces.load_wf(wdir)
    skills = stub_skills(tmp_path)
    built = catalog.workflow_map(wdir, skills, wf, catalog.load_genflow(wdir))
    assert [p["n"] for p in built["phases"]] == [0, 1, 2, 3, 4, 5, 6]
    for phase in built["phases"]:
        n = phase["n"]
        assert [d.name for d in phase["documents"]] == bundle_names(wf.bundle(n, wdir, skills)), n
        assert phase["exit_check"] == inspect.getdoc(getattr(wf, f"check_phase{n}")), n
    kinds = {d.kind for p in built["phases"] for d in p["documents"]}
    assert kinds == {"workflow", "skill"}
    assert built["phases"][2]["condition"] == "only when the feature touches UI"
    assert built["phases"][2]["name"] == "Product review"
    assert built["phases"][2]["rules_file"] == "phases/2-product-review.md"

    table = (wdir / "WORKFLOW.md").read_text()
    assert "| `product-review` |" in table
    (wdir / "WORKFLOW.md").write_text(
        table.replace("| `product-review` |", "| `product-review`, `only-in-table`, `gone-skill` |"))
    put(tmp_path / "skills2", "product-review/SKILL.md", "# p\n")
    put(tmp_path / "skills2", "only-in-table/SKILL.md", "# o\n")
    wf2 = traces.load_wf(wdir)
    two = catalog.workflow_map(wdir, tmp_path / "skills2", wf2, catalog.load_genflow(wdir))
    phase2 = two["phases"][2]
    assert phase2["skills"] == ["product-review", "only-in-table", "gone-skill"]
    assert [d.name for d in phase2["documents"] if d.kind == "skill"] == ["product-review", "only-in-table"]
    assert [d.name for d in phase2["documents"]] == bundle_names(wf2.bundle(2, wdir, tmp_path / "skills2"))
    assert "only-in-table" not in built["phases"][2]["skills"]


# --- report-store #56: stored documents -------------------------------------

def test_stored_documents_get_distinct_ids_and_bodies_by_key_and_a_missing_item_is_an_oserror(tmp_path):
    """witness two stored reports of the same name under two repos and one store path, then a key
    the store does not hold, then a store file that is gone - distinguish an id from kind and path
    only (both would share one) from one that carries the key, and a body read by name only from
    one read by the whole key (`someone/else` rows would leak into the first); a missing item or
    store is an OSError (the server's 503), not an empty body."""
    report_store = load("report_store", ROOT / "report_store.py")
    store = tmp_path / "s" / "reports.sqlite"
    conn = report_store.open_store(store)
    for repo, item in (("a/a", "A1"), ("b/b", "B1")):
        row = {"repo": repo, "criterion": "c", "item_id": item, "verdict": "pass", "severity": "fatal",
               "reviewer": "spec", "actor": "spec-1", "claim": "coverage"}
        report_store.add_review_rows(conn, [row], "f", "f-spec", tmp_path, datetime(2026, 11, 17))
    conn.close()
    keys = [("review", "a/a", "f", "f-spec"), ("review", "b/b", "f", "f-spec")]
    docs = catalog.row_documents(SimpleNamespace(traces=[]), [("f-spec.jsonl", key) for key in keys], store)
    assert len({d.id for d in docs}) == 2
    assert [catalog.document_body(d, store).decode().count(item) for d, item in zip(docs, ("A1", "B1"))] == [1, 1]
    assert b"B1" not in catalog.document_body(docs[0], store)
    missing = catalog.Document("x", "report", "n.jsonl", str(store), ("review", "a/a", "f", "nope"))
    with pytest.raises(OSError):
        catalog.document_body(missing, store)
    store.unlink()
    with pytest.raises(OSError):
        catalog.document_body(docs[0], store)


def test_artefacts_go_to_the_phase_their_kind_belongs_to(tmp_path):
    """witness one document of each kind, adr and skill included - distinguishes the fixed
    phase mapping (in the kinds' order) from a pass-through of every document, and a kind in no
    phase must appear nowhere."""
    docs = {k: catalog.make(k, k, tmp_path / k) for k in
            ("adr", "design", "model", "mockup", "spec-model", "workflow", "skill", "spec",
             "handoff", "report")}
    kinds = {n: [d.kind for d in catalog.phase_artefacts(n, docs.values())] for n in range(7)}
    assert kinds == {0: ["spec"], 1: ["spec", "spec-model"], 2: ["mockup"],
                     3: ["spec", "spec-model"], 4: ["spec"], 5: ["design"],
                     6: ["spec", "model", "report", "handoff"]}
    assert catalog.phase_artefacts(7, docs.values()) == []
    assert catalog.phase_artefacts(6, [docs["handoff"], docs["model"]]) == [docs["model"], docs["handoff"]]


def test_the_artefacts_of_a_kind_keep_the_order_the_documents_came_in(tmp_path):
    first, second = (catalog.make("handoff", n, tmp_path / n) for n in ("b", "a"))
    assert catalog.phase_artefacts(6, [first, second]) == [first, second]


# --- #100 (gate-sections): the sections the gates contract, cut out of a feature's documents -------

wf_real = traces.load_wf(ROOT)
check_model_real = load("check_model_sections", ROOT / "guards" / "check_model.py")


def sections_of(n, docs):
    return catalog.phase_sections(n, docs, wf_real, check_model_real)


def feature_docs(tree: Path, slug: str, spec_text: str, model_text: str | None = None):
    """The spec (as a row lists it) and the model (as `feature_documents` lists it) of `slug`."""
    spec = put(tree, f"docs/specs/{slug}.md", spec_text)
    docs = [catalog.make("spec", str(spec), spec)]
    if model_text is not None:
        model = put(tree, f"docs/models/{slug}.md", model_text)
        docs.append(catalog.make("model", f"docs/models/{slug}.md", model))
    return docs


FIXTURE_SPEC = """\
---
feature: {slug}
---

## Phase plan

| Phase | Decision | Reason |
|---|---|---|
| 2 · Product review | skip | no UI |

## Problem Statement

Something is wrong.

## Solution

Fix it.

## User Stories

1. As an owner, I want it fixed, so that it works.

## Premises

| # | Premise | Command | Output | Verdict |
|---|---|---|---|---|
| P1 | The sky | `echo blue` | blue | confirmed |

## Model

### Lifecycle inventory

| Entity | Moves in time? |
|---|---|
| Thing | no |

## Tickets

- #9 one

## Delivery

- #9 — PR: https://example.com/pr/1
"""


def fixture_model(moving: bool) -> str:
    """A model document with the four required sections; with a moving entity in its inventory
    (so a `State machines` section is contracted) when `moving`."""
    return (f"---\nfeature: f\n---\n\n# Model\n\n## Lifecycle inventory\n\n"
            f"| Entity | Moves in time? | Statechart |\n|---|---|---|\n"
            f"| Thing | {'yes' if moving else 'no'} | {'required' if moving else '—'} |\n\n"
            "## Elements\n\n| ID | Kind | Code | Source |\n|---|---|---|---|\n| Thing | entity | pkg.Thing | pkg.py:1 |\n\n"
            "## Flow\n\ntext\n\n## Model changes\n\n| a |\n"
            + ("\n## State machines\n\ntext\n" if moving else ""))


def fixture_docs(tmp_path: Path, slug: str, moving: bool):
    """The spec (no Product review, no System Architecture) and model of `slug`, written into
    `tmp_path/docs`, as `feature_docs` lists them; `moving` puts a moving entity in the inventory."""
    docs_dir = tmp_path / "docs"
    spec = put(docs_dir, f"specs/{slug}.md", FIXTURE_SPEC.format(slug=slug))
    model = put(docs_dir, f"models/{slug}.md", fixture_model(moving))
    return [catalog.make("spec", str(spec), spec), catalog.make("model", f"docs/models/{slug}.md", model)]


def test_section_body_returns_the_body_of_the_exact_heading_and_none_when_absent():
    text = "## Tickets\n\n- #1 first\n\n## Tickets two\n\nother\n"
    body = catalog.section_body(text, "spec", "Tickets", wf_real, check_model_real)
    assert body == "\n- #1 first\n"
    assert catalog.section_body(text, "spec", "tickets", wf_real, check_model_real) is None
    assert catalog.section_body(text, "spec", "Delivery", wf_real, check_model_real) is None


def test_section_body_keeps_the_three_hash_subsections_of_a_spec_and_of_a_model():
    spec = "## Model\nintro\n### Lifecycle inventory\n| a |\n## Next\nx"
    body = catalog.section_body(spec, "spec", "Model", wf_real, check_model_real)
    assert body is not None
    assert "intro" in body and "### Lifecycle inventory" in body and "| a |" in body and "Next" not in body
    model = "## Elements\ne\n## Flow\nstart\n### Happy path\n| b |\n## Model changes\nz"
    flow = catalog.section_body(model, "model", "Flow", wf_real, check_model_real)
    assert flow == "start\n### Happy path\n| b |"


def test_document_body_of_a_model_section_is_cut_by_the_model_parser_not_the_spec_one(tmp_path):
    """witness a model whose heading is `## flow` (lower case): the spec parser is exact-case and
    finds nothing under `Flow`, the model parser lower-cases both sides."""
    path = put(tmp_path, "docs/models/m.md", "## Elements\ne\n## flow\nstart\n### Happy path\n| b |\n## Model changes\nz\n")
    doc = catalog.Document("s", "section", "m#Flow", str(path), section=("model", "Flow"))
    assert catalog.document_body(doc, parsers=(wf_real, check_model_real)) == b"start\n### Happy path\n| b |"


def test_section_body_ignores_a_heading_inside_a_code_fence():
    text = "## Plan\n\n```\n## Tickets\nin the fence\n```\n"
    assert catalog.section_body(text, "spec", "Tickets", wf_real, check_model_real) is None
    assert "in the fence" in (catalog.section_body(text, "spec", "Plan", wf_real, check_model_real) or "")


def test_section_body_of_a_model_matches_the_heading_case_insensitively():
    text = "## Model Changes\n\n| a |\n\n## Flow\n\nx\n"
    assert catalog.section_body(text, "model", "model changes", wf_real, check_model_real) == "\n| a |\n"
    assert catalog.section_body(text, "model", "Model changes", wf_real, check_model_real) == "\n| a |\n"
    assert catalog.section_body(text, "model", "Elements", wf_real, check_model_real) is None


def test_phase_sections_of_a_delivered_spec_lists_present_sections_in_the_gates_order_for_each_phase(tmp_path):
    """witness a fixture spec and model (phase 2 and part of 3 are skipped there, so
    `Product review` and `System Architecture` are absent) - distinguishes the gate's order and the
    per-phase list from one flat list of every heading."""
    docs = fixture_docs(tmp_path, "resting", moving=False)
    shown = {n: [(s.heading, s.state) for s in sections_of(n, docs)] for n in range(7)}
    assert shown[0] == [("Phase plan", "present")]
    assert shown[1] == [(h, "present") for h in
                        ("Problem Statement", "Solution", "User Stories", "Premises", "Model")]
    assert shown[2] == [("Product review", "missing")]
    assert shown[3] == [("System Architecture", "missing"), ("Model", "present")]
    assert shown[4] == [("Tickets", "present")]
    assert shown[5] == []
    assert shown[6] == [("Delivery", "present"), ("Lifecycle inventory", "present"),
                        ("Elements", "present"), ("Flow", "present"), ("Model changes", "present")]


def test_phase_sections_marks_a_removed_heading_missing_and_an_unreadable_file_unreadable_with_the_error(tmp_path):
    spec = "## Phase plan\n\nplan\n\n## Solution\n\ntext\n"
    docs = feature_docs(tmp_path, "f", spec)
    found = {s.heading: s for s in sections_of(1, docs)}
    assert [(s.state, s.doc is not None and s.doc.kind, s.rev is not None) for s in found.values()
            if s.heading == "Solution"] == [("present", "section", True)]
    assert [(s.state, s.doc, s.rev, s.note) for s in sections_of(4, docs)] == [("missing", None, None, None)]
    folder = tmp_path / "docs" / "specs" / "folder.md"
    folder.mkdir()
    broken = sections_of(4, [catalog.make("spec", str(folder), folder)])
    assert [(s.state, s.doc) for s in broken] == [("unreadable", None)]
    assert "Is a directory" in broken[0].note and str(folder) in broken[0].note


def test_phase_sections_at_phase_6_adds_state_machines_only_for_a_model_with_a_moving_entity(tmp_path):
    moving = [s.heading for s in sections_of(6, fixture_docs(tmp_path, "moving", moving=True))]
    resting = [s.heading for s in sections_of(6, fixture_docs(tmp_path, "resting", moving=False))]
    assert moving[-1:] == ["State machines"] and "State machines" not in resting
    assert moving[:1] == ["Delivery"]


def test_a_phase_with_no_source_document_has_no_sections(tmp_path):
    """witness a row with no spec among its documents, and a model-less row at phase 6: the spec
    headings of phase 6 are still listed (the spec is there), the model ones are not."""
    assert sections_of(4, []) == []
    docs = feature_docs(tmp_path, "f", "## Delivery\n\n- x\n")
    assert [s.heading for s in sections_of(6, docs)] == ["Delivery"]


def test_two_sections_of_one_file_have_two_document_ids(tmp_path):
    docs = feature_docs(tmp_path, "f", "## Problem Statement\n\na\n\n## Solution\n\nb\n")
    found = [s.doc for s in sections_of(1, docs) if s.state == "present"]
    assert len(found) == 2 and len({d.id for d in found}) == 2
    assert docs[0].id not in {d.id for d in found}


def test_document_body_of_a_section_is_the_cut_and_raises_oserror_when_the_heading_is_gone(tmp_path):
    docs = feature_docs(tmp_path, "f", "## Problem Statement\n\nfirst\n\n## Solution\n\nsecond\n")
    solutions = [s.doc for s in sections_of(1, docs) if s.heading == "Solution"]
    assert len(solutions) == 1, "the Solution section is not listed"
    solution = solutions[0]
    assert catalog.document_body(solution, parsers=(wf_real, check_model_real)) == b"\nsecond"
    Path(docs[0].path).write_text("## Problem Statement\n\nfirst\n")
    with pytest.raises(OSError, match="Solution"):
        catalog.document_body(solution, parsers=(wf_real, check_model_real))


def test_rev_changes_when_the_body_changes_and_not_when_another_section_does(tmp_path):
    docs = feature_docs(tmp_path, "f", "## Problem Statement\n\na\n\n## Solution\n\nb\n")
    before = {s.heading: s.rev for s in sections_of(1, docs)}
    assert set(before) >= {"Problem Statement", "Solution"}, before
    Path(docs[0].path).write_text("## Problem Statement\n\na\n\n## Solution\n\nb changed\n")
    after = {s.heading: s.rev for s in sections_of(1, docs)}
    assert after["Problem Statement"] == before["Problem Statement"] is not None
    assert after["Solution"] != before["Solution"]


def test_load_check_model_loads_the_guard_by_path_and_writes_no_bytecode():
    flag = sys.dont_write_bytecode
    module = catalog.load_check_model(ROOT)
    assert callable(getattr(module, "contracted_sections", None))
    assert module.REQUIRED_SECTIONS[0] == "lifecycle inventory"
    assert sys.dont_write_bytecode == flag


# --- #127 (design-diagrams): the Mermaid blocks of a phase's documents, with their heading path --------

def mermaid(body: str) -> str:
    return f"```mermaid\n{body}\n```\n"


DIAGRAM_SPEC = (
    "# Spec title\n\n## Phase plan\n\n" + mermaid("flowchart LR\n  P --> Q") + "\n"
    "## Model\n\n### Flow\n\n" + mermaid("flowchart LR\n  A --> B") + "\n"
    "```python\n## Not a heading\nprint('x')\n```\n\n"
    "### State machines\n\n#### Fold\n\n" + mermaid("stateDiagram-v2\n  [*] --> Closed") + "\n"
    "### Sequence\n\n" + mermaid("sequenceDiagram\n  A->>B: hi") + "\n"
    "## System Architecture\n\n" + mermaid("flowchart TD\n  S --> T") + "\n"
    "## Tickets\n\n" + mermaid("flowchart LR\n  X --> Y") + "\n")


def diagram_docs(tree: Path, spec_text: str = DIAGRAM_SPEC, model_text: str | None = None):
    return feature_docs(tree, "f", spec_text, model_text)


def test_mermaid_blocks_carry_their_heading_path_and_skip_other_fences():
    """witness a spec with a level-1 title, six blocks under six heading stacks (the title is in none), a python fence
    holding a `## Not a heading` line before a block - distinguishes a heading-aware scan (level 1 left
    out, a deeper heading replaced by a sibling, a fence of another language unable to open a heading)
    from a naive one that would caption the block with `Not a heading` or keep the title; and a `~~~` fence is
    a block too, a title-only spec has none."""
    found = catalog.mermaid_blocks_with_headings(DIAGRAM_SPEC)
    assert [path for path, _ in found] == [
        ("Phase plan",), ("Model", "Flow"), ("Model", "State machines", "Fold"), ("Model", "Sequence"),
        ("System Architecture",), ("Tickets",)]
    assert found[1][1] == "flowchart LR\n  A --> B"
    assert catalog.mermaid_blocks_with_headings("# Only a title\n\n```json\n{}\n```\n") == []
    tilde = "## Model\n\n~~~mermaid\nflowchart LR\n  A --> B\n~~~\n"
    assert catalog.mermaid_blocks_with_headings(tilde) == [(("Model",), "flowchart LR\n  A --> B")]


def test_phase_diagrams_list_every_mermaid_block_in_order(tmp_path):
    """witness a `## Model` with 3 blocks under 3 different headings plus a non-mermaid fence that
    contains a `## ` line - distinguishes a heading-aware scan (3 diagrams, each captioned with its own
    path) from a naive one (a block lost, reordered or captioned with another's heading)."""
    docs = diagram_docs(tmp_path)
    found = catalog.phase_diagrams(1, docs)
    assert [d.title for d in found] == ["Model › Flow", "Model › State machines › Fold", "Model › Sequence"]
    assert [d.mermaid.splitlines()[0] for d in found] == ["flowchart LR", "stateDiagram-v2", "sequenceDiagram"]
    assert [d.source for d in found] == ["docs/specs/f.md § Model"] * 3
    assert all(d.doc == docs[0] and d.generated is None for d in found)


def test_phase_one_takes_only_the_model_and_phase_three_also_the_architecture(tmp_path):
    """witness a spec with a block under `## System Architecture` and others under `## Phase plan` and
    `## Tickets` - distinguishes DIAGRAM_SECTIONS (phase 1: Model only; phase 3: Model then System
    Architecture, in document order; the other sections never) from taking every block of the spec."""
    docs = diagram_docs(tmp_path)
    one = catalog.phase_diagrams(1, docs)
    three = catalog.phase_diagrams(3, docs)
    assert "System Architecture" not in " ".join(d.title for d in one)
    assert [d.title for d in three] == [*(d.title for d in one), "System Architecture"]
    assert three[-1].source == "docs/specs/f.md § System Architecture"
    assert not any(d.title in ("Phase plan", "Tickets") for d in three)
    assert catalog.DIAGRAM_SECTIONS == {1: ("Model",), 3: ("Model", "System Architecture"), 6: ()}


def test_phase_six_takes_the_as_built_model(tmp_path):
    """witness a model whose blocks sit under `## Flow` and `### State machines`, beside a spec with a
    block of its own - distinguishes phase 6 reading the first `model` whole (the spec's blocks left out)
    from reading the spec."""
    model_text = ("# Model\n\n## Flow\n\n" + mermaid("flowchart LR\n  M --> N") + "\n"
                  "## State machines\n\n### Fold\n\n" + mermaid("stateDiagram-v2\n  [*] --> Open"))
    docs = diagram_docs(tmp_path, model_text=model_text)
    found = catalog.phase_diagrams(6, docs)
    assert [(d.title, d.source) for d in found] == [
        ("Flow", "docs/models/f.md § Flow"), ("State machines › Fold", "docs/models/f.md § State machines")]
    assert all(d.doc is not None and d.doc.kind == "model" for d in found)
    assert catalog.phase_diagrams(6, diagram_docs(tmp_path)) == []


def test_other_phases_and_an_unreadable_file_give_no_diagram(tmp_path):
    """witness phases 0, 2, 4, 5 and a spec that is a directory - distinguishes `[]` (no raise) for a
    phase the table does not draw and for an unreadable file, from a raise or a diagram of another phase."""
    docs = diagram_docs(tmp_path)
    assert [catalog.phase_diagrams(n, docs) for n in (0, 2, 4, 5)] == [[], [], [], []]
    assert catalog.phase_diagrams(1, []) == []
    folder = tmp_path / "docs" / "specs" / "folder.md"
    folder.mkdir()
    assert catalog.phase_diagrams(1, [catalog.make("spec", str(folder), folder)]) == []
    gone = catalog.make("model", "docs/models/gone.md", tmp_path / "gone.md")
    assert catalog.phase_diagrams(6, [gone]) == []


def test_text_lines_of_a_document_a_section_and_a_stored_report(tmp_path):
    """witness a 3-line file, the `Solution` section of a spec (2 lines) cut by the gate's parsers, a
    stored report, and a file that is gone - distinguishes the length of the fold's text (a section's own
    body, not its file) from the file's, and `None` (not 0, not a raise) where there is no length."""
    spec = put(tmp_path, "docs/specs/f.md", "## Problem Statement\n\na\n\n## Solution\n\nb\nc\n")
    plain = catalog.make("spec", str(spec), spec)
    assert catalog.text_lines(plain) == 8
    sections = {s.heading: s for s in sections_of(1, [plain]) if s.doc}
    solution = sections["Solution"].doc
    assert catalog.text_lines(solution, (wf_real, check_model_real)) == 3   # "", "b", "c"
    report = catalog.Document("r", "report", "m.jsonl", str(tmp_path / "store.db"), ("matrix", "m"))
    assert catalog.text_lines(report) is None
    assert catalog.text_lines(catalog.make("spec", "g", tmp_path / "gone.md")) is None


# --- #128 (design-diagrams): the diagrams generated from a design's `json design` block ----------------

def design_text(design: dict | str, hand_written: str = "") -> str:
    block = design if isinstance(design, str) else json.dumps(design, indent=1)
    return f"# Design\n\n## Notes\n\n{hand_written}\n## JSON design\n\n```json design\n{block}\n```\n"


def design_docs(tree: Path, text: str):
    path = put(tree, "docs/design/f.md", text)
    return [catalog.make("design", "docs/design/f.md", path)]


def flow_parts(source: str):
    """(nodes {index: id}, edges [(from index, to index)], classes {index: kind}, classDefs {kind: style})"""
    lines = source.splitlines()
    nodes = {int(m[1]): m[2] for line in lines if (m := re.fullmatch(r'\s*n(\d+)\["(.*)"\]', line))}
    edges = [(int(m[1]), int(m[2])) for line in lines if (m := re.fullmatch(r"\s*n(\d+) --> n(\d+)", line))]
    classes = {int(m[1]): m[2] for line in lines if (m := re.fullmatch(r"\s*class n(\d+) (\w+)", line))}
    defs = {m[1]: m[2] for line in lines if (m := re.fullmatch(r"\s*classDef (\w+) (.+)", line))}
    return nodes, edges, classes, defs


FLOW_DESIGN = {
    "feature": "f",
    "call_stack": {"name": "entry", "children": [
        {"name": "a.b.start", "children": [{"name": "x.mid", "children": []}, {"name": "y.end", "children": []}]},
        {"name": "z.other", "children": []}]},
    "data_flow": {
        "nodes": [{"id": "a.b.start", "kind": "add"}, {"id": "x.mid", "kind": "mod"}, {"id": "y.end", "kind": "keep"}],
        "edges": [{"from": "a.b.start", "to": "x.mid"}, {"from": "x.mid", "to": "y.end"},
                  {"from": "y.end", "to": "lib.ghost[0]"}]}}


def test_generated_data_flow_matches_the_json_design():
    """witness a design with an `add`, a `mod` and a `keep` node whose ids hold dots and brackets, 3 edges, the last
    to an id that is no node - distinguishes a flowchart that names exactly the nodes and edges of the design (ids
    only inside quoted labels, `n<i>` as Mermaid ids, a ghost node for the unknown id, each node classed by its
    kind, a classDef per kind present and none for a kind that is absent, no colour) from one that swaps a kind,
    drops the ghost edge or lets a dot into a Mermaid id."""
    source = catalog.data_flow_mermaid(FLOW_DESIGN)
    assert source.splitlines()[0] == "flowchart LR"
    nodes, edges, classes, defs = flow_parts(source)
    assert nodes == {0: "a.b.start", 1: "x.mid", 2: "y.end", 3: "lib.ghost[0]"}
    assert edges == [(0, 1), (1, 2), (2, 3)]
    assert classes == {0: "add", 1: "mod", 2: "keep", 3: "ghost"}
    assert set(defs) == {"add", "mod", "keep", "ghost"}
    assert all("stroke-width:1.5px" in style and "#" not in style and "fill" not in style for style in defs.values())
    only_keep = {"data_flow": {"nodes": [{"id": "k", "kind": "keep"}], "edges": []}}
    kinds = {"data_flow": {"nodes": [{"id": "n", "kind": "new"}, {"id": "w", "kind": "weird"}, {"id": "u"}], "edges": []}}
    assert flow_parts(catalog.data_flow_mermaid(kinds))[2] == {0: "add", 1: "keep", 2: "keep"}   # Decision 4: `new` is drawn as `add`
    assert flow_parts(catalog.call_stack_mermaid({**kinds, "call_stack": {"name": "n"}}))[2] == {0: "add"}
    assert set(flow_parts(catalog.data_flow_mermaid(only_keep))[3]) == {"keep"}
    odd = {"data_flow": {"nodes": [{"id": 'say "hi"; # <b>', "kind": "add"}], "edges": []}}
    label = [line for line in catalog.data_flow_mermaid(odd).splitlines() if line.strip().startswith("n0[")][0]
    inner = re.sub(r"#\w+;", "", label.strip()[4:-2])   # what is left once the entities are taken out
    assert not set('";<>#') & set(inner) and "#34;" in label and "#59;" in label and "#35;" in label


def test_call_stack_mermaid_follows_children_and_classes():
    """witness a tree of 5 calls (one repeated nowhere, one with two children in a given order) and a data_flow
    that classes 3 of them - distinguishes a top-down flowchart with an edge from each parent to its children in
    order, each node classed like the data_flow node of the same id and `keep` when it has none, from a flat
    list, a reordered tree or a class taken from the wrong node."""
    source = catalog.call_stack_mermaid(FLOW_DESIGN)
    assert source.splitlines()[0] == "flowchart TD"
    nodes, edges, classes, defs = flow_parts(source)
    assert nodes == {0: "entry", 1: "a.b.start", 2: "x.mid", 3: "y.end", 4: "z.other"}
    assert [(nodes[a], nodes[b]) for a, b in edges] == [
        ("entry", "a.b.start"), ("a.b.start", "x.mid"), ("a.b.start", "y.end"), ("entry", "z.other")]
    assert classes == {0: "keep", 1: "add", 2: "mod", 3: "keep", 4: "keep"}
    assert set(defs) == {"add", "mod", "keep"}


def test_phase_five_puts_generated_before_hand_written(tmp_path):
    """witness a design with two hand-written blocks under two headings and a json design - distinguishes phase 5
    (the Data flow, then the Call stack, both generated with the design's path and no document, then the
    hand-written ones in document order, whose `doc` is the design) from phases that give none, or an order that puts
    the hand-written first; and a design with no json block gives only its hand-written blocks."""
    hand = ("### Flow\n\n" + mermaid("flowchart LR\n  A --> B") + "\n### Order\n\n" + mermaid("sequenceDiagram\n  A->>B: x") + "\n")
    docs = design_docs(tmp_path, design_text(FLOW_DESIGN, hand))
    found = catalog.phase_diagrams(5, docs)
    assert [d.title for d in found] == ["Data flow (generated)", "Call stack (generated)", "Notes › Flow", "Notes › Order"]
    assert [d.generated for d in found] == ["data_flow", "call_stack", None, None]
    assert [d.source for d in found[:2]] == ["docs/design/f.md § json design"] * 2
    assert [d.doc for d in found] == [None, None, docs[0], docs[0]]
    assert found[0].mermaid == catalog.data_flow_mermaid(FLOW_DESIGN)
    assert found[1].mermaid == catalog.call_stack_mermaid(FLOW_DESIGN)
    assert found[2].mermaid == "flowchart LR\n  A --> B"


def test_a_design_without_a_json_block_gives_only_its_hand_written_blocks(tmp_path):
    """witness a design with one hand-written block and no json block - distinguishes phase 5 giving just that block
    (and `design_block` giving None) from a failed card or generated diagrams made of nothing."""
    plain = design_docs(tmp_path, "# D\n\n## Notes\n\n" + mermaid("flowchart LR\n  A --> B"))
    assert [d.title for d in catalog.phase_diagrams(5, plain)] == ["Notes"]
    assert catalog.design_block("# no block here\n") is None
    assert catalog.design_block(design_text(FLOW_DESIGN))["feature"] == "f"


def test_design_block_reads_the_same_json_as_the_design_guard_on_every_fixture_design(tmp_path):
    """witness three designs written into tmp_path (a json block, no block, an unclosed fence) -
    distinguishes a catalog fence pattern equal in behaviour to the guard's `load_design` (same dict
    where there is a block, None where the guard exits) from one that drifted."""
    check_design = load("check_design_for_blocks", ROOT / "guards" / "check_design.py")
    texts = {
        "block.md": '# d\n\n## Shape\n\n```json design\n{"feature": "f", "call_stack": {}}\n```\n',
        "none.md": "# d\n\n## Notes\n\nno block here\n",
        "unclosed.md": '# d\n\n```json design\n{"feature": "f"}\n',
    }
    for name, text in texts.items():
        path = put(tmp_path, f"docs/design/{name}", text)
        try:
            expected = check_design.load_design(path)
        except SystemExit:
            expected = None
        assert catalog.design_block(path.read_text(encoding="utf-8")) == expected, name
    assert catalog.design_block(texts["block.md"]) == {"feature": "f", "call_stack": {}}


def test_design_block_stops_at_the_first_closing_fence_like_the_design_guard_when_another_fence_follows(tmp_path):
    """witness a design whose json block is followed by prose and a mermaid fence - distinguishes a catalog fence
    pattern that stops at the first closing fence (the guard's dict) from a greedy one (a ValueError or other dict)."""
    check_design = load("check_design_for_trailing_fence", ROOT / "guards" / "check_design.py")
    path = tmp_path / "f.md"
    path.write_text('# d\n\n```json design\n{"feature": "f"}\n```\n\nmore text\n\n```mermaid\nflowchart LR\n  A --> B\n```\n',
                    encoding="utf-8")
    assert check_design.load_design(path) == {"feature": "f"}
    assert catalog.design_block(path.read_text(encoding="utf-8")) == {"feature": "f"}


def test_a_malformed_json_design_is_one_failed_diagram(tmp_path):
    """witness a design whose json block is cut short, then one whose data_flow is a string - distinguishes one
    Diagram with an empty `mermaid` and the error in its title (the page draws it as a failed card), the
    hand-written blocks still following, from a raise out of the stage, two failed cards, or an empty box."""
    broken = design_docs(tmp_path, design_text('{"feature": "f", "data_flow": ', mermaid("flowchart LR\n  A --> B")))
    found = catalog.phase_diagrams(5, broken)
    assert [d.mermaid for d in found] == ["", "flowchart LR\n  A --> B"]
    assert found[0].generated is None and found[0].doc is None
    assert found[0].title.startswith("json design") and "Expecting" in found[0].title
    assert found[0].source == "docs/design/f.md § json design"
    with pytest.raises(ValueError):
        catalog.design_block(design_text('{"feature": '))
    wrong = design_docs(tmp_path, design_text({"data_flow": "oops"}))
    found = catalog.phase_diagrams(5, wrong)
    assert len(found) == 1 and found[0].mermaid == "" and found[0].title.startswith("json design")


# --- ticket 129 (design-diagrams): the invariants of a spec, with a live status from the matrices ---------

report_store = load("report_store", ROOT / "report_store.py")

INVARIANT_HEADER = "| Invariant (law) | Negation → test | Verified in |\n|---|---|---|\n"


def invariant_spec(*rows: tuple[str, str, str], header: str = INVARIANT_HEADER, after: str = "") -> str:
    body = "".join(f"| {law} | {negation} | {verified} |\n" for law, negation, verified in rows)
    return f"# T\n\n## System Architecture\n\ntext\n\n### System invariants\n\n{header}{body}\n{after}## Tickets\n\nx\n"


def invariants_of(tree: Path, text: str, rows=(), n: int = 3):
    return catalog.phase_invariants(n, feature_docs(tree, "f", text), tree, rows)


def mrow(test: str, outcome: str = "pinned", matrix: str = "m1", at: str = "2026-11-20T10:00:00",
         file: str | None = "app/x.py", mutant: str = "M1", kind: str = "mutant"):
    return catalog.MutantRow(matrix, at, mutant, kind, test, outcome, file)


def with_test(tree: Path, rel: str = "tests/test_a.py", text: str = "def test_one():\n    pass\n", source: str = "app/x.py"):
    put(tree, rel, text)
    put(tree, source, "x = 1\n")


def test_normalised_tests_strip_markers_params_and_resolve_shorthand():
    """witness cells with backticks, a `(to write)` and a `(da scrivere …)` marker, a `[param]` suffix, a class
    id, a `…::name` and a `::name` shorthand after a full id, and cells that name no test - distinguishes
    ids cleaned and in order, the shorthand taking the path of the id before it, from raw cell text,
    a kept marker or param, a shorthand with no path before it, or a script/file/prose read as a test."""
    n = catalog.normalised_tests
    assert n("`tests/app/test_a.py::test_x` (to write)") == ("tests/app/test_a.py::test_x",)
    assert n("tests/a.py::test_x[1-2] (da scrivere nel ticket 5)") == ("tests/a.py::test_x",)
    assert n("`tests/a.py::TestK::test_x`") == ("tests/a.py::TestK::test_x",)
    assert n("`tests/a.py::test_x`, `…::test_y` and `::test_z`") == (
        "tests/a.py::test_x", "tests/a.py::test_y", "tests/a.py::test_z")
    assert n("`tests/a.py::test_x`; `tests/b.py::test_y`, `::test_z`") == (
        "tests/a.py::test_x", "tests/b.py::test_y", "tests/b.py::test_z")
    assert n("`::test_orphan`") == ()
    for cell in ("", "`scripts/check_x.py`", "`audit.py check_cache`", "prose only", "tests/a.py"):
        assert n(cell) == (), cell


def test_invariant_table_reads_columns_by_meaning():
    """witness three header shapes of the real specs (the 3-column one, one with the columns reordered and
    renamed, one with no `Verified in`) and a table after other text - distinguishes columns read by the
    words law / negation / verified (any order, any extra column) from fixed positions, and the first
    table after the heading from a later one; a row's cells come back as written."""
    text = invariant_spec(("Law one", "neg one → `tests/a.py::test_x`", "`tests/a.py::test_x` (to write)"),
                          ("Law two", "neg two", "`scripts/c.py`"))
    table = catalog.invariant_table(text, "docs/specs/f.md")
    assert table.readable and table.source == "docs/specs/f.md" and table.raw is None and table.why is None
    assert [(r.n, r.law, r.negation, r.verified, r.tests, r.status, r.evidence) for r in table.rows] == [
        (1, "Law one", "neg one → `tests/a.py::test_x`", "`tests/a.py::test_x` (to write)",
         ("tests/a.py::test_x",), "not-checkable", ()),
        (2, "Law two", "neg two", "`scripts/c.py`", (), "not-checkable", ())]
    shuffled = catalog.invariant_table(
        "### System invariants\n\n| Where | Verified in | Negation | Law |\n|---|---|---|---|\n"
        "| w | `a.py::test_q` | the negation | the law |\n\n## Next\n", "s")
    assert [(r.law, r.negation, r.verified, r.tests) for r in shuffled.rows] == [
        ("the law", "the negation", "`a.py::test_q`", ("a.py::test_q",))]
    two = catalog.invariant_table("### System invariants\n\n| Law | Negation → test |\n|---|---|\n| L | N |\n", "s")
    assert two.readable and [(r.law, r.negation, r.verified, r.tests) for r in two.rows] == [("L", "N", "", ())]
    assert catalog.invariant_table("## Model\n\n| Law | Negation |\n|---|---|\n| a | b |\n", "s") is None
    assert catalog.invariant_table("### System invariants\n\nNo table here.\n\n## Next\n", "s") is None
    later = "### System invariants\n\nno table\n\n## Tickets\n\n| Law | Negation |\n|---|---|\n| a | b |\n"
    assert catalog.invariant_table(later, "s") is None


def test_an_unreadable_invariants_table_is_returned_raw():
    """witness a `| Module | Owns | May not |` table under the heading and a spec file that cannot be read -
    distinguishes `readable=False` with the table's own lines as `raw` and a reason (rows empty, nothing
    invented, never dropped to None) from a parsed or missing table; an OSError becomes an unreadable table
    that carries the error text."""
    text = ("### System invariants\n\n| Module | Owns | May not |\n|---|---|---|\n"
            "| server | routes | write |\n\n## Next\n")
    table = catalog.invariant_table(text, "docs/specs/f.md")
    assert table.readable is False and table.rows == ()
    assert table.raw == "| Module | Owns | May not |\n|---|---|---|\n| server | routes | write |"
    assert "law" in table.why and "negation" in table.why and "Module" in table.why
    assert table.source == "docs/specs/f.md"


def test_an_unreadable_spec_is_an_unreadable_invariants_table(tmp_path):
    """witness a spec path that is a directory - distinguishes an unreadable table (no rows, no raw text, the
    OSError text in `why`) from None (no table) or a crash."""
    spec = tmp_path / "docs" / "specs" / "f.md"
    spec.mkdir(parents=True)
    docs = [catalog.make("spec", "docs/specs/f.md", spec)]
    table = catalog.phase_invariants(3, docs, tmp_path, ())
    assert table.readable is False and table.rows == () and table.raw is None
    assert "Is a directory" in table.why and table.source == "docs/specs/f.md"


def test_a_cell_without_a_test_is_not_checkable(tmp_path):
    """witness a `Verified in` of `scripts/check_x.py`, one of prose and one empty, with a matrix row that
    mutates a real file for another test - distinguishes `not-checkable` with the cell text kept and no
    evidence from any other status or an altered cell."""
    with_test(tmp_path)
    text = invariant_spec(("L1", "n", "`scripts/check_x.py`"), ("L2", "n", "by reading the code"), ("L3", "n", ""))
    table = invariants_of(tmp_path, text, [mrow("tests/test_a.py::test_one")])
    assert [(r.status, r.verified, r.evidence) for r in table.rows] == [
        ("not-checkable", "`scripts/check_x.py`", ()), ("not-checkable", "by reading the code", ()),
        ("not-checkable", "", ())]


def test_invariant_status_takes_the_newest_counting_matrix(tmp_path):
    """witness a test pinned in an old matrix and blind in a newer one, a test blind then pinned, a test
    pinned only by a row whose mutated file is not in the tree, and a test with a [param] in the matrix -
    distinguishes the newest counting matrix deciding (blind, pinned) and a row off the tree not counting
    (it must not make the older pin newest), from first-seen order or a pin that survives a newer blind;
    the evidence names the deciding matrix and mutant."""
    with_test(tmp_path, text="def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n"
                             "def test_four(): pass\n")
    rows = [mrow("tests/test_a.py::test_one", "pinned", "old", "2026-11-01T00:00:00"),
            mrow("tests/test_a.py::test_one", "blind-test", "new", "2026-11-10T00:00:00", mutant="M7"),
            mrow("tests/test_a.py::test_two", "blind-test", "old", "2026-11-01T00:00:00"),
            mrow("tests/test_a.py::test_two", "pinned", "new", "2026-11-10T00:00:00", mutant="M8"),
            mrow("tests/test_a.py::test_three", "pinned", "older", "2026-11-01T00:00:00"),
            mrow("tests/test_a.py::test_three", "blind-test", "newer-elsewhere", "2026-11-12T00:00:00",
                 file="other/repo.py"),
            mrow("tests/test_a.py::test_four[x-1]", "pinned", "param", "2026-11-05T00:00:00", mutant="M9")]
    text = invariant_spec(*[(f"L{i}", "n", f"`tests/test_a.py::test_{w}`") for i, w in enumerate(
        ("one", "two", "three", "four"), 1)])
    table = invariants_of(tmp_path, text, rows)
    assert [r.status for r in table.rows] == ["blind", "pinned", "pinned", "pinned"]
    assert table.rows[0].evidence == ({"test": "tests/test_a.py::test_one", "matrix": "new", "mutant": "M7",
                                       "outcome": "blind-test", "at": "2026-11-10T00:00:00"},)
    assert table.rows[1].evidence[0]["matrix"] == "new" and table.rows[1].evidence[0]["mutant"] == "M8"
    assert table.rows[2].evidence[0]["matrix"] == "older"
    assert table.rows[3].evidence[0]["matrix"] == "param"


def test_invariant_status_a_blind_row_beats_a_pinned_one_in_the_same_newest_matrix(tmp_path):
    """witness one newest matrix holding for the same test a pinned row and a blind-test row that both count, in
    both insertion orders, above an older all-pinned matrix - distinguishes any blind row of the newest matrix
    giving blind (evidence names the blind row) from first-row or last-row picking, where the pinned row hides it."""
    with_test(tmp_path, text="def test_one(): pass\ndef test_two(): pass\n")
    new = "2026-11-10T00:00:00"
    rows = [mrow("tests/test_a.py::test_one", "pinned", "old", "2026-11-01T00:00:00", mutant="M0"),
            mrow("tests/test_a.py::test_one", "pinned", "new", new, mutant="M1"),
            mrow("tests/test_a.py::test_one", "blind-test", "new", new, mutant="M2"),
            mrow("tests/test_a.py::test_two", "blind-test", "new", new, mutant="M3"),
            mrow("tests/test_a.py::test_two", "pinned", "new", new, mutant="M4")]
    text = invariant_spec(("L1", "n", "`tests/test_a.py::test_one`"), ("L2", "n", "`tests/test_a.py::test_two`"))
    table = invariants_of(tmp_path, text, rows)
    assert [r.status for r in table.rows] == ["blind", "blind"]
    assert [r.evidence[0]["mutant"] for r in table.rows] == ["M2", "M3"]
    assert {r.evidence[0]["outcome"] for r in table.rows} == {"blind-test"}


def test_a_matrix_of_another_repo_does_not_count(tmp_path):
    """witness the same test id pinned by a row whose diff mutates a file absent from the tree (and a row
    with no file at all) - distinguishes `never-mutated` (the test exists, no row of this repo targets it)
    from `pinned` borrowed from a fork's matrix."""
    with_test(tmp_path)
    text = invariant_spec(("L", "n", "`tests/test_a.py::test_one`"))
    foreign = [mrow("tests/test_a.py::test_one", file="somewhere/else.py"),
               mrow("tests/test_a.py::test_one", file=None, matrix="m2")]
    assert [r.status for r in invariants_of(tmp_path, text, foreign).rows] == ["never-mutated"]
    own = [*foreign, mrow("tests/test_a.py::test_one", matrix="m3", at="2026-11-01T00:00:00")]
    assert [r.status for r in invariants_of(tmp_path, text, own).rows] == ["pinned"]


def test_missing_and_never_mutated_follow_the_tree(tmp_path):
    """witness a test with a `def` and no matrix row, a test with a `class`, an `async def`, a test whose
    name is only in a comment, a test in a file that is not there - distinguishes `never-mutated` for a
    definition at line start (def, async def, class, indented), from `test-missing` for no definition (a
    comment, a longer name, a missing file). The two outcomes must not be swapped."""
    put(tmp_path, "tests/test_a.py", "def test_def(): pass\nclass TestK:\n    async def test_async(self): pass\n"
                                     "# def test_comment(): pass\ndef test_defined_longer(): pass\n")
    ids = ["test_def", "TestK", "test_async", "test_comment", "test_defined", "test_gone"]
    cells = [f"`tests/test_a.py::{i}`" for i in ids] + ["`tests/nope.py::test_def`"]
    table = invariants_of(tmp_path, invariant_spec(*[(f"L{k}", "n", c) for k, c in enumerate(cells)]), [])
    assert [r.status for r in table.rows] == ["never-mutated", "never-mutated", "never-mutated",
                                              "test-missing", "test-missing", "test-missing", "test-missing"]
    assert table.rows[3].evidence == ({"test": "tests/test_a.py::test_comment", "matrix": None, "mutant": None,
                                       "outcome": None, "at": None},)
    assert catalog.defines_test(tmp_path, "tests/test_a.py::TestK::test_async") is True
    assert catalog.defines_test(tmp_path / "gone", "tests/test_a.py::test_def") is False


def test_an_invariant_takes_the_worst_status_of_its_tests(tmp_path):
    """witness one invariant naming a pinned, a blind, a never-mutated and a test-missing test in turn -
    distinguishes the order blind > test-missing > never-mutated > pinned (each pair of the chain) from
    the first test's status, the best, or the alphabetical one; the evidence has one entry per test."""
    with_test(tmp_path, text="def test_p(): pass\ndef test_b(): pass\ndef test_n(): pass\n")
    rows = [mrow("tests/test_a.py::test_p"), mrow("tests/test_a.py::test_b", "blind-test")]

    def status(*names: str) -> str:
        cell = ", ".join(f"`tests/test_a.py::{x}`" for x in names)
        return invariants_of(tmp_path, invariant_spec(("L", "n", cell)), rows).rows[0].status

    assert status("test_p", "test_n") == "never-mutated"
    assert status("test_n", "test_p") == "never-mutated"
    assert status("test_p", "test_n", "test_gone") == "test-missing"
    assert status("test_gone", "test_n") == "test-missing"
    assert status("test_gone", "test_b", "test_p", "test_n") == "blind"
    assert status("test_p", "test_p") == "pinned"
    full = invariants_of(tmp_path, invariant_spec(("L", "n", "`tests/test_a.py::test_p`, `::test_b`, `::test_n`")), rows)
    assert [e["test"].split("::")[1] for e in full.rows[0].evidence] == ["test_p", "test_b", "test_n"]


def test_phase_invariants_only_in_phases_3_and_6_and_with_a_table(tmp_path):
    """witness a spec with a table asked for phases 0-6, and a spec without the heading - distinguishes the
    table for phases 3 and 6 only (None for the others, and for a spec without the heading or without any spec)."""
    with_test(tmp_path)
    text = invariant_spec(("L", "n", "`tests/test_a.py::test_one`"))
    docs = feature_docs(tmp_path, "f", text)
    assert [catalog.phase_invariants(n, docs, tmp_path, ()) is not None for n in range(7)] == [
        False, False, False, True, False, False, True]
    bare = feature_docs(tmp_path, "g", "# T\n\n## Model\n\nx\n")
    assert catalog.phase_invariants(3, bare, tmp_path, ()) is None
    assert catalog.phase_invariants(3, [], tmp_path, ()) is None


def test_mutant_rows_read_only_and_narrow(tmp_path):
    """witness a stored matrix with a control, an error, an inconclusive, a no-test, a red, a blind-test and a
    pinned row (the pinned with a long diff) - distinguishes the narrow read (mutants that are pinned or
    blind-test only; `file` from the diff's first `--- a/` line; no output tail or command) from a
    read of everything; the store file is byte-identical after the read; no store gives (), a file that is not
    a database raises sqlite3.Error."""
    store = tmp_path / "state" / "reports.sqlite"
    diff = "--- a/app/x.py\n+++ b/app/x.py\n@@\n-a\n+b\n" + "# pad\n" * 80
    conn = report_store.open_store(store)
    report_store.add_matrix(conn, "m-one", [
        {"id": "C1", "kind": "control", "outcome": "pinned", "test": "t.py::a", "diff": diff},
        {"id": "E1", "kind": "mutant", "outcome": "error", "test": "t.py::a", "diff": diff},
        {"id": "I1", "kind": "mutant", "outcome": "inconclusive", "test": "t.py::a", "diff": diff},
        {"id": "N1", "kind": "mutant", "outcome": "no-test", "test": "t.py::a", "diff": diff},
        {"id": "R1", "kind": "mutant", "outcome": "red", "test": "t.py::a", "diff": diff},
        {"id": "B1", "kind": "mutant", "outcome": "blind-test", "test": "t.py::b[p]", "diff": "no diff here"},
        {"id": "P1", "kind": "mutant", "outcome": "pinned", "test": "t.py::a", "diff": diff, "output_tail": "x"}],
        datetime(2026, 11, 20, 10, 0))
    conn.close()
    before = store.read_bytes()
    found = catalog.mutant_rows(store)
    assert store.read_bytes() == before
    assert sorted(found, key=lambda r: r.mutant) == [
        catalog.MutantRow("m-one", "2026-11-20T10:00:00", "B1", "mutant", "t.py::b[p]", "blind-test", None),
        catalog.MutantRow("m-one", "2026-11-20T10:00:00", "P1", "mutant", "t.py::a", "pinned", "app/x.py")]
    assert catalog.mutant_rows(None) == () and catalog.mutant_rows(tmp_path / "none.sqlite") == ()
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"this is not an sqlite database" * 20)
    with pytest.raises(sqlite3.Error):
        catalog.mutant_rows(junk)


def test_a_row_without_a_worktree_takes_the_repo_of_its_spec_as_the_tree(tmp_path):
    """witness a spec in a tmp git repo (a `.git` directory, and in a second repo a `.git` file) asked with
    tree=None, a matrix row mutating a file of that repo, and a test defined only there - distinguishes the
    nearest parent holding a `.git` as the tree (pinned from the repo's own file, never-mutated from its `def`,
    `table.tree` naming it) from a join against nothing, where every test would be test-missing."""
    for marker in ("dir", "file"):
        repo = tmp_path / marker
        with_test(repo, text="def test_one(): pass\ndef test_two(): pass\n")
        (repo / ".git").mkdir() if marker == "dir" else (repo / ".git").write_text("gitdir: elsewhere\n")
        text = invariant_spec(("L1", "n", "`tests/test_a.py::test_one`"), ("L2", "n", "`tests/test_a.py::test_two`"),
                              ("L3", "n", "`tests/test_a.py::test_gone`"))
        docs = feature_docs(repo / "sub" / "deeper", "f", text)
        table = catalog.phase_invariants(3, docs, None, [mrow("tests/test_a.py::test_one")])
        assert [r.status for r in table.rows] == ["pinned", "never-mutated", "test-missing"], marker
        assert table.tree == str(repo) and table.readable and table.why is None


def test_a_spec_in_no_git_repo_has_every_row_not_checkable_with_the_reason(tmp_path):
    """witness a spec under tmp_path with no `.git` above it, tree=None, and a matrix row that would pin its test -
    distinguishes a readable table whose rows keep their law, negation and test but are all `not-checkable`,
    with `why` saying the spec is in no git repo and `tree` None, from a status borrowed from nowhere."""
    text = invariant_spec(("L1", "n", "`tests/test_a.py::test_one`"))
    table = catalog.phase_invariants(3, feature_docs(tmp_path, "f", text), None, [mrow("tests/test_a.py::test_one")])
    assert table.readable and table.tree is None and "no git repo" in table.why
    assert [(r.law, r.negation, r.tests, r.status, r.evidence) for r in table.rows] == [
        ("L1", "n", ("tests/test_a.py::test_one",), "not-checkable", ())]


def test_a_given_worktree_is_the_tree_and_is_named(tmp_path):
    """witness a given worktree holding the test - distinguishes the answer naming that very tree with no
    `why` from one that falls back to the spec's repo."""
    with_test(tmp_path)
    table = invariants_of(tmp_path, invariant_spec(("L", "n", "`tests/test_a.py::test_one`")), [])
    assert table.tree == str(tmp_path) and table.why is None
