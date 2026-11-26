"""wf: the phase harness. Tells an agent which phase a feature is in and
what to read for it, from the outputs in `docs/specs/<feature>.md`.

    python3 wf.py next <feature> [--presence-dir DIR] [--store FILE]    derive the phase, print verdicts + bundle
    python3 wf.py status <feature> [--presence-dir DIR] [--store FILE]  verdicts only
    python3 wf.py end FEATURE... [--repo-root DIR]       session-end gate text + handoff checks + Delivery PRs' `Model:` line
    python3 wf.py post-spec FEATURE [--repo-root DIR]    post the spec's missing sections to each `#N` ticket (the only write to the tracker)
    python3 wf.py report add --repo-root DIR --feature F --report R [--store FILE]   JSON lines on stdin
    python3 wf.py report brief --repo OWNER/NAME --feature F [--store FILE]   item ids on stdin
    python3 wf.py report manifest --name NAME [--store FILE]   manifest JSON on stdin
    python3 wf.py report import [--workflow-dir DIR] [--store FILE] PATH...   the existing evidence files
    python3 wf.py report show (--report R | --feature F --repo OWNER/NAME | --matrix NAME) [--store FILE]

The phase is derived from the phase outputs on the branch (first phase from
0 whose output is invalid and not skipped by the approved plan); the phase is
never stored. The harness never writes to the work repo. Its only writes are
outside it: the report store (SQLite, written by `report add`, `report brief`, `report manifest`, `report import` and the
prover's `--name`; see
docs/design/report-store.md) and the presence record. `next` and `status`
record which agent process is working on the feature (one JSON file per repo
root + slug, default `$XDG_STATE_HOME/agentic-dev-kit/presence` or
`~/.local/state/agentic-dev-kit/presence`). A failure to record never changes
the answer; it prints one line on stderr. `post-spec` is the only command that writes to the
tracker (a new comment on a GitHub issue); `next` and `end` only read it.

Design: docs/design/phase-harness.md, docs/design/workflow-desktop.md (presence).
Spec: docs/specs/phase-harness.md, docs/specs/workflow-desktop.md.

Stdlib only: argparse, collections.abc, dataclasses, datetime, hashlib, importlib, json,
os, pathlib, re, sqlite3, subprocess, sys, typing (plus the siblings audit.py and
report_store.py, imported by path; `end` and `post-spec` also run the `gh` CLI for the Delivery PRs and tickets).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING:
    from audit import Unread

WORKFLOW_DIR = Path(__file__).resolve().parent
STATE_NAME = "agentic-dev-kit"   # presence dir under $XDG_STATE_HOME; same literal in report_store.py and app/desk.py; sync_kit.py rewrites it to "agentic-dev-kit" in the public kit (Decision 1)
PHASE0_REQUIRED: tuple[str, ...] = ("SESSION-ISOLATION.md", "INVARIANTS.md")
PHASE0_LESSONS = "LESSONS.md"   # printed at phase 0 only when the file exists
PhaseStatus = Literal["pass", "skip", "fail", "todo"]
PHASES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
APPROVED_RE = re.compile(r"^Approved:\s*(\d{4}-\d{2}-\d{2})\b(.*)$", re.MULTILINE)
TRACKER_RE = re.compile(r"^(https?://\S+|#\d+|[A-Z][A-Z0-9]*-\d+)$")
REF_RE = re.compile(r"(?<![\w/.~:-])/([a-z0-9][a-z0-9-]*)|skill://([a-z0-9][a-z0-9-]*)")


@dataclass(frozen=True)
class Spec:
    path: Path
    text: str
    front: dict[str, str]
    sections: dict[str, str]


@dataclass(frozen=True)
class PlanRow:
    phase: int
    decision: Literal["run", "skip"]
    reason: str


@dataclass(frozen=True)
class Plan:
    rows: dict[int, PlanRow]
    approved: bool
    problems: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    phase: int
    status: PhaseStatus
    problems: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Derivation:
    feature: str
    verdicts: tuple[Verdict, ...]
    phase: int | None


# --- spec parsing -----------------------------------------------------------

def load_spec(repo_root: Path, feature: str) -> Spec | None:
    path = repo_root / "docs" / "specs" / f"{feature}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(f"{path}: not valid UTF-8 ({err.reason})") from err
    return Spec(path, text, parse_front_matter(text), parse_sections(text))


def parse_front_matter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    front: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return front
        key, sep, value = line.partition(":")
        if sep:
            front[key.strip()] = value.strip()
    return {}


def _fence_step(fence: str | None, line: str) -> tuple[bool, str | None]:
    """(is `line` a fence line, the fence state after it)."""
    m = FENCE_RE.match(line)
    if not m:
        return False, fence
    mark = m.group(1)[0]
    return True, None if fence == mark else (fence or mark)


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence: str | None = None
    for line in text.splitlines():
        is_fence, fence = _fence_step(fence, line)
        if not is_fence and fence is None and line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(body) for name, body in sections.items()}


def _cells(line: str) -> list[str]:
    row = line.strip()
    row = row.removeprefix("|")
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def spec_table(body: str) -> list[dict[str, str]]:
    """Rows of the first markdown table of `body`, keyed by header cell.
    `\\|` is not a separator; lines inside fences are ignored."""
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    fence: str | None = None
    for line in body.splitlines():
        is_fence, fence = _fence_step(fence, line)
        if is_fence:
            continue
        if fence or not line.strip().startswith("|"):
            if header is not None:
                break
            continue
        cells = _cells(line)
        if header is None:
            header = cells
        elif all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        else:
            cells += [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, cells)))
    return rows


def _first_cell(row: dict[str, str]) -> str:
    return next(iter(row.values()))


def _column(row: dict[str, str], name: str) -> str | None:
    for key, value in row.items():
        if name in key.lower():
            return value
    return None


def _approval(text: str) -> bool:
    return bool(re.search(r"\d{4}-\d{2}-\d{2}", text)) and bool(re.search(r"[\"“”].+?[\"“”]", text))


def _decision(text: str) -> Literal["run", "skip"] | None:
    text = text.strip().lower()
    return "run" if text == "run" else "skip" if text == "skip" else None


def _skip_problems(phase: int, decision: str, reason: str, label: str) -> list[str]:
    problems: list[str] = []
    if decision == "skip" and reason in ("", "—", "-"):
        problems.append(f"{label}: skip without a reason")
    if phase == 0 and decision == "skip":
        problems.append("phase 0 cannot be skipped")
    return problems


def parse_plan(spec: Spec) -> Plan:
    (plan_heading,) = GATE_SECTIONS[0]
    body = spec.sections.get(plan_heading)
    if body is None:
        return Plan({}, False, (f"no ## {plan_heading}",))
    problems: list[str] = []
    rows: dict[int, PlanRow] = {}
    for raw in spec_table(body):
        cells = list(raw.values())
        m = re.match(r"\s*(\d+)", cells[0]) if cells else None
        raw_decision = (_column(raw, "decision") or "").strip().lower()
        decision = _decision(raw_decision)
        reason = (_column(raw, "reason") or "").strip()
        if not m:
            problems.append(f"phase plan row without a phase number: {cells[0] if cells else ''!r}")
            continue
        phase = int(m.group(1))
        if decision is None:
            problems.append(f"phase {phase}: decision must be run or skip, got {raw_decision!r}")
            continue
        problems += _skip_problems(phase, decision, reason, f"phase {phase}")
        rows[phase] = PlanRow(phase, "run" if phase == 0 else decision, reason)
    for phase in PHASES[1:]:
        if phase not in rows:
            problems.append(f"phase plan has no row for phase {phase}")
    m = APPROVED_RE.search(body)
    if not m:
        problems.append("no Approved: line with a date in the phase plan")
        approved = False
    elif not _approval(m.group(0)):
        problems.append("Approved: line has no quoted owner answer")
        approved = False
    else:
        approved = True
    for raw in spec_table(spec.sections.get("Plan amendments", "")):
        label = _first_cell(raw)
        change = re.fullmatch(r"\s*phase\s+(\d)\s+(run|skip)\s*", _column(raw, "change") or "")
        if not change:
            problems.append(f"amendment {label}: change must read 'phase <N> <run|skip>'")
            continue
        if not _approval(_column(raw, "approved") or ""):
            problems.append(f"amendment {label} is not approved (needs a date and a quoted owner answer)")
            continue
        phase, decision = int(change.group(1)), _decision(change.group(2))
        assert decision is not None  # the regex admits only run|skip
        reason = (_column(raw, "reason") or "").strip()
        found = _skip_problems(phase, decision, reason, f"amendment {label}")
        if found:
            problems += found
        else:
            rows[phase] = PlanRow(phase, decision, reason)
    return Plan(rows, approved, tuple(problems))


# --- phase checks -----------------------------------------------------------

# The level-2 headings of the spec that the gate of each phase requires, in the gate's order: the one
# place the names live (#100). The checks unpack their phase's tuple; the window reads the map.
GATE_SECTIONS: dict[int, tuple[str, ...]] = {
    0: ("Phase plan",),
    1: ("Problem Statement", "Solution", "User Stories", "Premises", "Model"),
    2: ("Product review",),
    3: ("System Architecture", "Model"),
    4: ("Tickets",),
    5: (),
    6: ("Delivery",),
}


def check_phase(phase: int, spec: Spec, plan: Plan, repo_root: Path,
                store: Path | None = None) -> tuple[str, ...]:
    if phase == 0:
        return check_phase0(spec, plan)
    if phase == 1:
        return check_phase1(spec)
    if phase == 2:
        return check_phase2(spec, repo_root)
    if phase == 3:
        return check_phase3(spec)
    if phase == 4:
        return check_phase4(spec)
    if phase == 5:
        return check_phase5(spec, repo_root)
    return check_phase6(spec, repo_root, store)


def _design_path(spec: Spec, repo_root: Path) -> Path:
    return repo_root / "docs" / "design" / f"{spec.path.stem}.md"


def _repo_root_of(spec: Spec) -> Path:
    """The repo root a spec at `<root>/docs/specs/<feature>.md` lives in."""
    return spec.path.parent.parent.parent


def _tail(output: str, lines: int = 5) -> str:
    kept = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return " / ".join(kept[-lines:])


def _tail_keeping_missing_sections(output: str, lines: int = 5) -> str:
    """`_tail`, plus the lines before it that name a missing section (`missing section ## <Name>`):
    the desk shows a missing section only when a problem names its heading, so a long output must
    not cut it."""
    kept = [ln.strip() for ln in output.splitlines() if ln.strip()]
    cut = [ln for ln in kept[:-lines] if "missing section ##" in ln]
    return " / ".join([*cut, *kept[-lines:]])


def _guard(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `<WORKFLOW_DIR>/guards/<script>` (the harness's own install)."""
    return subprocess.run([sys.executable, str(WORKFLOW_DIR / "guards" / script), *args],
                          capture_output=True, text=True, check=False)


def check_phase5(spec: Spec, repo_root: Path) -> tuple[str, ...]:
    """The design exists and passes check_design.py in gate mode, always: full
    mode would fail on `state` as soon as the first function is implemented.
    A declared removal still pending (`removing`, D-054) is accepted."""
    design = _design_path(spec, repo_root)
    rel = design.relative_to(repo_root)
    if not design.is_file():
        return (f"no design file {rel}",)
    # --allow-removing: a declared removal (D-054) is valid design before the code
    # carries it out; the pre-push gate, without the flag, catches one never done.
    out = _guard("check_design.py", str(design), str(repo_root), "--gate", "--quiet", "--allow-removing")
    if out.returncode == 0:
        return ()
    return (f"{rel} fails check_design.py --gate: {_tail(out.stdout + out.stderr)}",)


DELIVERY_FIELDS = ("PR", "Model", "Mutation")
FIELD_RE = re.compile(r"\b(PR|Model|Mutation):")
REF_TICKET_RE = re.compile(r"https?://[^\s,;)]+|#\d+|\b[A-Z][A-Z0-9]*-\d+\b")


def delivery_rows(spec: Spec) -> dict[str, dict[str, str]]:
    """Ticket reference -> {PR, Model, Mutation} for each `- <refs> — PR: … ·
    Model: … · Mutation: …` line of `## Delivery`; only fields that are present
    and non-empty appear."""
    rows: dict[str, dict[str, str]] = {}
    (delivery,) = GATE_SECTIONS[6]
    for line in spec.sections.get(delivery, "").splitlines():
        first = FIELD_RE.search(line)
        if not line.lstrip().startswith("- ") or not first:
            continue
        fields: dict[str, str] = {}
        for piece in line[first.start():].split("·"):
            key, _, value = piece.partition(":")
            if key.strip() in DELIVERY_FIELDS and value.strip():
                fields[key.strip()] = value.strip()
        for ref in REF_TICKET_RE.findall(line[:first.start()]):
            rows[ref] = dict(fields)
    return rows


ITEM_RE = re.compile(r"\s*(- |\d+\. )")


def _item_refs(item: str) -> list[str]:
    """Ticket references of one `## Tickets` item: the `#N` / URL / `KEY-N`
    before its first ` — ` (the whole line when there is none); what follows
    the dash is description, e.g. `blocked by #8`. Phases 4 and 6 share this."""
    return REF_TICKET_RE.findall(item.split(" — ")[0])


def _ticket_refs(spec: Spec) -> list[str]:
    refs: list[str] = []
    (tickets,) = GATE_SECTIONS[4]
    for line in spec.sections.get(tickets, "").splitlines():
        if ITEM_RE.match(line):
            refs += [ref for ref in _item_refs(line) if ref not in refs]
    return refs


def _audit():
    """The sibling audit.py of this install, loaded by path."""
    found = importlib.util.spec_from_file_location("wf_audit", WORKFLOW_DIR / "audit.py")
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def feature_reports(repo: str, feature: str, store: Path | None = None) -> list[dict]:
    rows = _audit().gather_reviewer_reports(repo, store=store)
    return [r for r in rows if r.get("_feature") == feature]


def _judge_matrix(label: str, rows: list[dict]) -> tuple[str, ...]:
    """The control (`kind` control, else the first row) is red; every other row is pinned."""
    if not rows:
        return (f"mutation matrix {label} has no rows",)
    control = next((r for r in rows if r.get("kind") == "control"), rows[0])
    problems: list[str] = []
    if control.get("outcome") != "red":
        problems.append(f"{label}: control {control.get('id')} is {control.get('outcome')}, not red")
    problems += [f"{label}: {r.get('id')} is {r.get('outcome')}, not pinned"
                 for r in rows if r is not control and r.get("outcome") != "pinned"]
    return tuple(problems)


def _read_store(store: Path | None, read):
    """`read(report_store module, conn)` on the open store; None when there is no store file."""
    mod = _report_store()
    try:
        conn = mod.open_existing(store)
        if conn is None:
            return None
        try:
            return read(mod, conn)
        finally:
            conn.close()
    except sqlite3.Error as err:
        raise RuntimeError(f"report store {store}: {err}") from err


def _stored_alias(path: str, store: Path | None) -> str | None:
    return _read_store(store, lambda mod, conn: mod.alias(conn, path))


def _stored_matrix(name: str, store: Path | None) -> list[dict] | None:
    return _read_store(store, lambda mod, conn: mod.matrix_rows(conn, name))


def mutation_problems(value: str, store: Path | None) -> tuple[str, ...]:
    """Judge one Delivery `Mutation:` value against the store (Decision 6, #70): a path with a `/`
    must be relative and is the matrix imported from it (`report_store.alias`), else, as
    `reports/NAME.jsonl`, the stored matrix NAME; a bare NAME is the stored matrix NAME. Files are not read."""
    path = Path(value)
    if "/" in value:
        if path.is_absolute() or ".." in path.parts:
            return (f"mutation matrix path must be relative to the workflow dir: {value}",)
        name = _stored_alias(value, store)
        if name is None and (len(path.parts) == 2 and path.parts[0] == "reports" and path.suffix == ".jsonl"
                             and SLUG_RE.match(path.stem)):
            name = path.stem
    elif SLUG_RE.match(value):
        name = value
    else:
        return (f"{value} is not a matrix name",)
    found = None if name is None else _stored_matrix(name, store)
    if found is None:
        return (f"no mutation matrix {value} in the store",)
    return _judge_matrix(name, found)


NA_RE = re.compile(r"n/a\s*[—-]\s*(.*)$", re.DOTALL)


def _is_na(value: str) -> bool:
    """Whether a Delivery field value starts with `n/a` (valid only as `n/a — <reason>`)."""
    return value.startswith("n/a")


def _na_has_reason(value: str) -> bool:
    found = NA_RE.match(value)
    return bool(found and found.group(1).strip())


def check_phase6(spec: Spec, repo_root: Path, store: Path | None = None) -> tuple[str, ...]:
    """The spec has a `## Delivery` with a row for every ticket, each with `PR` (an http(s) URL),
    `Model` and `Mutation` (`n/a` only with a reason); each named model passes `check_model.py
    --gate --strict`, each mutation matrix is valid, and the feature's reviewer reports pass the
    audit (brief coverage, zero actionable findings, actor), read from the report store `store`."""
    (delivery,) = GATE_SECTIONS[6]
    rows = delivery_rows(spec)
    if delivery not in spec.sections:
        return (f"no ## {delivery}",)
    problems: list[str] = []
    for ref in _ticket_refs(spec):
        row = rows.get(ref)
        if row is None:
            problems.append(f"no Delivery row for {ref}")
            continue
        problems += [f"Delivery row for {ref} has no {name}:" for name in DELIVERY_FIELDS if name not in row]
        for name in ("Model", "Mutation"):
            value = row.get(name, "")
            if _is_na(value) and not _na_has_reason(value):
                problems.append(f"{name} of {ref} is n/a without a reason (write `n/a — <reason>`)")
        if "PR" in row and not re.match(r"https?://\S+$", row["PR"]):
            problems.append(f"PR of {ref} is not an http(s) URL: {row['PR']}")
    models = {r["Model"] for r in rows.values() if "Model" in r and not _is_na(r["Model"])}
    for model in sorted(models):
        out = _guard("check_model.py", str(repo_root / model), "--root", str(repo_root), "--gate", "--strict")
        if out.returncode != 0:
            problems.append(f"{model} fails check_model.py --gate --strict: "
                            f"{_tail_keeping_missing_sections(out.stdout + out.stderr)}")
    for matrix in sorted({r["Mutation"] for r in rows.values()
                          if "Mutation" in r and not _is_na(r["Mutation"])}):
        problems += mutation_problems(matrix, store)
    problems += _report_problems(spec, store)
    return tuple(problems)


def _report_problems(spec: Spec, store: Path | None = None) -> list[str]:
    audit = _audit()
    repo, feature = spec.front.get("repo", ""), spec.path.stem
    briefs = audit.gather_brief_items(repo, store=store)
    if feature not in briefs:
        return [f"no brief for {feature} in the store (wf.py report brief)"]
    reports = feature_reports(repo, feature, store)
    return [*audit.check_brief_coverage(reports, briefs[feature]),
            *audit.check_i6_zero_actionable(reports),
            *audit.check_report_actor(reports)]


def check_phase0(spec: Spec, plan: Plan) -> tuple[str, ...]:
    """The spec's front matter names `feature`, `repo` and `branch`, and the `## Phase plan` is
    valid: a row for every phase, each `run` or `skip` with a reason, and the owner's approval."""
    problems = [f"front matter has no {k}" for k in ("feature", "repo", "branch")
                if not spec.front.get(k)]
    return tuple(problems) + plan.problems


def check_phase1(spec: Spec) -> tuple[str, ...]:
    """The spec has a `## Problem Statement`, a `## Solution` and numbered `## User Stories`; every
    row of its `## Premises` table has a command, an output and a confirmed or falsified verdict;
    its `## Model` has a lifecycle inventory and a mermaid block (or a `Model skipped — <reason>`
    line); and the front matter has a `tracker` (a URL, `#N` or `KEY-N`)."""
    problem, solution, stories_heading, premises, model = GATE_SECTIONS[1]
    problems: list[str] = []
    for name in (problem, solution):
        if not spec.sections.get(name, "").strip():
            problems.append(f"no ## {name}")
    stories = spec.sections.get(stories_heading)
    if stories is None:
        problems.append(f"no ## {stories_heading}")
    elif not re.search(r"^\s*\d+\.\s+\S", stories, re.MULTILINE):
        problems.append(f"## {stories_heading} has no numbered story")
    problems += _premise_problems(spec.sections.get(premises), premises)
    if not re.search(r"^Model skipped — ", spec.text, re.MULTILINE):
        problems += _model_problems(spec.sections.get(model), model)
    tracker = spec.front.get("tracker", "")
    if not tracker:
        problems.append("front matter has no tracker")
    elif not TRACKER_RE.match(tracker):
        problems.append(f"tracker {tracker!r} is not a URL, #N or KEY-N")
    return tuple(problems)


def check_phase2(spec: Spec, repo_root: Path) -> tuple[str, ...]:
    """The spec has a `## Product review` that names a back-ticked path (the mockup), and at
    least one named path exists under the repo root."""
    (review,) = GATE_SECTIONS[2]
    body = spec.sections.get(review)
    if body is None:
        return (f"no ## {review}",)
    paths = [n for n in re.findall(r"`([^`\n]+)`", body) if "/" in n or "." in n]
    if not paths:
        return (f"## {review} names no path (back-ticked)",)
    if any((repo_root / n).exists() for n in paths):
        return ()
    return (f"mockup path {paths[0]} does not exist under the repo root",)


def check_phase3(spec: Spec) -> tuple[str, ...]:
    """The spec has a `## System Architecture` with a `### System invariants` table (each row a
    law, its negation and a back-ticked `Verified in`) or the words `no domain laws`, and its
    `## Model` has a `sequenceDiagram` (unless `Model skipped — <reason>`)."""
    architecture, model = GATE_SECTIONS[3]
    body = spec.sections.get(architecture)
    if body is None:
        return (f"no ## {architecture}",)
    problems: list[str] = []
    if "no domain laws" not in body:
        problems += _invariant_problems(body, architecture)
    if not re.search(r"^Model skipped — ", spec.text, re.MULTILINE):
        model_body = spec.sections.get(model, "")
        if not re.search(r"^```mermaid\s*\n\s*sequenceDiagram\b", model_body, re.MULTILINE):
            problems.append(f"## {model} has no sequenceDiagram")
    return tuple(problems)


def _invariant_problems(body: str, architecture: str) -> list[str]:
    m = re.search(r"^### System invariants\s*\n(.*?)(?=^#{1,3} |\Z)", body, re.MULTILINE | re.DOTALL)
    if not m:
        return [f"## {architecture} has no ### System invariants (or the words 'no domain laws')"]
    rows = spec_table(m.group(1))
    if not rows:
        return ["### System invariants table has no rows"]
    problems: list[str] = []
    for n, row in enumerate(rows, 1):
        for col in ("law", "negation"):
            if not (_column(row, col) or "").strip():
                problems.append(f"invariant row {n} has an empty {col}")
        if not _names(_column(row, "verified") or ""):
            problems.append(f"invariant row {n} has no back-ticked Verified in")
    return problems


def check_phase4(spec: Spec) -> tuple[str, ...]:
    """The spec has a `## Tickets` section with at least one item, and every item carries a
    tracker reference (a URL, `#N` or `KEY-N`) before its ` — `."""
    (tickets,) = GATE_SECTIONS[4]
    body = spec.sections.get(tickets)
    if body is None:
        return (f"no ## {tickets}",)
    items = [ln.strip() for ln in body.splitlines() if ITEM_RE.match(ln)]
    if not items:
        return (f"## {tickets} has no item",)
    return tuple(f"ticket item without a tracker reference before ' — ': {item!r}"
                 for item in items if not _item_refs(item))


def _premise_problems(body: str | None, heading: str) -> list[str]:
    if body is None:
        return [f"no ## {heading}"]
    rows = spec_table(body)
    if not rows:
        return [f"## {heading} table has no rows"]
    problems: list[str] = []
    for row in rows:
        label = _first_cell(row)
        for col in ("premise", "command", "output", "verdict"):
            if not (_column(row, col) or "").strip():
                problems.append(f"premise {label} has an empty {col}")
        verdict = (_column(row, "verdict") or "").strip().lower()
        if verdict and not verdict.startswith(("confirmed", "falsified")):
            problems.append(f"premise {label} verdict must be confirmed or falsified")
    return problems


def _model_problems(body: str | None, heading: str) -> list[str]:
    if body is None:
        return [f"no ## {heading} (or a 'Model skipped — <reason>' line)"]
    problems: list[str] = []
    if "### Lifecycle inventory" not in body:
        problems.append(f"## {heading} has no ### Lifecycle inventory")
    if not re.search(r"^```mermaid\s*$", body, re.MULTILINE):
        problems.append(f"## {heading} has no mermaid block")
    return problems


def phase_started(phase: int, spec: Spec) -> bool:
    if phase == 1:
        return any(n in spec.sections for n in GATE_SECTIONS[1]) or bool(spec.front.get("tracker"))
    if phase == 5:
        return _design_path(spec, _repo_root_of(spec)).is_file()
    if phase == 0:
        return True
    return GATE_SECTIONS[phase][0] in spec.sections


# --- derivation -------------------------------------------------------------

def derive(repo_root: Path, feature: str, store: Path | None = None) -> Derivation:
    """Walk the phases in order; the first invalid one is the derived phase.
    An unapproved plan stops the walk at phase 0, which is what makes a skip
    count only with approval."""
    spec = load_spec(repo_root, feature)
    if spec is None:
        problem = f"no spec file docs/specs/{feature}.md"
        return Derivation(feature, (Verdict(0, "todo", (problem,), ""),), 0)
    plan = parse_plan(spec)
    verdicts: list[Verdict] = []
    for phase in PHASES:
        row = plan.rows.get(phase)
        if phase > 0 and row and row.decision == "skip":
            verdicts.append(Verdict(phase, "skip", (), row.reason))
            continue
        problems = check_phase(phase, spec, plan, repo_root, store)
        if not problems:
            verdicts.append(Verdict(phase, "pass", (), ""))
            continue
        status: PhaseStatus = "fail" if phase_started(phase, spec) else "todo"
        verdicts.append(Verdict(phase, status, problems, ""))
        return Derivation(feature, tuple(verdicts), phase)
    return Derivation(feature, tuple(verdicts), None)


# --- isolation --------------------------------------------------------------

def is_isolated(repo_root: Path, feature: str) -> bool:
    """True in a linked worktree (git-dir differs from git-common-dir) or when
    the claim `<git-common-dir>/agentic-sessions/<feature>` exists."""
    out = subprocess.run(["git", "rev-parse", "--git-dir", "--git-common-dir"],
                         cwd=repo_root, capture_output=True, text=True, check=False)
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 2:
        return False
    git_dir, common = ((repo_root / line).resolve() for line in lines)
    return git_dir != common or (common / "agentic-sessions" / feature).exists()


# --- instruction bundle -----------------------------------------------------

def _names(cell: str) -> tuple[str, ...]:
    return tuple(re.findall(r"`([^`]+)`", cell))


def _pipeline_rows(workflow_dir: Path) -> list[dict[str, str]]:
    text = (workflow_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    return spec_table(parse_sections(text).get("Pipeline", ""))


def _rules_file(workflow_dir: Path, phase: int) -> str:
    """The file the Pipeline table's Rules column names for `phase`."""
    for row in _pipeline_rows(workflow_dir):
        if _first_cell(row).strip() == str(phase):
            names = _names(_column(row, "rules") or "")
            if names:
                return names[0]
    raise ValueError(f"WORKFLOW.md Pipeline table names no rules file for phase {phase}")


def pipeline(workflow_dir: Path) -> dict[int, tuple[str, ...]]:
    """Skills of each phase 1-6, read from the Pipeline table of WORKFLOW.md."""
    table = _pipeline_rows(workflow_dir)
    skills = {int(_first_cell(r)): _names(_column(r, "skill") or "")
              for r in table if _first_cell(r).strip().isdigit()}
    for phase in PHASES[1:]:
        if phase not in skills:
            raise ValueError(f"WORKFLOW.md Pipeline table has no row for phase {phase}")
    return skills


def resolve_skills(phase_skills: tuple[str, ...], skills_dir: Path,
                   other_phase_skills: frozenset[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(skills referenced one level down and included, those named only
    because they belong to another phase)."""
    included: list[str] = []
    named: list[str] = []
    for skill in phase_skills:
        path = skills_dir / skill / "SKILL.md"
        if not path.is_file():
            continue
        for m in REF_RE.finditer(path.read_text(encoding="utf-8")):
            ref = m.group(1) or m.group(2)
            if ref == skill or ref in phase_skills or not (skills_dir / ref).is_dir():
                continue
            target = named if ref in other_phase_skills else included
            if ref not in target:
                target.append(ref)
    return tuple(included), tuple(named)


def transversal_files(workflow_dir: Path, phase: int) -> tuple[str, ...]:
    """Files the Transversal table assigns to `phase` ('Read when' mentions it)."""
    text = (workflow_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    _, _, tail = text.partition("Transversal files:")
    files: list[str] = []
    for row in spec_table(tail):
        names = _names(_first_cell(row))
        if names and re.search(rf"\bphase {phase}(?!\d)", _column(row, "read when") or ""):
            files.append(names[0])
    return tuple(files)


def missing_skills(phase: int, workflow_dir: Path, skills_dir: Path) -> tuple[str, ...]:
    """Skills the Pipeline table assigns to `phase` that are not in the store."""
    if phase == 0:
        return ()
    return tuple(s for s in pipeline(workflow_dir)[phase]
                 if not (skills_dir / s / "SKILL.md").is_file())


def _block(label: str, text: str) -> str:
    return f"=== {label} ===\n{text.rstrip()}\n"


def phase0_files(workflow_dir: Path) -> tuple[str, ...]:
    """The transversal files phase 0 prints: `PHASE0_REQUIRED`, then `PHASE0_LESSONS` when it
    exists in `workflow_dir`. A missing required file stays in the answer (`bundle` then raises on it)."""
    return PHASE0_REQUIRED + ((PHASE0_LESSONS,) if (workflow_dir / PHASE0_LESSONS).is_file() else ())


def bundle(phase: int, workflow_dir: Path, skills_dir: Path) -> str:
    """The instructions to read for `phase`, derived from WORKFLOW.md."""
    workflow = (workflow_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    parts: list[str] = []
    if phase == 0:
        general = parse_sections(workflow).get("General rules", "")
        parts.append(_block("WORKFLOW.md § General rules", general))
        for name in phase0_files(workflow_dir):
            parts.append(_block(f"file: {name}", (workflow_dir / name).read_text(encoding="utf-8")))
        for path in sorted((workflow_dir / "phases").glob("0-*.md")):
            parts.append(_block(f"phase file: phases/{path.name}", path.read_text(encoding="utf-8")))
        return "\n".join(parts)
    table = pipeline(workflow_dir)
    if phase == 4:
        m = re.search(r"^### 4\. Tickets\n(.*?)(?=^#{1,3} |\Z)", workflow, re.MULTILINE | re.DOTALL)
        if not m:
            raise ValueError("WORKFLOW.md has no '### 4. Tickets' section")
        parts.append(_block("WORKFLOW.md § 4. Tickets", m.group(1)))
    else:
        rules = _rules_file(workflow_dir, phase)
        parts.append(_block(f"phase file: {rules}", (workflow_dir / rules).read_text(encoding="utf-8")))
    own = table[phase]
    others = frozenset(s for n, names in table.items() if n != phase for s in names) - frozenset(own)
    included, named = resolve_skills(own, skills_dir, others)
    missing = missing_skills(phase, workflow_dir, skills_dir)
    for skill in own:
        if skill in missing:
            parts.append(f"MISSING SKILL {skill}\n")
        else:
            parts.append(_block(f"skill: {skill}",
                                (skills_dir / skill / "SKILL.md").read_text(encoding="utf-8")))
    for skill in included:
        parts.append(_block(f"skill (referenced): {skill}",
                            (skills_dir / skill / "SKILL.md").read_text(encoding="utf-8")))
    if named:
        parts.append(f"referenced, not included: {', '.join(named)}\n")
    for name in transversal_files(workflow_dir, phase):
        parts.append(_block(f"file: {name}", (workflow_dir / name).read_text(encoding="utf-8")))
    return "\n".join(parts)


# --- session end -----------------------------------------------------------

GATE_MARKER = "- **Every session ends with the transversal gate**"


def session_gate_text(workflow_dir: Path) -> str:
    """The General rules bullet of WORKFLOW.md that starts the session-end gate."""
    workflow = (workflow_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    lines = parse_sections(workflow).get("General rules", "").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(GATE_MARKER)), None)
    if start is None:
        raise ValueError("WORKFLOW.md General rules has no bullet "
                         "'Every session ends with the transversal gate'")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- ")), len(lines))
    return "\n".join(lines[start:end]).rstrip()


GITHUB_PR_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/\d+$")
GH_TIMEOUT = 30   # seconds one `gh` call (pr view, issue view, issue comment) may take before it counts as failed


def read_pr(url: str) -> dict | None:
    """The PR at a `github.com` PR URL as `{number, merged, mergedAt, body}` (the keys
    `audit.check_model_declared` reads), through `gh pr view`. None when the URL is not a
    GitHub PR, `gh` is missing or fails, or it prints something that is not JSON: no
    evidence. Raises nothing."""
    if not GITHUB_PR_RE.match(url):
        return None
    try:
        done = subprocess.run(["gh", "pr", "view", url, "--json", "number,state,mergedAt,body"],
                              capture_output=True, text=True, check=False, timeout=GH_TIMEOUT)
        data = json.loads(done.stdout) if done.returncode == 0 else None
        return {"number": data["number"], "merged": data["state"] == "MERGED",
                "mergedAt": data.get("mergedAt"), "body": data.get("body")}
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None


def delivery_model_problems(feature: str, repo_root: Path,
                            pr_reader: Callable[[str], dict | None] = read_pr) -> list[str]:
    """The audit's D-051 check (`check_model_declared`) over the PRs of the `## Delivery` rows
    of docs/specs/<feature>.md under `repo_root`, one read per distinct URL. No spec, no
    Delivery, or a PR `pr_reader` returns None for, gives nothing to judge. Each message is
    prefixed with the feature."""
    spec = load_spec(repo_root, feature)
    if spec is None:
        return []
    urls: list[str] = []
    for row in delivery_rows(spec).values():
        url = row.get("PR", "")
        if re.match(r"https?://", url) and url not in urls:
            urls.append(url)
    prs = [pr for pr in map(pr_reader, urls) if pr is not None]
    return [f"{feature}: {m}" for m in _audit().check_model_declared(prs)]


# --- ticket spec gate (#116) -------------------------------------------------

POSTED_SECTIONS = ("Problem Statement", "Solution", "User Stories", "Premises")   # what `post-spec` posts
COMMENT_LIMIT = 65_000   # characters of one posted comment; GitHub's limit is 65 536
ISSUE_REF_RE = re.compile(r"#(\d+)")


def read_tickets(repo: str, numbers: list[int]) -> dict[int, dict | Unread]:
    """Issue number -> `{number, state, body, comments}` through one pooled, retried read
    (`audit._gh_many`); a ticket that keeps failing is an `Unread` with its reason. Raises nothing."""
    calls = [["issue", "view", str(n), "--repo", repo, "--json", "number,state,body,comments"]
             for n in numbers]
    return dict(zip(numbers, _audit()._gh_many(calls) if calls else []))


def split_refs(refs: Iterable[str]) -> tuple[list[int], list[str]]:
    """The `#N` refs as numbers, in order and without repeats, and the other refs (a `KEY-N`,
    a URL) as written."""
    numbers: list[int] = []
    others: list[str] = []
    for ref in refs:
        match = ISSUE_REF_RE.fullmatch(ref)
        if match and int(match[1]) not in numbers:
            numbers.append(int(match[1]))
        elif not match and ref not in others:
            others.append(ref)
    return numbers, others


def delivery_tickets(spec: Spec) -> tuple[list[int], list[str]]:
    """`split_refs` of the `## Delivery` rows."""
    return split_refs(delivery_rows(spec))


def not_judged(ref: str, why: str) -> str:
    return f"ticket {ref} not judged: {why}"


def _unread_reason(answer: object) -> str:
    return str(getattr(answer, "reason", "no answer"))


def ticket_spec_check(feature: str, repo_root: Path,
                      ticket_reader: Callable[[str, list[int]], dict[int, dict | Unread]] = read_tickets
                      ) -> tuple[list[str], list[str]]:
    """`(problems, notes)` for the tickets of the `## Delivery` rows of docs/specs/<feature>.md:
    the audit's I3 rule (`audit.issue_has_spec`) is a problem for a ticket read without a spec
    marker or `Skipped` note; a ticket that could not be read, or whose ref is not a GitHub
    issue of the spec's repo, is a note. No spec or no Delivery gives `([], [])`. One read."""
    spec = load_spec(repo_root, feature)
    if spec is None:
        return [], []
    numbers, others = delivery_tickets(spec)
    repo = spec.front.get("repo", "")
    problems: list[str] = []
    notes = [f"{feature}: " + not_judged(ref, f"not a GitHub issue of {repo or 'the spec'}") for ref in others]
    if not repo:
        notes += [f"{feature}: " + not_judged(f"#{n}", "the spec has no repo") for n in numbers]
        return problems, notes
    issues = ticket_reader(repo, numbers) if numbers else {}
    has_spec = _audit().issue_has_spec
    for n in numbers:
        issue = issues.get(n)
        if not isinstance(issue, dict):
            notes.append(f"{feature}: ticket #{n} not read: {_unread_reason(issue)}")
        elif not has_spec(issue):
            problems.append(f"{feature}: ticket #{n} has no spec section and no 'Skipped — reason' "
                            f"note: run python3 wf.py post-spec {feature}")
    return problems, notes


def missing_sections(issue: dict) -> list[str]:
    """Of the four spec sections the ticket carries (`POSTED_SECTIONS`), those for which no
    line of the body or of any comment is exactly `## <name>`."""
    texts = [issue.get("body") or "", *((c.get("body") or "") for c in issue.get("comments") or [])]
    lines = {line.rstrip() for text in texts for line in text.splitlines()}
    return [name for name in POSTED_SECTIONS if f"## {name}" not in lines]


def spec_comment(spec: Spec, names: list[str]) -> str:
    """`## <name>`, a blank line and the spec's text of the section, for each name in order, one blank
    line between sections. ValueError when a section is empty or absent in the spec, or the whole
    text is over `COMMENT_LIMIT` characters (the message names each section and its length)."""
    texts = {name: spec.sections.get(name, "").strip() for name in names}
    for name, text in texts.items():
        if not text:
            raise ValueError(f"section '{name}' is empty or absent in {spec.path.name}")
    body = "\n\n".join(f"## {name}\n\n{text}" for name, text in texts.items())
    if len(body) > COMMENT_LIMIT:
        sizes = ", ".join(f"{name}: {len(text)} characters" for name, text in texts.items())
        raise ValueError(f"comment is {len(body)} characters, over the {COMMENT_LIMIT} limit ({sizes})")
    return body


def post_comment(repo: str, number: int, body: str) -> None:
    """One new comment on issue `number` of `repo` (`gh issue comment --body-file -`, body on stdin).
    RuntimeError with gh's message on a non-zero exit, a missing `gh` or a timeout."""
    try:
        done = subprocess.run(["gh", "issue", "comment", str(number), "--repo", repo, "--body-file", "-"],
                              input=body, capture_output=True, text=True, check=False, timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise RuntimeError(f"gh issue comment {number}: {type(err).__name__}: {err}") from err
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"gh exited {done.returncode}")


def run_post_spec(feature: str, repo_root: Path,
                  ticket_reader: Callable[[str, list[int]], dict[int, dict | Unread]] = read_tickets,
                  commenter: Callable[[str, int, str], None] = post_comment) -> int:
    """Post to each `#N` ticket of the spec's `## Tickets` the spec sections it lacks as one new
    comment (never an edit). 0 when every ticket is handled, 1 when one could not be read or
    posted to (the others are still tried), 2 when there is no spec, no repo in its front matter,
    or a section it lacks."""
    spec = load_spec(repo_root, feature)
    if spec is None:
        print(f"wf: no spec docs/specs/{feature}.md under {repo_root}", file=sys.stderr)
        return 2
    repo = spec.front.get("repo", "")
    if not repo:
        print(f"wf: docs/specs/{feature}.md has no `repo:` in its front matter", file=sys.stderr)
        return 2
    absent = [name for name in POSTED_SECTIONS if not spec.sections.get(name, "").strip()]
    if absent:
        print(f"wf: docs/specs/{feature}.md lacks the section(s): {', '.join(absent)}", file=sys.stderr)
        return 2
    numbers, others = split_refs(_ticket_refs(spec))
    for ref in others:
        print(not_judged(ref, f"not a GitHub issue of {repo}"))
    if not numbers:
        print("nothing to post: no #N ticket in ## Tickets")
        return 0
    issues = ticket_reader(repo, numbers)
    failed = False
    for n in numbers:
        issue = issues.get(n)
        if not isinstance(issue, dict):
            print(f"FAIL: ticket #{n} not read: {_unread_reason(issue)}")
            failed = True
            continue
        names = missing_sections(issue)
        if not names:
            print(f"#{n}: already carries the spec sections, nothing posted")
            continue
        try:
            commenter(repo, n, spec_comment(spec, names))
        except (ValueError, RuntimeError) as err:
            print(f"FAIL: ticket #{n} not posted: {err}")
            failed = True
            continue
        print(f"#{n}: posted {', '.join(names)}")
    return 1 if failed else 0


def end_checks(workflow_dir: Path, today: date, features: tuple[str, ...],
               known_refs: Callable[[str], set[int]] | None = None,
               repo_root: Path = Path("."),
               pr_reader: Callable[[str], dict | None] = read_pr) -> tuple[str, ...]:
    """Problems with handoffs/handoff-<feature>-<today>.md, per feature; a handoff that passes
    the two basic checks is also given the audit's I10 and I9. `known_refs(repo)` reads the
    tracker (None: audit.gather_known_refs) only when the handoff cites a `#N`; an empty read
    counts the cited numbers as known. After the handoffs, each feature's Delivery PRs under
    `repo_root` get `delivery_model_problems` (read by `pr_reader`), whatever its handoff's
    result."""
    stamp = f"{today:%Y%m%d}"
    problems: list[str] = []
    for feature in features:
        name = f"handoff-{feature}-{stamp}.md"
        path = workflow_dir / "handoffs" / name
        if not path.is_file():
            problems.append(f"{feature}: no {name}")
            continue
        text = path.read_text(encoding="utf-8")
        found = len(problems)
        if not re.search(r"^- \*\*Repo\*\*:", text, re.MULTILINE):
            problems.append(f"{feature}: {name} has no '- **Repo**:' line")
        if "audit.py --repo" not in text:
            problems.append(f"{feature}: {name} does not contain 'audit.py --repo' (the audit outcome)")
        if len(problems) > found:
            continue
        audit = _audit()
        repo = audit._handoff_repo(text)
        handoff = {"name": name, "text": text, "repo": repo}
        messages = audit.check_handoffs_quote_commands([handoff])
        if repo:
            cited = {int(n) for n in re.findall(r"#(\d+)", text)}
            known = (known_refs or audit.gather_known_refs)(repo) if cited else set()
            messages += audit.check_followups_tracked([handoff], known or cited, repo)
        problems += [f"{feature}: {m}" for m in messages]
    for feature in features:
        problems += delivery_model_problems(feature, repo_root, pr_reader)
    return tuple(problems)


# --- presence ---------------------------------------------------------------

SHELLS: frozenset[str] = frozenset({"bash", "sh", "dash", "zsh", "fish", "ksh",
                                    "env", "timeout", "nohup", "xargs"})
PROC: Path = Path("/proc")


@dataclass(frozen=True)
class Presence:
    slug: str
    repo_root: str
    pid: int
    process: str
    start_ticks: int
    recorded_at: str
    alive: bool


def default_presence_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / STATE_NAME / "presence"


def proc_stat(pid: int, proc_root: Path = PROC) -> tuple[str, int, int] | None:
    """(comm, ppid, start_ticks) of a pid; None when it does not exist.

    The comm sits between the first "(" and the LAST ")": it may hold both."""
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    start, end = text.find("("), text.rfind(")")
    rest = text[end + 1:].split()
    if start < 0 or end < start or len(rest) < 20:
        raise ValueError(f"malformed stat for pid {pid}")
    return text[start + 1:end], int(rest[1]), int(rest[19])


def agent_process(pid: int, proc_root: Path = PROC) -> tuple[int, str, int] | None:
    """First ancestor-or-self of pid whose comm is not a shell; None at pid 1,
    when /proc is absent, or when a stat cannot be read."""
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            stat = proc_stat(pid, proc_root)
        except ValueError:
            return None
        if stat is None:
            return None
        comm, ppid, ticks = stat
        if comm not in SHELLS:
            return pid, comm, ticks
        pid = ppid
    return None


def record_presence(presence_dir: Path, repo_root: Path, feature: str, now: datetime,
                    proc_root: Path = PROC) -> Path | None:
    """Write (overwrite) the record of the agent running wf on (repo_root, feature).

    None when no agent process is found; OSError when the store is not writable."""
    agent = agent_process(os.getppid(), proc_root)
    if agent is None:
        return None
    pid, comm, ticks = agent
    root = str(repo_root.resolve())
    name = hashlib.sha1(f"{root}\0{feature}".encode()).hexdigest() + ".json"
    record = {"slug": feature, "repo_root": root, "pid": pid, "process": comm,
              "start_ticks": ticks, "recorded_at": now.astimezone().isoformat()}
    presence_dir.mkdir(parents=True, exist_ok=True)
    path = presence_dir / name
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def read_presence(presence_dir: Path, proc_root: Path = PROC) -> tuple[Presence, ...]:
    """Every record in the store; `alive` when its pid exists with the same start ticks."""
    if not presence_dir.is_dir():
        return ()
    found: list[Presence] = []
    for path in sorted(presence_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            slug, root, pid = str(data["slug"]), str(data["repo_root"]), int(data["pid"])
            process, ticks = str(data["process"]), int(data["start_ticks"])
            recorded_at = str(data["recorded_at"])
        except (OSError, ValueError, KeyError, TypeError) as err:
            raise ValueError(f"{path}: malformed presence record ({err})") from err
        try:
            live = proc_stat(pid, proc_root)
        except ValueError:
            live = None
        found.append(Presence(slug, root, pid, process, ticks, recorded_at,
                              live is not None and live[2] == ticks))
    return tuple(found)


# --- CLI --------------------------------------------------------------------

def render(derivation: Derivation) -> str:
    lines: list[str] = []
    for v in derivation.verdicts:
        if v.status == "pass":
            lines.append(f"PASS phase {v.phase}")
        elif v.status == "skip":
            lines.append(f"SKIP phase {v.phase} (approved): {v.reason}")
        else:
            lines += [f"{v.status.upper()} phase {v.phase}: {p}" for p in v.problems]
    if derivation.phase is None:
        lines.append("DELIVERED: every phase is valid or an approved skip")
    return "\n".join(lines)


def print_ticket_check(feature: str, repo_root: Path) -> int:
    """`next` on a delivered feature: the ticket notes (NOTE) and problems (FAIL tickets) of
    `ticket_spec_check`; 1 when there is a problem, 0 otherwise."""
    problems, notes = ticket_spec_check(feature, repo_root)
    for note in notes:
        print(f"NOTE tickets: {note}")
    for problem in problems:
        print(f"FAIL tickets: {problem}")
    return 1 if problems else 0


def run_end(workflow_dir: Path, features: tuple[str, ...], repo_root: Path = Path("."),
            ticket_reader: Callable[[str, list[int]], dict[int, dict | Unread]] = read_tickets) -> int:
    """Print the session gate and every `end_checks` problem (handoffs, then the Delivery PRs
    under `repo_root`) and `ticket_spec_check` problem (the Delivery tickets) as FAIL lines, the
    ticket notes as NOTE lines; 0 when no problem, 1 otherwise, 2 on an unreadable input."""
    try:
        gate = session_gate_text(workflow_dir)
        problems = list(end_checks(workflow_dir, date.today(), features, repo_root=repo_root))
        notes: list[str] = []
        for feature in features:
            ticket_problems, ticket_notes = ticket_spec_check(feature, repo_root, ticket_reader)
            problems += ticket_problems
            notes += ticket_notes
    except (OSError, ValueError) as err:
        print(f"wf: {err}", file=sys.stderr)
        return 2
    print(gate)
    print()
    for note in notes:
        print(f"NOTE: {note}")
    for problem in problems:
        print(f"FAIL: {problem}")
    return 1 if problems else 0


def _record_presence(args: argparse.Namespace) -> None:
    try:
        record_presence(args.presence_dir, args.repo_root, args.feature, datetime.now())
    except OSError as err:
        print(f"wf: presence not recorded: {err}", file=sys.stderr)


def _report_store():
    """The sibling report_store.py of this install, loaded by path."""
    found = importlib.util.spec_from_file_location("wf_report_store", WORKFLOW_DIR / "report_store.py")
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def run_report(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    """`report add|show|brief`: every failure becomes an exit code and a stderr line."""
    store = _report_store()
    path = args.store or store.default_store_path(os.environ, Path.home())
    conn = None
    try:
        if args.report_command == "show":
            conn = store.open_existing(path)
            if args.matrix:
                found = None if conn is None else store.matrix_rows(conn, args.matrix)
                if found is None:
                    print(f"wf: no mutation matrix {args.matrix}", file=sys.stderr)
                    return 1
                for item in found:
                    print(json.dumps(item, ensure_ascii=False), file=stdout)
                return 0
            if conn is not None:
                for item in store.review_rows(conn, args.repo, args.feature, args.report):
                    print(json.dumps(item, ensure_ascii=False), file=stdout)
            return 0
        if args.report_command == "import":
            store.read_evidence(args.workflow_dir, args.paths)  # a rejected import leaves no store file
        conn = store.open_store(path)
        if args.report_command == "manifest":
            try:
                given = json.loads(stdin.read())
            except ValueError as err:
                print(f"wf: the manifest is not JSON: {err}", file=sys.stderr)
                return 2
            store.add_manifest(conn, args.name, given, datetime.now())
            print(f"manifest {args.name} stored", file=stdout)
            return 0
        if args.report_command == "import":
            done = store.import_files(conn, args.workflow_dir, args.paths, datetime.now())
            print(f"{done.files} files, {done.rows} rows imported ({done.historical} historical), "
                  f"{done.skipped} already imported", file=stdout)
            return 0
        if args.report_command == "brief":
            items = [line.strip() for line in stdin.read().splitlines() if line.strip()]
            added = store.add_brief_items(conn, args.repo, args.feature, items, datetime.now())
            print(f"{added} items added to the brief of {args.feature}", file=stdout)
            return 0
        rows, raw = [], []
        for line in stdin.read().splitlines():
            if line.strip():
                raw.append(line)
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append(None)
        added = store.add_review_rows(conn, rows, args.feature, args.report,
                                      args.repo_root, datetime.now(), raw)
        print(f"{added} rows added to {args.report}", file=stdout)
        return 0
    except store.BatchRejected as err:
        for reason in err.reasons:
            print(reason, file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as err:
        print(f"wf: {err}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wf", description="The phase harness.")
    default_dir = WORKFLOW_DIR
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("next", "status"):
        cmd = sub.add_parser(name)
        cmd.add_argument("feature")
        cmd.add_argument("--repo-root", type=Path, default=Path("."))
        cmd.add_argument("--workflow-dir", type=Path, default=default_dir)
        cmd.add_argument("--skills-dir", type=Path, default=Path(
            os.environ.get("AGENT_SKILLS_DIR") or Path.home() / ".agents" / "skills"))
        cmd.add_argument("--presence-dir", type=Path, default=default_presence_dir())
        cmd.add_argument("--store", type=Path)
    end = sub.add_parser("end")
    end.add_argument("features", nargs="+", metavar="feature")
    end.add_argument("--workflow-dir", type=Path, default=default_dir)
    end.add_argument("--repo-root", type=Path, default=Path("."))
    post = sub.add_parser("post-spec")
    post.add_argument("feature")
    post.add_argument("--repo-root", type=Path, default=Path("."))
    report = sub.add_parser("report")
    rsub = report.add_subparsers(dest="report_command", required=True)
    radd = rsub.add_parser("add")
    radd.add_argument("--repo-root", type=Path, required=True)
    radd.add_argument("--feature", required=True)
    radd.add_argument("--report", required=True)
    rshow = rsub.add_parser("show")
    rshow.add_argument("--report")
    rshow.add_argument("--feature")
    rshow.add_argument("--repo")
    rshow.add_argument("--matrix")
    rman = rsub.add_parser("manifest")
    rman.add_argument("--name", required=True)
    rimport = rsub.add_parser("import")
    rimport.add_argument("--workflow-dir", type=Path, default=default_dir)
    rimport.add_argument("paths", nargs="+", metavar="PATH", type=Path)
    rbrief = rsub.add_parser("brief")
    rbrief.add_argument("--repo", required=True)
    rbrief.add_argument("--feature", required=True)
    for cmd in (radd, rshow, rbrief, rman, rimport):
        cmd.add_argument("--store", type=Path)
    args = parser.parse_args(argv)
    if args.command == "report":
        if args.report_command == "show" and not (
                args.matrix or args.report or (args.feature and args.repo)):
            parser.error("report show needs --matrix, --report, or --feature with --repo")
        return run_report(args, sys.stdin, sys.stdout)
    if args.command == "end":
        for feature in args.features:
            if not SLUG_RE.match(feature):
                print(f"wf: invalid feature slug {feature!r}", file=sys.stderr)
                return 2
        return run_end(args.workflow_dir, tuple(args.features), args.repo_root)
    if not SLUG_RE.match(args.feature):
        print(f"wf: invalid feature slug {args.feature!r}", file=sys.stderr)
        return 2
    if args.command == "post-spec":
        try:
            return run_post_spec(args.feature, args.repo_root)
        except (OSError, ValueError) as err:
            print(f"wf: {err}", file=sys.stderr)
            return 2
    try:
        store = args.store or _report_store().default_store_path(os.environ, Path.home())
        derivation = derive(args.repo_root, args.feature, store)
        print(render(derivation))
        _record_presence(args)
        last = derivation.verdicts[-1]
        if args.command == "status":
            return 0 if derivation.phase is None else 1
        if derivation.phase is None:
            return print_ticket_check(args.feature, args.repo_root)
        if not is_isolated(args.repo_root, args.feature):
            print(f"not isolated: run in a linked worktree or claim "
                  f"<git-common-dir>/agentic-sessions/{args.feature}")
            return 1
        text = bundle(derivation.phase, args.workflow_dir, args.skills_dir)
        missing = missing_skills(derivation.phase, args.workflow_dir, args.skills_dir)
    except (OSError, ValueError, RuntimeError) as err:
        print(f"wf: {err}", file=sys.stderr)
        return 2
    print()
    print(text)
    if missing:
        return 2
    return 0 if last.status == "todo" and derivation.phase > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
