"""Tests for wf.py, the phase harness.

Seams (agreed with the owner): the CLI run as a subprocess on a temporary
git repo, `derive()`, and `bundle()` fed a synthetic WORKFLOW.md and skills
store. No test reaches into the parsers or the per-phase checks.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / "wf.py"
_spec = importlib.util.spec_from_file_location("wf", WF)
wf = importlib.util.module_from_spec(_spec)
sys.modules["wf"] = wf
_spec.loader.exec_module(wf)

FEATURE = "demo"

# A spec valid for phases 0-4 whose plan skips 5-6 with approval: the
# feature is delivered. Tests derive their input from it by one replacement.
SPEC = """\
---
feature: demo
repo: owner/demo
branch: feature/demo
tracker: https://example.com/owner/demo/issues/1
---

# Spec: demo

## Phase plan

| Phase | Decision | Reason |
|---|---|---|
| 0 · Start | run | worktree |
| 1 · Design & alignment | run | — |
| 2 · Product review | run | mockup needed |
| 3 · System architecture | run | domain laws exist |
| 4 · Tickets | run | one ticket |
| 5 · Program design | skip | no new code |
| 6 · Implement-loop | skip | docs only |

Approved: 2026-11-16, owner in chat — "Approvo" (plan as above).

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
| P2 | Pipes \\| survive | `ls \\| wc -l` | 3 | falsified — it was 4 |

## Model

### Lifecycle inventory

| Entity | Moves in time? |
|---|---|
| Thing | no |

```mermaid
flowchart TD
  A --> B
```

```mermaid
sequenceDiagram
  A->>B: call
```

## Product review

Mockup: `docs/mockups/demo.md`

## System Architecture

### System invariants

| Invariant (law) | Negation → test | Verified in |
|---|---|---|
| Output is deterministic | run twice, outputs differ | `tests/test_demo.py::test_deterministic` |
| Input is never modified | edit the input, hash differs | `tests/test_demo.py::test_read_only`, also `demo.py` |

## Tickets

- #9 phases 2-4
1. https://example.com/owner/demo/issues/2
"""

AMENDMENT = """
## Plan amendments

| # | Change | Reason | Approved |
|---|---|---|---|
| A1 | phase 6 run | the docs need code after all | 2026-11-17, owner in chat — "Va bene, aggiungi la 6" |
"""


PLAN = SPEC[SPEC.index("## Phase plan"):SPEC.index("## Problem Statement")]
PREMISES = SPEC[SPEC.index("## Premises"):SPEC.index("## Model")]


def swap(old: str, new: str, text: str = SPEC) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A primary-tree git repo with a session claim for FEATURE (isolated)."""
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    claim = root / ".git" / "agentic-sessions" / FEATURE
    claim.parent.mkdir()
    claim.write_text("claimed\n")
    (root / "docs" / "mockups").mkdir(parents=True)
    (root / "docs" / "mockups" / "demo.md").write_text("mockup\n")
    return root


def write_spec(repo: Path, text: str, feature: str = FEATURE) -> None:
    path = repo / "docs" / "specs" / f"{feature}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def statuses(d) -> dict[int, str]:
    return {v.phase: v.status for v in d.verdicts}


def test_derived_phase_is_the_first_invalid_output(repo):
    """Witness: phase 3 present but invalid (empty negation) AND phase 4 not
    started (no ## Tickets), plus a planted state file claiming phase 6 - the
    derived phase is 3 with status `fail`: distinguishes 'first invalid
    output' (3) from 'first not-started' (4) and from 'stored state' (6)."""
    spec = SPEC[:SPEC.index("## Tickets")].replace("| run twice, outputs differ |", "|  |")
    write_spec(repo, spec)
    (repo / "docs" / "specs" / f"{FEATURE}.state.json").write_text(
        json.dumps({"phase": 6}))
    d = wf.derive(repo, FEATURE)
    assert d.phase == 3
    assert statuses(d) == {0: "pass", 1: "pass", 2: "pass", 3: "fail"}
    assert d.verdicts[-1].problems == ("invariant row 1 has an empty negation",)


# --- synthetic kit: WORKFLOW.md, phase files, transversal files, skills -----

PIPELINE_SKILLS = {1: ("s1",), 2: ("s2",), 3: ("s3",), 4: ("s4",), 5: ("s5",), 6: ("s6",)}


def make_kit(root: Path, skills: dict[int, tuple[str, ...]] | None = None) -> tuple[Path, Path]:
    """Write a synthetic WORKFLOW.md (+ files it names) and a skills store
    under `root`; return (workflow_dir, skills_dir). Every skill body is the
    marker `BODY-<name>`."""
    skills = skills or PIPELINE_SKILLS
    wdir, sdir = root / "workflow", root / "skills"
    (wdir / "phases").mkdir(parents=True)
    rows = "\n".join(
        f"| {n} | Phase {n} | {', '.join(f'`{s}`' for s in names)} | "
        f"{'below' if n == 4 else f'`phases/{n}-x.md`'} |"
        for n, names in sorted(skills.items())
    )
    (wdir / "WORKFLOW.md").write_text(
        "# W\n\n## Pipeline\n\n| # | Phase | Skill(s) | Rules |\n|---|---|---|---|\n"
        f"{rows}\n\nTransversal files:\n\n| File | Read when |\n|---|---|\n"
        "| `REVIEW-CRITERIA.md` | phase 6 — paste into every reviewer task |\n"
        "| `SESSION-ISOLATION.md` | on entering a repo |\n"
        "| `LESSONS.md` | before phase 1 (section A), phase 6 (B, C) |\n\n"
        "### 4. Tickets\n\nBODY-section-4\n\n"
        "## General rules\n\nBODY-general-rules\n\n## Workflow invariants\n\nBODY-tail\n"
    )
    for n in (1, 2, 3, 5, 6):
        (wdir / "phases" / f"{n}-x.md").write_text(f"BODY-phase-file-{n}\n")
    for name in ("REVIEW-CRITERIA", "SESSION-ISOLATION", "LESSONS", "INVARIANTS"):
        (wdir / f"{name}.md").write_text(f"BODY-{name}\n")
    for names in skills.values():
        for name in names:
            write_skill(sdir, name, f"BODY-{name}")
    return wdir, sdir


def write_skill(sdir: Path, name: str, body: str) -> None:
    (sdir / name).mkdir(parents=True, exist_ok=True)
    (sdir / name / "SKILL.md").write_text(body + "\n")


@pytest.fixture
def kit(tmp_path: Path) -> tuple[Path, Path]:
    return make_kit(tmp_path / "kit")


def evidence_store(kit: tuple[Path, Path]) -> Path:
    """The store `run_wf` reads when it is given none: a fresh one under the kit's parent (tmp_path),
    made by importing the evidence files of the kit's `reports` and `handoffs` dirs the way
    `wf.py report import` does. The files are fixtures only (the harness reads the store alone);
    with no `reports` dir there is no store file."""
    path = kit[0].parent / "env-store" / "reports.sqlite"
    path.unlink(missing_ok=True)
    if (kit[0] / "reports").is_dir():
        mod = wf._report_store()
        conn = mod.open_store(path)
        try:
            mod.import_files(conn, kit[0], [Path(d) for d in ("reports", "handoffs") if (kit[0] / d).is_dir()],
                             datetime.now())
        finally:
            conn.close()
    return path


FAKE_GH = """#!{python}
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\\n")
path = os.path.join(d, "issue-" + (args[2] if len(args) > 2 else "") + ".json")
if args[:2] == ["issue", "view"]:
    if os.path.exists(path):
        sys.stdout.write(open(path).read())
    elif os.environ.get("FAKE_GH_DEFAULT") == "solution":
        print(json.dumps({{"number": int(args[2]), "state": "OPEN", "body": "## Solution", "comments": []}}))
    else:
        sys.stderr.write("could not resolve to an Issue\\n")
        sys.exit(1)
elif args[:2] == ["issue", "comment"] and os.path.exists(path) and "--body-file" in args:
    body = sys.stdin.read()
    issue = json.load(open(path))
    issue["comments"].append({{"body": body}})
    json.dump(issue, open(path, "w"))
    with open(os.path.join(d, "posted.log"), "a") as posted:
        posted.write(json.dumps({{"number": int(args[2]), "args": args, "body": body}}) + "\\n")
else:
    sys.stderr.write("fake gh: refused " + " ".join(args) + "\\n")
    sys.exit(1)
"""


def fake_gh_dir(root: Path, default_solution: bool = False) -> dict[str, str]:
    """A `gh` executable (python script) in `root/bin`, first on PATH in the returned env; synthetic
    stand-in for GitHub (design `External fixtures`): `issue view N` answers `root/issue-N.json`,
    `issue comment N --body-file -` appends its stdin to that issue's comments and to `posted.log`,
    everything else (`issue edit` included) exits 1; every call is logged in `calls.log`."""
    script = root / "bin" / "gh"
    if not script.exists():
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(FAKE_GH.format(python=sys.executable))
        script.chmod(0o755)
    env = {"PATH": f"{script.parent}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_DIR": str(root)}
    if default_solution:
        env["FAKE_GH_DEFAULT"] = "solution"
    return env


class FakeGh:
    def __init__(self, root: Path):
        self.root = root
        self.env = {**fake_gh_dir(root), "FAKE_GH_DEFAULT": ""}

    def issue(self, number: int, body: str = "", comments: tuple[str, ...] = (), state: str = "OPEN") -> None:
        (self.root / f"issue-{number}.json").write_text(json.dumps(
            {"number": number, "state": state, "body": body, "comments": [{"body": c} for c in comments]}))

    def calls(self) -> list[str]:
        path = self.root / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def posted(self) -> list[dict]:
        path = self.root / "posted.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def issue_json(self, number: int) -> dict:
        return json.loads((self.root / f"issue-{number}.json").read_text())


@pytest.fixture
def fake_gh(tmp_path: Path) -> FakeGh:
    return FakeGh(tmp_path / "fake-gh")


def run_wf(repo: Path, kit: tuple[Path, Path], command: str, feature: str = FEATURE,
           presence_dir: Path | None = None, store: Path | None = None, env: dict | None = None):
    """The store is always under the kit's parent (tmp_path): `--store` when given, else
    `$AGENT_REPORT_STORE` (`evidence_store`), so no run can reach the real one."""
    flags = ["--store", str(store)] if store else []
    default_store = evidence_store(kit) if store is None else None
    # `gh` is always the fake one (a ticket carrying `## Solution`): no run reaches GitHub (#116)
    gh = fake_gh_dir(kit[0].parent / "gh-default", default_solution=True)
    return subprocess.run(
        [sys.executable, str(WF), command, feature, "--repo-root", str(repo),
         "--workflow-dir", str(kit[0]), "--skills-dir", str(kit[1]),
         "--presence-dir", str(presence_dir or kit[0].parent / "presence"), *flags],
        capture_output=True, text=True, check=False,
        env={**os.environ, "AGENT_REPORT_STORE": str(default_store or store), **gh, **(env or {})},
    )


def test_next_refuses_when_an_earlier_phase_is_invalid(repo, kit):
    """Witness: phase 1 invalid (a premise's command emptied) while phase 2
    would be valid (approved skip) - distinguishes 'refuse at the first
    invalid phase' (exit 1, phase-1 bundle) from 'look only at the last
    phase' (exit 0)."""
    write_spec(repo, swap("`echo blue`", ""))
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1, r.stdout
    assert "FAIL phase 1" in r.stdout and "empty command" in r.stdout
    assert "BODY-phase-file-1" in r.stdout


def test_next_exits_zero_when_the_next_phase_is_not_started(repo, kit):
    """Witness: a spec holding only phase 0 (no phase-1 section, no tracker)
    - distinguishes 'phase 1 not started = todo, exit 0, phase-1 bundle'
    from 'phase 1 invalid = exit 1'."""
    write_spec(repo, "---\nfeature: demo\nrepo: o/d\nbranch: b\n---\n\n"
               "## Phase plan\n\n| Phase | Decision | Reason |\n|---|---|---|\n"
               + "".join(f"| {n} | run | — |\n" for n in range(7))
               + '\nApproved: 2026-11-16, owner in chat — "Ok"\n')
    r = run_wf(repo, kit, "next")
    assert r.returncode == 0, r.stdout
    assert "TODO phase 1" in r.stdout and "BODY-phase-file-1" in r.stdout


def test_skip_without_approval_is_not_a_skip(repo, kit):
    """Witness: skips with reasons but no Approved: line - distinguishes
    'phase 2 not skipped, derivation stops at 0' from 'skips honoured'
    (a SKIP line, exit 0)."""
    write_spec(repo, swap('Approved: 2026-11-16, owner in chat — "Approvo" (plan as above).\n', ""))
    r = run_wf(repo, kit, "status")
    assert r.returncode == 1
    assert "FAIL phase 0: no Approved: line" in r.stdout
    assert "SKIP" not in r.stdout


def test_phase_zero_cannot_be_skipped(repo, kit):
    """Witness: a plan row `0 skip` with approval and a reason, valid
    otherwise; and no spec at all - distinguishes 'phase 0 always runs'
    from 'phase 0 skipped, derivation starts at phase 1' (exit 0)."""
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1
    assert "TODO phase 0: no spec file" in r.stdout and "BODY-general-rules" in r.stdout
    write_spec(repo, swap("| 0 · Start | run | worktree |", "| 0 · Start | skip | not needed |"))
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1
    assert "FAIL phase 0: phase 0 cannot be skipped" in r.stdout
    assert "SKIP phase 0" not in r.stdout


def test_invalid_earlier_output_regresses_the_phase(repo):
    """Witness: delivered feature, then a premise's output emptied -
    distinguishes 'revalidate earlier phases' (derived phase 1) from
    'delivered stays delivered' (None)."""
    write_spec(repo, SPEC)
    assert wf.derive(repo, FEATURE).phase is None
    write_spec(repo, swap("| blue |", "|  |"))
    d = wf.derive(repo, FEATURE)
    assert d.phase == 1
    assert d.verdicts[-1].problems == ("premise P1 has an empty output",)


def test_approved_amendment_changes_the_effective_plan(repo):
    """Witness: delivered plan amended (approved) to run phase 6 -
    distinguishes 'amendments applied' (phase 6, unimplemented) from
    'amendments ignored' (delivered)."""
    write_spec(repo, SPEC + AMENDMENT)
    d = wf.derive(repo, FEATURE)
    assert d.phase == 6
    assert statuses(d)[5] == "skip"


# Each case breaks exactly one check row of phase 0 or 1 in the valid SPEC.
CHECK_CASES = [
    ("plan-row-missing", "| 4 · Tickets | run | one ticket |\n", "", 0, "no row for phase 4"),
    ("skip-without-reason", "| 5 · Program design | skip | no new code |",
     "| 5 · Program design | skip | — |", 0, "phase 5: skip without a reason"),
    ("no-approved-line", 'Approved: 2026-11-16, owner in chat — "Approvo" (plan as above).\n', "",
     0, "no Approved: line"),
    ("approval-without-quote", ' — "Approvo" (plan as above)', " — fine", 0, "no quoted owner answer"),
    ("unapproved-amendment", AMENDMENT.split("| 2026")[0] + "| |\n", None, 0, "amendment A1 is not approved"),
    ("no-problem-statement", "## Problem Statement\n\nSomething is wrong.\n", "", 1, "no ## Problem Statement"),
    ("no-solution", "## Solution\n\nFix it.\n", "", 1, "no ## Solution"),
    ("no-user-stories", "1. As an owner, I want it fixed, so that it works.", "(none yet)",
     1, "no numbered story"),
    ("premises-table-empty", ("| P1 | The sky | `echo blue` | blue | confirmed |\n"
     "| P2 | Pipes \\| survive | `ls \\| wc -l` | 3 | falsified — it was 4 |\n"), "", 1, "no rows"),
    ("premise-empty-command", "`echo blue`", "", 1, "premise P1 has an empty command"),
    ("premise-empty-verdict", "| confirmed |", "|  |", 1, "premise P1 has an empty verdict"),
    ("premise-empty-output", "| blue |", "|  |", 1, "premise P1 has an empty output"),
    ("verdict-not-decided", "| confirmed |", "| maybe |", 1, "verdict must be confirmed or falsified"),
    ("model-without-inventory", "### Lifecycle inventory", "### Entities", 1, "no ### Lifecycle inventory"),
    ("model-without-mermaid", "```mermaid", "```text", 1, "no mermaid block"),
    ("no-feature", "feature: demo\n", "", 0, "front matter has no feature"),
    ("no-repo", "repo: owner/demo\n", "", 0, "front matter has no repo"),
    ("no-branch", "branch: feature/demo\n", "", 0, "front matter has no branch"),
    ("no-phase-plan", PLAN, "", 0, "no ## Phase plan"),
    ("decision-not-run-or-skip", "| 0 · Start | run | worktree |", "| 0 · Start | maybe | worktree |",
     0, "decision must be run or skip"),
    ("plan-row-without-number", "| 4 · Tickets", "| Tickets | run | y |\n| 4 · Tickets",
     0, "without a phase number"),
    ("amendment-malformed-change", AMENDMENT.replace("phase 6 run", "phase six run"), None,
     0, "change must read"),
    ("amendment-skip-without-reason",
     AMENDMENT.replace("phase 6 run | the docs need code after all", "phase 6 skip | —"), None,
     0, "amendment A1: skip without a reason"),
    ("no-user-stories-heading", "## User Stories\n\n1. As an owner, I want it fixed, so that it works.\n\n",
     "", 1, "no ## User Stories"),
    ("no-premises-heading", PREMISES, "", 1, "no ## Premises"),
    ("no-model-heading", SPEC[SPEC.index("## Model"):], "", 1, "no ## Model"),
    ("empty-problem-statement", "Something is wrong.", "", 1, "no ## Problem Statement"),
    ("empty-solution", "Fix it.", "", 1, "no ## Solution"),
    ("heading-inside-a-fence", "## User Stories\n", "```\n## User Stories\n```\n", 1, "no ## User Stories"),
    ("premise-empty-premise", "| P1 | The sky |", "| P1 |  |", 1, "premise P1 has an empty premise"),
    ("no-tracker", "tracker: https://example.com/owner/demo/issues/1\n", "", 1, "no tracker"),
    ("tracker-not-a-reference", "https://example.com/owner/demo/issues/1", "soon", 1, "is not a URL"),
    ("review-names-no-path", "Mockup: `docs/mockups/demo.md`", "Mockup: none", 2,
     "## Product review names no path"),
    ("review-path-does-not-exist", "`docs/mockups/demo.md`", "`docs/mockups/other.md`", 2,
     "mockup path docs/mockups/other.md does not exist"),
    ("no-invariants-subsection", "### System invariants", "### Constraints", 3,
     "## System Architecture has no ### System invariants"),
    ("invariant-without-a-law", "| Output is deterministic |", "|  |", 3, "invariant row 1 has an empty law"),
    ("invariant-without-a-negation", "| run twice, outputs differ |", "|  |", 3,
     "invariant row 1 has an empty negation"),
    ("invariant-verified-in-without-a-reference", "| `tests/test_demo.py::test_read_only`, also `demo.py` |",
     "| tbd |", 3, "invariant row 2 has no back-ticked Verified in"),
    ("invariants-table-without-rows",
     ("| Output is deterministic | run twice, outputs differ | `tests/test_demo.py::test_deterministic` |\n"
      "| Input is never modified | edit the input, hash differs | `tests/test_demo.py::test_read_only`, also `demo.py` |\n"),
     "", 3, "invariants table has no rows"),
    ("model-without-a-sequence-diagram", "sequenceDiagram", "flowchart LR", 3,
     "## Model has no sequenceDiagram"),
    ("tickets-without-an-item", "- #9 phases 2-4\n1. https://example.com/owner/demo/issues/2\n",
     "(none yet)\n", 4, "## Tickets has no item"),
    ("ticket-without-a-reference", "- #9 phases 2-4", "- phases 2-4", 4,
     "ticket item without a tracker reference"),
]


@pytest.mark.parametrize("old,new,phase,problem", [c[1:] for c in CHECK_CASES],
                         ids=[c[0] for c in CHECK_CASES])
def test_each_phase_check_can_fail(repo, kit, old, new, phase, problem):
    """Witness: the valid SPEC with this one row broken - distinguishes a
    check that fires (exit 1, one named problem, derived phase = `phase`)
    from a check that is missing (delivered, exit 0). The empty-verdict case
    is a characterization test: the code already enforced it."""
    if new is None:  # amendment cases: append the table instead
        text = SPEC + old
    else:
        assert old in SPEC, old
        text = SPEC.replace(old, new)
    write_spec(repo, text)
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1, r.stdout
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL phase")]
    assert len(fails) == 1 and problem in fails[0], r.stdout
    assert fails[0].startswith(f"FAIL phase {phase}:")


def test_no_domain_laws_replaces_the_invariants_table(repo):
    """Witness: the System Architecture section reduced to the words `no
    domain laws` - distinguishes 'accepted' (delivered) from 'table required'."""
    body = SPEC[SPEC.index("### System invariants"):SPEC.index("## Tickets")]
    write_spec(repo, swap(body, "This feature has no domain laws: it only moves files.\n\n"))
    assert wf.derive(repo, FEATURE).phase is None


def test_a_missing_section_of_phases_2_to_4_is_todo_not_fail(repo):
    """Witness: each of the sections dropped in turn (plan runs the phase) -
    distinguishes 'not started' (todo, the harness hands out the phase)
    from 'started and invalid' (fail)."""
    for name, phase in (("Product review", 2), ("System Architecture", 3), ("Tickets", 4)):
        text = SPEC
        start = text.index(f"## {name}")
        end = text.find("\n## ", start + 1)
        write_spec(repo, text[:start] + (text[end + 1:] if end != -1 else ""))
        d = wf.derive(repo, FEATURE)
        assert (d.phase, statuses(d)[phase]) == (phase, "todo"), name


def test_model_skipped_line_replaces_the_model_section(repo):
    """Witness: no ## Model but a `Model skipped — <reason>` line -
    distinguishes 'skip line accepted' (delivered) from 'Model required'."""
    text = swap(SPEC[SPEC.index("## Model"):SPEC.index("## Product review")],
                "Model skipped — no entities move in time.\n\n")
    write_spec(repo, text)
    assert wf.derive(repo, FEATURE).phase is None


def test_bundle_follows_the_pipeline_table(tmp_path):
    """Witness: skill `extra` added to the phase-5 row of the table -
    distinguishes a table-driven bundle (extra printed, only for phase 5)
    from a hard-coded skill list."""
    skills = {**PIPELINE_SKILLS, 5: ("s5", "extra")}
    wdir, sdir = make_kit(tmp_path, skills)
    text = wf.bundle(5, wdir, sdir)
    assert "=== skill: s5 ===" in text and "=== skill: extra ===" in text
    assert "BODY-phase-file-5" in text
    assert "BODY-extra" not in wf.bundle(6, wdir, sdir)


def test_bundle_prints_transversal_files_of_the_phase_and_section_four(kit):
    """Witness: phase 6 vs phase 4 - REVIEW-CRITERIA and LESSONS only where
    the 'Read when' cell says `phase N`; phase 4 has no phase file, its
    rules are the `### 4. Tickets` section."""
    wdir, sdir = kit
    six = wf.bundle(6, wdir, sdir)
    assert "BODY-REVIEW-CRITERIA" in six and "BODY-LESSONS" in six
    assert "BODY-SESSION-ISOLATION" not in six
    four = wf.bundle(4, wdir, sdir)
    assert "BODY-section-4" in four and "BODY-LESSONS" not in four


def test_phase_zero_prints_lessons_only_when_the_file_exists(kit):
    """Witness (spec invariant 6): the same install with and without `LESSONS.md` - distinguishes
    an optional file (printed between INVARIANTS.md and the phase-0 file when there, silently
    absent otherwise) from a required one (FileNotFoundError) or a dropped one (never printed)."""
    wdir, sdir = kit
    (wdir / "phases" / "0-start.md").write_text("BODY-phase-0\n")
    with_lessons = wf.bundle(0, wdir, sdir)
    assert "BODY-LESSONS" in with_lessons
    order = [with_lessons.index(m) for m in ("BODY-SESSION-ISOLATION", "BODY-INVARIANTS", "BODY-LESSONS", "BODY-phase-0")]
    assert order == sorted(order)
    (wdir / "LESSONS.md").unlink()
    without = wf.bundle(0, wdir, sdir)
    assert "BODY-LESSONS" not in without and "file: LESSONS.md" not in without
    for body in ("BODY-general-rules", "BODY-SESSION-ISOLATION", "BODY-INVARIANTS", "BODY-phase-0"):
        assert body in without, body


def test_phase0_files_lists_the_required_files_then_each_optional_one_that_exists(kit):
    """Witness the install with `LESSONS.md`, without it, and without INVARIANTS.md - distinguishes
    an answer that checks the optional file's presence from a constant tuple, and a required file
    that stays named (so `bundle` raises on it) from one silently dropped."""
    wdir, sdir = kit
    assert wf.phase0_files(wdir) == ("SESSION-ISOLATION.md", "INVARIANTS.md", "LESSONS.md")
    (wdir / "LESSONS.md").unlink()
    assert wf.phase0_files(wdir) == ("SESSION-ISOLATION.md", "INVARIANTS.md")
    (wdir / "INVARIANTS.md").unlink()
    assert wf.phase0_files(wdir) == ("SESSION-ISOLATION.md", "INVARIANTS.md")
    with pytest.raises(FileNotFoundError, match="INVARIANTS.md"):
        wf.bundle(0, wdir, sdir)


def test_wrapper_skills_resolved_one_level(tmp_path):
    """Witness: wrapper `wrap` (phase 1) referencing `/leaf1`, `skill://leaf2`
    and `/other` (a phase-2 skill); leaf1 references `/deep` - distinguishes
    'one level, own-phase only' from 'flat (no leaves)', 'recursive (deep)'
    and 'any referenced skill (other)'."""
    wdir, sdir = make_kit(tmp_path, {**PIPELINE_SKILLS, 1: ("wrap",), 2: ("other",)})
    write_skill(sdir, "wrap", "BODY-wrap run /leaf1 then skill://leaf2 and /other; see /home/x")
    write_skill(sdir, "leaf1", "BODY-leaf1 uses /deep")
    write_skill(sdir, "leaf2", "BODY-leaf2")
    write_skill(sdir, "deep", "BODY-deep")
    text = wf.bundle(1, wdir, sdir)
    assert "BODY-leaf1" in text and "BODY-leaf2" in text
    assert "BODY-deep" not in text
    assert "BODY-other" not in text
    assert "referenced, not included: other" in text


def tree_digest(root: Path) -> dict[str, str]:
    """Relative path -> content hash of every file and symlink under `root`,
    .git included, plus every directory (hash '-')."""
    import hashlib
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "link:" + os.readlink(p)
        elif p.is_dir():
            out[rel] = "-"
        else:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_next_does_not_modify_the_work_repo(repo, kit):
    """Witness: `next` and `status` on an invalid and a delivered spec in a
    repo with a commit - distinguishes a read-only harness from one that
    rewrites a file in place or writes inside .git (a content hash of every
    file under the root, .git included, would change)."""
    for text in (swap("`echo blue`", ""), SPEC):
        write_spec(repo, text)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "spec")
        before = tree_digest(repo)
        run_wf(repo, kit, "next")
        run_wf(repo, kit, "status")
        assert tree_digest(repo) == before


def test_next_refuses_in_a_primary_tree_without_a_claim(repo, kit):
    """Witness: a primary tree with no claim - distinguishes 'isolation is
    checked by next' (exit 1, `not isolated`, no bundle) from no check."""
    write_spec(repo, swap("`echo blue`", ""))
    (repo / ".git" / "agentic-sessions" / FEATURE).unlink()
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1
    assert "not isolated" in r.stdout and "BODY-phase-file-1" not in r.stdout


def test_status_never_checks_isolation(repo, kit):
    """Witness: delivered spec in an unclaimed primary tree - `status` exits 0
    (a delivered feature must not regress when its worktree is gone); `next`
    on it also exits 0 because isolation is only required below delivered."""
    write_spec(repo, SPEC)
    (repo / ".git" / "agentic-sessions" / FEATURE).unlink()
    assert run_wf(repo, kit, "status").returncode == 0
    assert run_wf(repo, kit, "next").returncode == 0


def test_next_proceeds_in_a_linked_worktree(tmp_path, kit):
    """Witness: linked worktree, no claim file - distinguishes 'git-dir !=
    git-common-dir counts as isolated' from 'claim file only'."""
    primary = tmp_path / "primary"
    primary.mkdir()
    git(primary, "init", "-q")
    git(primary, "commit", "-q", "--allow-empty", "-m", "x")
    linked = tmp_path / "linked"
    git(primary, "worktree", "add", "-q", "-b", "feature/demo", str(linked))
    write_spec(linked, swap("`echo blue`", ""))
    r = run_wf(linked, kit, "next")
    assert "not isolated" not in r.stdout
    assert "BODY-phase-file-1" in r.stdout


def test_an_approved_amendment_cannot_skip_phase_zero(repo, kit):
    """Characterization (code existed): approved `phase 0 skip` amendment -
    distinguishes 'phase 0 always runs' from 'amendment skips it'."""
    write_spec(repo, SPEC + AMENDMENT.replace("phase 6 run", "phase 0 skip"))
    r = run_wf(repo, kit, "status")
    assert "FAIL phase 0: phase 0 cannot be skipped" in r.stdout
    assert "SKIP phase 0" not in r.stdout


def test_status_on_an_unfinished_feature_never_prints_not_isolated(repo, kit):
    """Characterization: invalid spec in an unclaimed primary tree - `status`
    reports the phase but never the isolation refusal (that is `next` only)."""
    write_spec(repo, swap("`echo blue`", ""))
    (repo / ".git" / "agentic-sessions" / FEATURE).unlink()
    r = run_wf(repo, kit, "status")
    assert r.returncode == 1 and "not isolated" not in r.stdout


def test_a_linked_worktree_path_with_a_space_is_isolated(tmp_path, kit):
    """Witness: linked worktree whose primary repo path contains a space (git
    prints the absolute git-dir/common-dir, so the paths contain it) - distinguishes
    line-based parsing of `git rev-parse` from whitespace splitting (which
    reads 4 tokens and says not isolated)."""
    primary = tmp_path / "primary repo"
    primary.mkdir()
    git(primary, "init", "-q")
    git(primary, "commit", "-q", "--allow-empty", "-m", "x")
    linked = tmp_path / "linked tree"
    git(primary, "worktree", "add", "-q", "-b", "feature/demo", str(linked))
    write_spec(linked, swap("`echo blue`", ""))
    r = run_wf(linked, kit, "next")
    assert "not isolated" not in r.stdout
    assert "BODY-phase-file-1" in r.stdout


def test_bundle_prints_the_phase_file_named_by_the_pipeline_table(tmp_path):
    """Witness: a decoy `phases/3-a-decoy.md` sorts before the file the Rules
    column names (`phases/3-x.md`) - distinguishes 'table-driven' from glob."""
    wdir, sdir = make_kit(tmp_path)
    (wdir / "phases" / "3-a-decoy.md").write_text("BODY-decoy\n")
    text = wf.bundle(3, wdir, sdir)
    assert "BODY-phase-file-3" in text and "BODY-decoy" not in text
    assert "BODY-section-4" in wf.bundle(4, wdir, sdir)


def test_a_skill_body_mentioning_missing_skill_is_not_a_missing_skill(repo, kit):
    """Witness: skill s1 present, its body containing the phrase - distinguishes
    a structural missing-skill signal (exit 1) from a text search (exit 2)."""
    write_skill(kit[1], "s1", "BODY-s1 says MISSING SKILL here")
    write_spec(repo, swap("`echo blue`", ""))
    r = run_wf(repo, kit, "next")
    assert r.returncode == 1, r.stderr
    assert "BODY-s1 says MISSING SKILL here" in r.stdout


def test_undecodable_spec_is_exit_two_naming_the_path(repo, kit):
    """Exit-2 path: spec file that is not UTF-8; the message names the file."""
    path = repo / "docs" / "specs" / f"{FEATURE}.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\xff\xfe\n")
    r = run_wf(repo, kit, "next")
    assert r.returncode == 2
    assert str(path) in r.stderr


def test_a_phase_skill_missing_from_the_store_is_exit_two(repo, kit):
    """Exit-2 path (characterization): the phase's skill is not in the store."""
    import shutil
    shutil.rmtree(kit[1] / "s1")
    write_spec(repo, swap("`echo blue`", ""))
    r = run_wf(repo, kit, "next")
    assert r.returncode == 2
    assert "MISSING SKILL s1" in r.stdout


def test_a_pipeline_table_missing_a_phase_row_is_exit_two(repo, tmp_path):
    """Exit-2 path (characterization): WORKFLOW.md Pipeline has no phase-3 row."""
    kit2 = make_kit(tmp_path / "kit2", {k: v for k, v in PIPELINE_SKILLS.items() if k != 3})
    write_spec(repo, swap("`echo blue`", ""))
    r = run_wf(repo, kit2, "next")
    assert r.returncode == 2
    assert "phase 3" in r.stderr


def test_invalid_feature_slug_is_a_usage_error(repo, kit):
    """Witness: a slug with a path separator - exit 2 before any read."""
    assert run_wf(repo, kit, "next", "../x").returncode == 2


def test_missing_section_four_fails_loudly(repo, tmp_path):
    """Witness: derived phase 4 but WORKFLOW.md has no `### 4. Tickets` -
    distinguishes 'exit 2 naming the section' from an empty rules block
    (silent truncation)."""
    write_spec(repo, SPEC[:SPEC.index("## Tickets")])
    wdir, sdir = make_kit(tmp_path / "nosec")
    workflow = wdir / "WORKFLOW.md"
    workflow.write_text(workflow.read_text().replace("### 4. Tickets", "### 4. Other"))
    r = subprocess.run(
        [sys.executable, str(WF), "next", FEATURE, "--repo-root", str(repo),
         "--workflow-dir", str(wdir), "--skills-dir", str(sdir),
         "--presence-dir", str(tmp_path / "presence")],
        capture_output=True, text=True, check=False)
    assert r.returncode == 2, r.stdout
    assert "WORKFLOW.md has no '### 4. Tickets' section" in r.stderr


# --- wf end (#11) -----------------------------------------------------------

def test_end_with_an_invalid_feature_slug_is_a_usage_error(tmp_path):
    """Witness: `wf end ../x` - exit 2 naming the slug, before it reaches a path."""
    r = subprocess.run([sys.executable, str(WF), "end", "../x",
                        "--workflow-dir", str(tmp_path)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 2
    assert "'../x'" in r.stderr


TODAY = date(2026, 11, 16)
GATE_BULLET = ("- **Every session ends with the transversal gate** — GATE-V1: run the\n"
               "  audit, then write the handoff.\n")
GOOD_HANDOFF = "# Handoff\n\n- **Repo**: owner/demo\n\nRan `python audit.py --repo owner/demo`: clean.\n"


def make_gate_dir(root: Path, bullet: str = GATE_BULLET) -> Path:
    wdir = root / "gate"
    (wdir / "handoffs").mkdir(parents=True)
    (wdir / "WORKFLOW.md").write_text(
        f"# W\n\n## General rules\n\n- **Other rule** — x.\n{bullet}- **After** — y.\n\n## Tail\n\nz\n")
    return wdir


def run_end(wdir: Path, *features: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WF), "end", *features, "--workflow-dir", str(wdir)],
                          capture_output=True, text=True, check=False)


def test_gate_text_is_read_from_the_bullet_not_copied(tmp_path):
    """Witness: the bullet reworded (GATE-V2) - distinguishes 'read from
    WORKFLOW.md' (V2 printed, V1 gone, neighbouring bullets excluded) from
    a copied constant (V1)."""
    wdir = make_gate_dir(tmp_path)
    text = wf.session_gate_text(wdir)
    assert "GATE-V1" in text and "write the handoff." in text
    assert "Other rule" not in text and "After" not in text
    (wdir / "WORKFLOW.md").write_text(
        (wdir / "WORKFLOW.md").read_text().replace("GATE-V1", "GATE-V2"))
    text = wf.session_gate_text(wdir)
    assert "GATE-V2" in text and "GATE-V1" not in text
    assert "GATE-V2" in run_end(wdir, "demo").stdout


def test_end_without_the_gate_bullet_is_exit_two(tmp_path):
    """Witness: WORKFLOW.md whose General rules has no gate bullet -
    distinguishes a setup error (exit 2, stderr names the bullet) from an
    empty gate text with the checks run (exit 1)."""
    wdir = make_gate_dir(tmp_path, bullet="")
    r = run_end(wdir, "demo")
    assert r.returncode == 2, r.stdout
    assert "Every session ends with the transversal gate" in r.stderr


def test_end_passes_with_a_complete_handoff_dated_today(tmp_path):
    """Witness: a handoff of today (Repo line, audit command) - distinguishes
    'all checks pass' (empty tuple, exit 0) from a harness that always fails."""
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / "handoff-demo-20261116.md").write_text(GOOD_HANDOFF)
    assert wf.end_checks(wdir, TODAY, ("demo",)) == ()


@pytest.mark.parametrize("name,content,problem", [
    ("handoff-demo-20261115.md", GOOD_HANDOFF, "no handoff-demo-20261116.md"),
    ("notes-20261116.md", GOOD_HANDOFF, "no handoff-demo-20261116.md"),
    ("handoff-demo-20261116.md", GOOD_HANDOFF.replace("- **Repo**: owner/demo", "Repo: owner/demo"),
     "has no '- **Repo**:' line"),
    ("handoff-demo-20261116.md", GOOD_HANDOFF.replace("audit.py --repo", "audit.py"),
     "does not contain 'audit.py --repo'"),
], ids=["only-yesterday", "wrong-prefix", "no-repo-line", "no-audit-command"])
def test_end_names_each_missing_handoff_element(tmp_path, name, content, problem):
    """Witness: a handoff of today missing exactly one element (or dated
    yesterday / not named handoff-*) - distinguishes a check that fires with
    the named problem from one that is missing (empty tuple)."""
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / name).write_text(content)
    problems = wf.end_checks(wdir, TODAY, ("demo",))
    assert len(problems) == 1 and problem in problems[0], problems


def test_end_with_no_handoff_exits_one_and_names_it(tmp_path):
    """Witness: empty handoffs dir via the CLI - exit 1 with one problem line
    after the gate text, distinguishes 'refuse' from exit 0."""
    wdir = make_gate_dir(tmp_path)
    r = run_end(wdir, "demo")
    assert r.returncode == 1, r.stdout
    assert "GATE-V1" in r.stdout
    assert f"no handoff-demo-{date.today():%Y%m%d}.md" in r.stdout


def test_end_exits_zero_when_the_handoff_of_the_real_today_is_complete(tmp_path):
    """Witness: handoff dated with the process's real today - distinguishes
    `main` passing date.today() (exit 0) from a fixed date (exit 1)."""
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / f"handoff-demo-{date.today():%Y%m%d}.md").write_text(GOOD_HANDOFF)
    r = run_end(wdir, "demo")
    assert r.returncode == 0, r.stdout


def test_end_checks_only_the_named_features_handoff(tmp_path):
    """Witness: today's handoff of `demo` incomplete, today's handoff of
    `other` complete - distinguishes 'check the named feature' (exit 1,
    names demo) from 'best handoff of the day' (exit 0)."""
    wdir = make_gate_dir(tmp_path)
    stamp = f"{date.today():%Y%m%d}"
    (wdir / "handoffs" / f"handoff-demo-{stamp}.md").write_text(
        GOOD_HANDOFF.replace("audit.py --repo", "audit.py"))
    (wdir / "handoffs" / f"handoff-other-{stamp}.md").write_text(GOOD_HANDOFF)
    r = run_end(wdir, "demo")
    assert r.returncode == 1, r.stdout
    assert f"handoff-demo-{stamp}.md does not contain 'audit.py --repo'" in r.stdout


def test_end_ignores_the_complete_handoff_of_another_feature(tmp_path):
    """Witness: only `other` has a complete handoff today - distinguishes
    'no handoff of the named feature' (exit 1) from accepting any of today's."""
    wdir = make_gate_dir(tmp_path)
    stamp = f"{date.today():%Y%m%d}"
    (wdir / "handoffs" / f"handoff-other-{stamp}.md").write_text(GOOD_HANDOFF)
    r = run_end(wdir, "demo")
    assert r.returncode == 1, r.stdout
    assert f"no handoff-demo-{stamp}.md" in r.stdout


def test_end_with_two_complete_features_exits_zero(tmp_path):
    """Witness: two features named, both handoffs complete - distinguishes
    'every named feature is checked and passes' (exit 0) from checking one."""
    wdir = make_gate_dir(tmp_path)
    stamp = f"{date.today():%Y%m%d}"
    for name in ("demo", "other"):
        (wdir / "handoffs" / f"handoff-{name}-{stamp}.md").write_text(GOOD_HANDOFF)
    assert run_end(wdir, "demo", "other").returncode == 0


def test_end_reports_the_failing_feature_of_two(tmp_path):
    """Witness: two features named, only `other` incomplete - distinguishes
    per-feature reporting (the line names `other`, not `demo`) from a bare failure."""
    wdir = make_gate_dir(tmp_path)
    stamp = f"{date.today():%Y%m%d}"
    (wdir / "handoffs" / f"handoff-demo-{stamp}.md").write_text(GOOD_HANDOFF)
    r = run_end(wdir, "demo", "other")
    assert r.returncode == 1, r.stdout
    assert f"no handoff-other-{stamp}.md" in r.stdout and "handoff-demo" not in r.stdout


def test_end_without_a_feature_is_a_usage_error(tmp_path):
    """Witness: `wf end` with no feature - distinguishes argparse exit 2
    from a run that checks nothing (exit 0)."""
    assert run_end(make_gate_dir(tmp_path)).returncode == 2


# --- wf end applies the audit's I9 and I10 to the handoff (#73) ---------------

def handoff_with(body: str, repo_line: str = "- **Repo**: owner/demo") -> str:
    """A handoff that passes the three original checks, plus `body`."""
    return f"# Handoff\n\n{repo_line}\n\nOutcome of audit.py --repo owner/demo: clean.\n\n{body}"


def end_problems(tmp_path: Path, body: str, refs: set[int] | None = None, *,
                 repo_line: str = "- **Repo**: owner/demo",
                 calls: list[str] | None = None) -> tuple[str, ...]:
    """end_checks on a handoff of TODAY with a fake tracker returning `refs`, never the network."""
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / "handoff-demo-20261116.md").write_text(handoff_with(body, repo_line))

    def fake_refs(repo: str) -> set[int]:
        if calls is not None:
            calls.append(repo)
        return set(refs or ())
    return wf.end_checks(wdir, TODAY, ("demo",), known_refs=fake_refs)


def test_end_fails_on_a_follow_up_with_no_tracker_reference(tmp_path):
    problems = end_problems(tmp_path, "## Next session\n\n1. Advisory: tidy the thing\n")
    assert len(problems) == 1
    assert problems[0].startswith("demo: [I9] ")
    assert "names a follow-up with no tracker reference" in problems[0]


def test_end_gives_the_audit_checks_only_to_a_handoff_that_passes_the_basic_ones(tmp_path):
    """A handoff with no `- **Repo**:` line that also has an unreferenced follow-up and an
    unquoted verification fails once, on the missing line, not three times."""
    problems = end_problems(tmp_path, "## Verification\n\n- Suite passed.\n\n## Next session\n\n1. Advisory: tidy\n",
                            repo_line="Repo: owner/demo")
    assert len(problems) == 1
    assert "has no '- **Repo**:' line" in problems[0]


def test_end_fails_on_a_verification_section_that_quotes_no_command(tmp_path):
    problems = end_problems(tmp_path, "## Verification\n\n- Suite passed.\n")
    assert len(problems) == 1
    assert problems[0].startswith("demo: [I10] ")
    assert "quotes no command" in problems[0]


def test_end_passes_a_follow_up_that_cites_a_known_ticket(tmp_path):
    assert end_problems(tmp_path, "## Next session\n\n1. Advisory: tidy the thing (#9)\n", {9}) == ()


def test_end_fails_on_a_follow_up_citing_an_unknown_ticket(tmp_path):
    problems = end_problems(tmp_path, "## Next session\n\n1. Advisory: tidy the thing (#9)\n", {1})
    assert len(problems) == 1
    assert "[I9] " in problems[0] and "cites follow-up #9, which does not resolve" in problems[0]


def test_end_with_an_unreadable_tracker_passes_a_cited_ticket_and_still_fails_an_uncited_follow_up(tmp_path):
    cited = "## Next session\n\n1. Advisory: tidy the thing (#9)\n"
    assert end_problems(tmp_path, cited, set()) == ()
    uncited = end_problems(tmp_path / "b", "## Next session\n\n1. Advisory: tidy\n", set())
    assert len(uncited) == 1 and "names a follow-up with no tracker reference" in uncited[0]


def test_end_reads_the_tracker_only_when_the_handoff_cites_a_number(tmp_path):
    calls: list[str] = []
    end_problems(tmp_path, "## Next session\n\n1. Advisory: tidy\n", calls=calls)
    assert calls == []
    end_problems(tmp_path / "b", "## Next session\n\n1. Advisory: tidy (#9)\n", {9}, calls=calls)
    assert calls == ["owner/demo"]


def test_end_judges_each_features_own_handoff(tmp_path):
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / "handoff-first-20261116.md").write_text(handoff_with("## Next session\n\n1. None.\n"))
    (wdir / "handoffs" / "handoff-second-20261116.md").write_text(
        handoff_with("## Next session\n\n1. Advisory: tidy\n"))
    problems = wf.end_checks(wdir, TODAY, ("first", "second"), known_refs=lambda repo: set())
    assert len(problems) == 1
    assert problems[0].startswith("second: [I9] ")


def test_end_skips_the_new_checks_for_a_handoff_with_no_owner_repo(tmp_path):
    calls: list[str] = []
    problems = end_problems(tmp_path, "## Next session\n\n1. Advisory: tidy (#9)\n",
                            repo_line="- **Repo**: demo", calls=calls)
    assert problems == ()
    assert calls == []

# --- wf end judges the Delivery PRs' `Model:` line (#73 points 2-3) -----------

PR_URL = "https://github.com/owner/demo/pull/"


def delivery_spec(*prs: int) -> str:
    """A spec whose `## Delivery` has one row per PR number (ticket #N+100 each)."""
    rows = "".join(f"- #{n + 100} — PR: {PR_URL}{n} · Model: docs/models/demo.md · Mutation: n/a — x\n"
                   for n in prs)
    return f"---\nfeature: demo\n---\n\n# Spec\n\n## Delivery\n\n{rows}"


def delivery_problems(tmp_path: Path, bodies: dict[int, dict | None], *, spec: str | None = None,
                      handoff: bool = True, calls: list[str] | None = None) -> tuple[str, ...]:
    """end_checks over a repo whose spec lists PRs `bodies`, with a fake reader (never `gh`)
    answering each PR number with its dict (or None)."""
    wdir = make_gate_dir(tmp_path)
    if handoff:
        (wdir / "handoffs" / "handoff-demo-20261116.md").write_text(handoff_with(""))
    repo = tmp_path / "work"
    repo.mkdir()
    text = delivery_spec(*bodies) if spec is None else spec
    if text:
        write_spec(repo, text, "demo")

    def fake_reader(url: str) -> dict | None:
        if calls is not None:
            calls.append(url)
        return bodies[int(url.rsplit("/", 1)[1])]
    return wf.end_checks(wdir, TODAY, ("demo",), known_refs=lambda r: set(),
                         repo_root=repo, pr_reader=fake_reader)


def merged_pr(number: int, body: str) -> dict:
    return {"number": number, "merged": True, "mergedAt": "2026-11-17T10:00:00Z", "body": body}


def test_end_fails_on_a_delivery_pr_with_no_model_line(tmp_path):
    problems = delivery_problems(tmp_path, {5: merged_pr(5, "Closes #73")})
    assert problems == ("demo: [D-051] PR #5 merged without a `Model:` line (path or `n/a — reason`)",)


def test_end_passes_a_delivery_pr_with_a_model_path_or_a_reasoned_skip(tmp_path):
    problems = delivery_problems(tmp_path, {5: merged_pr(5, "Model: docs/models/x.md"),
                                            6: merged_pr(6, "Model: n/a — no component")})
    assert problems == ()


def test_end_fails_a_delivery_pr_with_model_n_a_and_no_reason(tmp_path):
    problems = delivery_problems(tmp_path, {5: merged_pr(5, "Model: n/a")})
    assert len(problems) == 1
    assert problems[0].startswith("demo: ") and "PR #5" in problems[0] and "n/a" in problems[0]


def test_end_does_not_fail_a_delivery_pr_the_reader_cannot_read(tmp_path):
    assert delivery_problems(tmp_path, {5: None}) == ()


def test_end_reads_each_distinct_delivery_pr_once(tmp_path):
    spec = delivery_spec(5, 6) + f"- #300 — PR: {PR_URL}5 · Model: docs/models/demo.md · Mutation: n/a — x\n"
    calls: list[str] = []
    delivery_problems(tmp_path, {5: None, 6: None}, spec=spec, calls=calls)
    assert calls == [f"{PR_URL}5", f"{PR_URL}6"]


def test_end_ignores_a_feature_with_no_spec_or_no_delivery(tmp_path):
    calls: list[str] = []
    assert delivery_problems(tmp_path / "a", {5: merged_pr(5, "x")}, spec="", calls=calls) == ()
    assert delivery_problems(tmp_path / "b", {5: merged_pr(5, "x")},
                             spec="---\nfeature: demo\n---\n\n# Spec\n", calls=calls) == ()
    assert calls == []


def test_end_judges_the_delivery_prs_even_when_the_handoff_fails(tmp_path):
    problems = delivery_problems(tmp_path, {5: merged_pr(5, "no model here")}, handoff=False)
    assert len(problems) == 2
    assert problems[0] == "demo: no handoff-demo-20261116.md"
    assert "[D-051] PR #5" in problems[1]


def gh_answer(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr="")


def test_run_end_passes_the_repo_root_to_the_checks(tmp_path, monkeypatch, capsys):
    """`wf.py end --repo-root DIR` hands DIR to the checks, so the spec read is DIR's, not the cwd's."""
    seen = {}

    def fake_checks(workflow_dir, today, features, **kwargs):
        seen.update(kwargs)
        return ()
    monkeypatch.setattr(wf, "end_checks", fake_checks)
    wdir = make_gate_dir(tmp_path)
    assert wf.main(["end", "demo", "--workflow-dir", str(wdir), "--repo-root", str(tmp_path / "work")]) == 0
    assert seen["repo_root"] == tmp_path / "work"


def test_read_pr_returns_none_for_a_url_that_is_not_a_github_pr_and_when_gh_fails():
    with mock.patch.object(wf.subprocess, "run") as run:
        assert wf.read_pr("https://bitbucket.example.com/projects/P/repos/r/pull-requests/3") is None
        run.assert_not_called()
    github = f"{PR_URL}5"
    with mock.patch.object(wf.subprocess, "run", return_value=gh_answer("", 1)):
        assert wf.read_pr(github) is None
    with mock.patch.object(wf.subprocess, "run", return_value=gh_answer("not json")):
        assert wf.read_pr(github) is None
    with mock.patch.object(wf.subprocess, "run", side_effect=FileNotFoundError("gh")):
        assert wf.read_pr(github) is None
    with mock.patch.object(wf.subprocess, "run", side_effect=wf.subprocess.TimeoutExpired("gh", 30)):
        assert wf.read_pr(github) is None


def test_read_pr_maps_a_merged_pr_to_the_audits_keys():
    url = f"{PR_URL}5"
    merged = json.dumps({"number": 5, "state": "MERGED", "mergedAt": "2026-11-17T10:00:00Z", "body": "Model: n/a — x"})
    with mock.patch.object(wf.subprocess, "run", return_value=gh_answer(merged)) as run:
        pr = wf.read_pr(url)
    assert pr == {"number": 5, "merged": True, "mergedAt": "2026-11-17T10:00:00Z", "body": "Model: n/a — x"}
    assert run.call_args.args[0][:4] == ["gh", "pr", "view", url]
    opened = json.dumps({"number": 5, "state": "OPEN", "mergedAt": None, "body": ""})
    with mock.patch.object(wf.subprocess, "run", return_value=gh_answer(opened)):
        assert wf.read_pr(url)["merged"] is False


# --- phases 5-6 (#10): design gate, Delivery, models, reports, matrix ---------

DEMO_PY = "def main() -> int:\n    return 0\n\n\ndef greet(name):\n    return name\n"
GREET_SIG = "def greet(name: str) -> str: ..."


def design_text(greet_sig: str = GREET_SIG) -> str:
    """A design valid in gate mode whose functions already exist in DEMO_PY."""
    exits = [{"kind": k, "type": "none", "handled_at": "demo.main"} for k in ("returns", "absent", "raises")]
    exits.append({"kind": "effect", "type": "none", "handled_at": None})
    doc = {
        "feature": "demo",
        "call_stack": {"name": "demo.main() -> int", "note": "", "children": [
            {"name": "demo.greet(name) -> str", "note": "", "children": []}]},
        "data_flow": {"nodes": [{"id": "demo.main", "kind": "add", "module": "demo.py"},
                                {"id": "demo.greet", "kind": "add", "module": "demo.py"}],
                      "edges": [{"from": "demo.main", "to": "demo.greet"}]},
        "file_tree": [{"kind": "add", "path": "demo.py", "note": "x"}],
        "signatures": [{"module": "demo.py", "language": "python", "code": "def main() -> int: ..."},
                       {"module": "demo.py", "language": "python", "code": greet_sig}],
        "contracts": [{"site": "demo.main", "callee": "demo.greet", "exits": exits}],
    }
    return f"# Design: demo\n\n```json design\n{json.dumps(doc)}\n```\n"


MODEL_TEXT = """\
---
feature: demo
area: demo
issue: 1
---

# Demo

## Lifecycle inventory

| Entity | Moves in time? | Statechart |
|---|---|---|
| Greeting | no | — |

## Elements

| ID | Kind | Code | Source |
|---|---|---|---|
| `demo.greet` | symbol | `demo.greet` | `demo.py` |

## Flow

```mermaid
flowchart TD
  A["demo.greet"] --> B
```

## Sequence

```mermaid
sequenceDiagram
  participant G as demo.greet
  G->>G: call
```

## Model changes

| # | Element | Change | Why | Approval |
|---|---|---|---|---|
"""

DELIVERY = """
## Delivery

- #9 — PR: https://example.com/pr/1 · Model: docs/models/demo.md · Mutation: reports/demo-mutation.jsonl
- https://example.com/owner/demo/issues/2 — PR: https://example.com/pr/2 · Model: n/a — docs only · Mutation: reports/demo-mutation.jsonl
"""
SPEC56 = (SPEC.replace("| 5 · Program design | skip | no new code |", "| 5 · Program design | run | — |")
          .replace("| 6 · Implement-loop | skip | docs only |", "| 6 · Implement-loop | run | — |") + DELIVERY)

MATRIX = [{"id": "W0", "kind": "control", "outcome": "red"},
          {"id": "M1", "kind": "mutant", "outcome": "pinned"},
          {"id": "M2", "kind": "mutant", "outcome": "pinned"}]


def report_row(item: str, **fields) -> dict:
    return {"repo": "owner/demo", "criterion": "a", "item_id": item, "verdict": "pass",
            "severity": "fatal", "reviewer": "spec", "actor": "spec-1", **fields}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def make_delivered(repo: Path, wdir: Path) -> None:
    """Every output of phases 0-6 valid: the feature is delivered."""
    (repo / "demo.py").write_text(DEMO_PY)
    (repo / "docs" / "design").mkdir(parents=True)
    (repo / "docs" / "models").mkdir(parents=True)
    (repo / "docs" / "design" / "demo.md").write_text(design_text())
    (repo / "docs" / "models" / "demo.md").write_text(MODEL_TEXT)
    write_spec(repo, SPEC56)
    write_jsonl(wdir / "reports" / "demo-mutation.jsonl", MATRIX)
    write_jsonl(wdir / "reports" / "demo-spec.jsonl", [report_row("I1"), report_row("I2")])
    (wdir / "reports" / "demo-brief.json").write_text(
        json.dumps({"repo": "owner/demo", "feature": "demo", "items": ["I1", "I2"]}))


@pytest.fixture
def delivered(repo, kit):
    make_delivered(repo, kit[0])
    return repo, kit


def test_a_feature_with_every_phase_valid_is_delivered(delivered):
    """Witness: every output of phases 0-6 valid (design, model, Delivery rows,
    matrix, brief, reports) - distinguishes 'phases 5-6 checked and passing'
    (DELIVERED, exit 0) from 'check not implemented' (FAIL phase 5)."""
    repo, kit = delivered
    r = run_wf(repo, kit, "status")
    assert r.returncode == 0, r.stdout
    assert "PASS phase 5" in r.stdout and "PASS phase 6" in r.stdout
    assert "DELIVERED" in r.stdout


def edit(path: Path, old: str, new: str) -> None:
    path.write_text(swap(old, new, path.read_text()))


def drop(path: Path) -> None:
    path.unlink()


DESIGN = lambda repo, wdir: repo / "docs" / "design" / "demo.md"
MODEL = lambda repo, wdir: repo / "docs" / "models" / "demo.md"
SPECF = lambda repo, wdir: repo / "docs" / "specs" / "demo.md"
MATRIXF = lambda repo, wdir: wdir / "reports" / "demo-mutation.jsonl"
BRIEF = lambda repo, wdir: wdir / "reports" / "demo-brief.json"
REPORTS = lambda repo, wdir: wdir / "reports" / "demo-spec.jsonl"


def broken_matrix(**changes):
    def apply(repo, wdir):
        rows = [dict(r) for r in MATRIX]
        for index, fields in changes.items():
            rows[int(index[1:])].update(fields)
        write_jsonl(MATRIXF(repo, wdir), rows)
    return apply


def extra_report(row: dict):
    def apply(repo, wdir):
        write_jsonl(REPORTS(repo, wdir), [report_row("I1"), report_row("I2"), row])
    return apply


# Each case breaks exactly one check row of phase 5 or 6 in the delivered feature.
DELIVERY_CASES = [
    ("no-design-file", lambda r, w: drop(DESIGN(r, w)), "TODO", 5, "no design file docs/design/demo.md"),
    ("design-fails-the-gate", lambda r, w: DESIGN(r, w).write_text(design_text("def greet(extra: str) -> str: ...")),
     "FAIL", 5, "docs/design/demo.md fails check_design.py --gate"),
    ("no-delivery-section", lambda r, w: edit(SPECF(r, w), DELIVERY, ""), "TODO", 6, "no ## Delivery"),
    ("ticket-without-a-delivery-row",
     lambda r, w: edit(SPECF(r, w), DELIVERY.splitlines()[-1] + "\n", ""),
     "FAIL", 6, "no Delivery row for https://example.com/owner/demo/issues/2"),
    ("row-without-pr", lambda r, w: edit(SPECF(r, w), "PR: https://example.com/pr/1 · ", ""),
     "FAIL", 6, "Delivery row for #9 has no PR:"),
    ("row-without-model", lambda r, w: edit(SPECF(r, w), "Model: docs/models/demo.md · ", ""),
     "FAIL", 6, "Delivery row for #9 has no Model:"),
    ("row-without-mutation",
     lambda r, w: edit(SPECF(r, w), " · Mutation: reports/demo-mutation.jsonl\n- https", "\n- https"),
     "FAIL", 6, "Delivery row for #9 has no Mutation:"),
    ("pr-is-not-a-url", lambda r, w: edit(SPECF(r, w), "PR: https://example.com/pr/1", "PR: pr-1"),
     "FAIL", 6, "PR of #9 is not an http(s) URL"),
    ("model-fails-check-model", lambda r, w: edit(MODEL(r, w), "| symbol | `demo.greet`", "| symbol | `demo.nope`"),
     "FAIL", 6, "docs/models/demo.md fails check_model.py --gate --strict"),
    ("model-n-a-without-a-reason", lambda r, w: edit(SPECF(r, w), "Model: n/a — docs only", "Model: n/a"),
     "FAIL", 6, "Model of https://example.com/owner/demo/issues/2 is n/a without a reason"),
    ("model-n-a-with-an-empty-reason", lambda r, w: edit(SPECF(r, w), "Model: n/a — docs only", "Model: n/a —"),
     "FAIL", 6, "Model of https://example.com/owner/demo/issues/2 is n/a without a reason"),
    ("mutation-n-a-without-a-reason",
     lambda r, w: edit(SPECF(r, w), "Mutation: reports/demo-mutation.jsonl\n- https", "Mutation: n/a\n- https"),
     "FAIL", 6, "Mutation of #9 is n/a without a reason"),
    ("mutation-n-a-with-a-hyphen-and-no-reason",
     lambda r, w: edit(SPECF(r, w), "Mutation: reports/demo-mutation.jsonl\n- https", "Mutation: n/a -\n- https"),
     "FAIL", 6, "Mutation of #9 is n/a without a reason"),
    ("model-n-a-without-a-dash", lambda r, w: edit(SPECF(r, w), "Model: n/a — docs only", "Model: n/a docs only"),
     "FAIL", 6, "Model of https://example.com/owner/demo/issues/2 is n/a without a reason"),
    ("mutation-n-a-without-a-dash",
     lambda r, w: edit(SPECF(r, w), "Mutation: reports/demo-mutation.jsonl\n- https", "Mutation: n/a docs only\n- https"),
     "FAIL", 6, "Mutation of #9 is n/a without a reason"),
    ("matrix-missing", lambda r, w: drop(MATRIXF(r, w)), "FAIL", 6, "no mutation matrix"),
    ("matrix-with-a-blind-test-row", broken_matrix(r2={"outcome": "blind-test"}),
     "FAIL", 6, "M2 is blind-test, not pinned"),
    ("matrix-control-not-red", broken_matrix(r0={"outcome": "pinned"}),
     "FAIL", 6, "control W0 is pinned, not red"),
    ("no-brief", lambda r, w: drop(BRIEF(r, w)), "FAIL", 6, "no brief for demo in the store (wf.py report brief)"),
    ("brief-item-without-a-report-row",
     lambda r, w: edit(BRIEF(r, w), '"I2"]', '"I2", "I3"]'), "FAIL", 6, "brief item 'I3' has no row"),
    ("open-fatal-missing-verdict", extra_report(report_row("I2", criterion="b", verdict="missing")),
     "FAIL", 6, "[I6/B-025/10] missing finding item_id=I2"),
    ("report-row-without-actor", extra_report({**report_row("I2", criterion="b"), "actor": ""}),
     "FAIL", 6, "[B-004] report demo/I2 has no actor"),
]


@pytest.mark.parametrize("mutate,label,phase,problem", [c[1:] for c in DELIVERY_CASES],
                         ids=[c[0] for c in DELIVERY_CASES])
def test_each_phase_5_6_check_can_fail(delivered, mutate, label, phase, problem):
    """Witness: the delivered feature with one output broken - distinguishes
    'next refuses at this phase naming the problem' from 'delivered'."""
    repo, kit = delivered
    mutate(repo, kit[0])
    r = run_wf(repo, kit, "next")
    assert f"{label} phase {phase}: " in r.stdout and problem in r.stdout, r.stdout
    assert r.returncode == (0 if label == "TODO" else 1)


MATRIX_REF = "Mutation: reports/demo-mutation.jsonl"


def test_mutation_n_a_with_a_reason_is_delivered_and_reads_no_matrix(delivered):
    """Witness: every ticket `Mutation: n/a — docs only` and no matrix file -
    distinguishes 'n/a is valid, no matrix read' from 'no mutation matrix'."""
    repo, kit = delivered
    path = SPECF(repo, kit[0])
    path.write_text(path.read_text().replace(MATRIX_REF, "Mutation: n/a — docs only"))
    drop(MATRIXF(repo, kit[0]))
    r = run_wf(repo, kit, "status")
    assert r.returncode == 0 and "PASS phase 6" in r.stdout and "DELIVERED" in r.stdout, r.stdout


def test_n_a_with_a_hyphen_and_a_reason_is_delivered(delivered):
    """Characterization (the code already accepts it). Witness: every ticket
    `Model: n/a - docs only` and `Mutation: n/a - docs only` with an ASCII
    hyphen - distinguishes 'hyphen accepted' from 'only the em dash accepted'."""
    repo, kit = delivered
    path = SPECF(repo, kit[0])
    text = path.read_text().replace(MATRIX_REF, "Mutation: n/a - docs only")
    path.write_text(text.replace("Model: docs/models/demo.md", "Model: n/a - docs only")
                    .replace("Model: n/a — docs only", "Model: n/a - docs only"))
    drop(MATRIXF(repo, kit[0]))
    r = run_wf(repo, kit, "status")
    assert r.returncode == 0 and "DELIVERED" in r.stdout, r.stdout


def test_mutation_n_a_next_to_a_real_matrix_still_checks_that_matrix(delivered):
    """Witness: ticket #9 `Mutation: n/a — x`, the other ticket a matrix with a
    blind-test row - distinguishes 'the real matrix is still read' from 'n/a
    on one row disables the check'."""
    repo, kit = delivered
    edit(SPECF(repo, kit[0]), "Mutation: reports/demo-mutation.jsonl\n- https", "Mutation: n/a — x\n- https")
    broken_matrix(r2={"outcome": "blind-test"})(repo, kit[0])
    r = run_wf(repo, kit, "next")
    assert "FAIL phase 6: " in r.stdout and "M2 is blind-test, not pinned" in r.stdout, r.stdout


def test_design_check_runs_in_gate_mode(delivered):
    """Witness: a design declaring `add` functions that already exist in the
    code - full mode reports the state, gate mode does not; distinguishes
    'phase 5 runs --gate' (phase 5 passes) from 'full mode' (fail)."""
    repo, kit = delivered
    guard = [sys.executable, str(ROOT / "guards" / "check_design.py"),
             str(repo / "docs" / "design" / "demo.md"), str(repo), "--quiet"]
    full = subprocess.run(guard, capture_output=True, text=True, check=False)
    assert full.returncode == 1 and "declared NEW, but it exists" in full.stdout
    d = wf.derive(repo, FEATURE, evidence_store(kit))
    assert statuses(d)[5] == "pass"


def test_phase5_accepts_a_declared_removal_still_pending(delivered):
    """Witness (D-054): the design removes `greet`'s `name`, which the code still has.
    Phase 5 judges the design, so it passes; distinguishes 'wf passes --allow-removing'
    from 'wf runs the bare gate' (which fails on `removing`)."""
    repo, kit = delivered
    text = design_text("def greet() -> str: ...").replace(
        '"code": "def greet() -> str: ..."',
        '"code": "def greet() -> str: ...", "removes": {"greet": ["name"]}')
    (repo / "docs" / "design" / "demo.md").write_text(text)
    bare = subprocess.run([sys.executable, str(ROOT / "guards" / "check_design.py"),
                           str(repo / "docs" / "design" / "demo.md"), str(repo), "--gate", "--quiet"],
                          capture_output=True, text=True, check=False)
    assert bare.returncode == 1 and "removing" in bare.stdout
    d = wf.derive(repo, FEATURE, evidence_store(kit))
    assert statuses(d)[5] == "pass"


def test_delivery_row_maps_every_ticket_reference_of_the_row():
    """Witness: one row for `#9, #11` and one for a URL - distinguishes 'every
    reference of a row maps to its fields' from 'only the first does'."""
    text = SPEC56.replace("- #9 — PR:", "- #9, ABC-11 — PR:")
    spec = wf.Spec(Path("docs/specs/demo.md"), text, wf.parse_front_matter(text), wf.parse_sections(text))
    rows = wf.delivery_rows(spec)
    fields = {"PR": "https://example.com/pr/1", "Model": "docs/models/demo.md",
              "Mutation": "reports/demo-mutation.jsonl"}
    assert rows["#9"] == rows["ABC-11"] == fields
    assert rows["https://example.com/owner/demo/issues/2"]["Model"] == "n/a — docs only"
    assert wf.delivery_rows(wf.Spec(Path("x.md"), "", {}, {})) == {}


def test_a_ticket_only_named_after_blocked_by_needs_no_delivery_row(delivered):
    """Witness: ticket item `#9 — x (blocked by #8)` and no row for #8 -
    distinguishes 'rows required for the ticket(s) an item is about' from
    'every #N in the item'."""
    repo, kit = delivered
    edit(SPECF(repo, kit[0]), "- #9 phases 2-4", "- #9 — phases 2-4 (blocked by #8)")
    assert wf.derive(repo, FEATURE, evidence_store(kit)).phase is None


# --- review round 7 fixes ------------------------------------------------------

def spec_with_tickets(items: str) -> wf.Spec:
    text = swap("- #9 phases 2-4\n1. https://example.com/owner/demo/issues/2\n", items, SPEC)
    return wf.Spec(Path("docs/specs/demo.md"), text, wf.parse_front_matter(text), wf.parse_sections(text))


def test_phase_4_refuses_a_reference_only_after_the_dash():
    """Witness: `1. Audit work — #13` - distinguishes 'an item's references are
    those before its first " — "' from 'a reference anywhere in the line'."""
    problems = wf.check_phase4(spec_with_tickets("1. Audit work — #13\n"))
    assert len(problems) == 1 and "Audit work — #13" in problems[0]
    assert "before ' — '" in problems[0]


def test_every_reference_before_the_dash_needs_a_delivery_row():
    """Witness: `- #13, #15 — text` - distinguishes 'one row per reference of
    the item' from 'one row per item'."""
    spec = spec_with_tickets("- #13, #15 — text\n")
    assert wf.check_phase4(spec) == ()
    assert wf._ticket_refs(spec) == ["#13", "#15"]


def test_a_dashless_item_needs_a_row_for_every_reference_in_the_line():
    """Witness: `- #20 blocked by #8` - the loud direction: without a dash
    nothing marks #8 as description, so both need rows."""
    spec = spec_with_tickets("- #20 blocked by #8\n")
    assert wf.check_phase4(spec) == ()
    assert wf._ticket_refs(spec) == ["#20", "#8"]


# Characterization tests (they pass on the code as it was): AC10-4 scoping.

def test_reports_and_brief_of_another_repo_are_ignored(delivered):
    """Characterization: a brief and reports whose front-matter `repo` differs
    are not read - the feature has no brief for its own repo."""
    repo, kit = delivered
    for path in (BRIEF(repo, kit[0]), REPORTS(repo, kit[0])):
        path.write_text(path.read_text().replace("owner/demo", "owner/other"))
    r = run_wf(repo, kit, "next")
    assert "FAIL phase 6: no brief for demo in the store (wf.py report brief)" in r.stdout, r.stdout


def test_report_rows_of_another_feature_are_ignored(delivered):
    """Characterization: a blocking row of feature `other` does not fail `demo`."""
    repo, kit = delivered
    write_jsonl(kit[0] / "reports" / "other-spec.jsonl",
                [report_row("X1", verdict="missing", severity="fatal")])
    (kit[0] / "reports" / "other-brief.json").write_text(
        json.dumps({"repo": "owner/demo", "feature": "other", "items": ["X1"]}))
    assert wf.derive(repo, FEATURE, evidence_store(kit)).phase is None


def test_a_mutation_path_outside_the_workflow_dir_is_a_problem_not_read(delivered):
    """Witness: absolute and `..` paths - distinguishes 'refused' from 'read'
    (the matrix file exists and is valid at the escaped location)."""
    repo, kit = delivered
    outside = kit[0].parent / "elsewhere.jsonl"
    write_jsonl(outside, MATRIX)
    for bad in (str(outside), f"../{outside.name}"):
        edit(SPECF(repo, kit[0]), "reports/demo-mutation.jsonl", bad)
        r = run_wf(repo, kit, "next")
        assert "mutation matrix path must be relative to the workflow dir" in r.stdout, r.stdout
        write_spec(repo, SPEC56)


def test_a_corrupt_stored_report_row_is_exit_two_not_a_traceback(delivered):
    """Witness: a stored row whose `row_json` is not JSON (written straight to the table, as the
    import and `report add` never would) - distinguishes 'exit 2 with a `wf:` line' from a
    traceback or a pass."""
    repo, kit = delivered
    store = evidence_store(kit)
    with sqlite3.connect(store) as c:
        c.execute("INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion, verdict,"
                  " severity, actor, row_json) VALUES ('t','owner/demo','demo','demo-spec','I3','a','pass',"
                  "'fatal','spec-1','{not json')")
    r = run_wf(repo, kit, "status", store=store)
    assert r.returncode == 2 and r.stderr.startswith("wf: ") and "Traceback" not in r.stderr, (r.stdout, r.stderr)


# --- presence (#23) ---------------------------------------------------------

def fake_proc(root: Path, pid: int, comm: str, ppid: int, ticks: int) -> None:
    """A /proc/<pid>/stat in the kernel's format: 52 fields, comm in parentheses."""
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(ticks)] + ["0"] * 30
    (root / str(pid)).mkdir(parents=True, exist_ok=True)
    (root / str(pid) / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}\n")


def write_record(presence_dir: Path, name: str, **over) -> None:
    rec = {"slug": "demo", "repo_root": "/r", "pid": 100, "process": "claude",
           "start_ticks": 5000, "recorded_at": "2026-11-16T10:00:00+01:00"} | over
    presence_dir.mkdir(parents=True, exist_ok=True)
    (presence_dir / f"{name}.json").write_text(json.dumps(rec))


def test_presence_alive_only_for_the_same_process(tmp_path):
    """Witness: three records on one fake process tree - pid 999 absent (dead),
    pid 100 with ticks 4999 against the live 5000 (pid reused), pid 100 with
    ticks 5000 (same process); the comm `a) b (x` holds a space and ')' -
    distinguishes 'alive iff pid exists AND ticks equal' from 'alive iff pid
    exists' (reused pid reads alive) and from a comm cut at the first ')'
    (ticks read from the wrong field, so the same process reads gone)."""
    proc = tmp_path / "proc"
    fake_proc(proc, 100, "a) b (x", 1, 5000)
    pres = tmp_path / "presence"
    write_record(pres, "dead", pid=999)
    write_record(pres, "reused", pid=100, start_ticks=4999)
    write_record(pres, "same", pid=100, start_ticks=5000, process="a) b (x")
    alive = {p.pid: p.alive for p in wf.read_presence(pres, proc) if p.start_ticks == 5000}
    by_name = {p.start_ticks: p.alive for p in wf.read_presence(pres, proc) if p.pid == 100}
    assert alive == {999: False, 100: True}
    assert by_name == {4999: False, 5000: True}


def presence_files(presence_dir: Path) -> list[Path]:
    return sorted(presence_dir.glob("*.json")) if presence_dir.is_dir() else []


def test_next_records_presence_outside_the_repo(repo, kit, tmp_path):
    """Witness: wf runs under `sh` whose parent is this test process - the
    record's pid is os.getpid(), not the sh pid; distinguishes 'first
    non-shell ancestor' from 'direct parent'. `git status --porcelain` of the
    work repo (untracked spec in it) is identical before and after;
    distinguishes 'store outside the repo' from 'record written in the tree'."""
    write_spec(repo, SPEC)
    pres = tmp_path / "pstore"
    before = git(repo, "status", "--porcelain")
    script = 'python="$1"; shift; "$python" "$@"; rc=$?; exit $rc'
    r = subprocess.run(
        ["sh", "-c", script, "sh", sys.executable, str(WF), "next", FEATURE,
         "--repo-root", str(repo), "--workflow-dir", str(kit[0]),
         "--skills-dir", str(kit[1]), "--presence-dir", str(pres)],
        capture_output=True, text=True, check=False)
    assert r.returncode in (0, 1), r.stderr
    name = hashlib.sha1(f"{repo.resolve()}\0{FEATURE}".encode()).hexdigest() + ".json"
    assert [p.name for p in presence_files(pres)] == [name]
    rec = json.loads((pres / name).read_text())
    assert rec["pid"] == os.getpid()
    assert rec["slug"] == FEATURE and rec["repo_root"] == str(repo.resolve())
    assert rec["start_ticks"] == wf.proc_stat(os.getpid())[2]
    assert rec["process"] == wf.proc_stat(os.getpid())[0]
    assert datetime.fromisoformat(rec["recorded_at"]).utcoffset() is not None
    assert git(repo, "status", "--porcelain") == before


def test_status_records_presence_too(repo, kit, tmp_path):
    """Witness: `status` on a spec that is not delivered (exit 1, a fail
    verdict) - distinguishes 'record on every status' from 'only on next' and
    from 'only when derive passes'."""
    write_spec(repo, swap("`echo blue`", ""))
    pres = tmp_path / "pstore"
    r = run_wf(repo, kit, "status", presence_dir=pres)
    assert r.returncode == 1 and "FAIL phase 1" in r.stdout
    assert len(presence_files(pres)) == 1


def test_presence_is_overwritten_per_repo_and_slug(repo, kit, tmp_path):
    """Witness: two runs on one slug, then one on another slug - two files,
    not three; distinguishes 'one record per (root, slug)' from 'one per run'."""
    write_spec(repo, SPEC)
    pres = tmp_path / "pstore"
    run_wf(repo, kit, "status", presence_dir=pres)
    run_wf(repo, kit, "status", presence_dir=pres)
    assert len(presence_files(pres)) == 1
    run_wf(repo, kit, "status", "other", presence_dir=pres)
    assert len(presence_files(pres)) == 2


def test_presence_write_failure_leaves_the_answer_unchanged(repo, kit, tmp_path):
    """Witness: --presence-dir under a regular file (OSError on write) -
    distinguishes 'one stderr line, same stdout and exit code as a run with a
    good store' from exit 2 or a traceback."""
    write_spec(repo, SPEC)
    good = run_wf(repo, kit, "next", presence_dir=tmp_path / "ok")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = run_wf(repo, kit, "next", presence_dir=blocker / "sub")
    assert (bad.returncode, bad.stdout) == (good.returncode, good.stdout)
    assert good.stderr == ""
    lines = bad.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("wf: presence not recorded: ")


def test_presence_is_not_recorded_when_derive_raises(repo, kit, tmp_path):
    """Witness: a spec that is not valid UTF-8 (derive raises, exit 2) -
    distinguishes 'record after a successful derive' from 'record first'."""
    (repo / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "specs" / f"{FEATURE}.md").write_bytes(b"\xff\xfe")
    pres = tmp_path / "pstore"
    r = run_wf(repo, kit, "next", presence_dir=pres)
    assert r.returncode == 2
    assert presence_files(pres) == []


def test_presence_agent_is_the_first_non_shell_ancestor(tmp_path):
    """Witness: chain 300 sh -> 200 bash -> 100 claude -> 1 init, started at
    300 - distinguishes 'walk past every shell to claude' from 'stop at the
    first parent' (sh) and from 'stop at the first comm that is not sh'
    (bash, as a one-name filter)."""
    proc = tmp_path / "proc"
    fake_proc(proc, 300, "sh", 200, 3)
    fake_proc(proc, 200, "bash", 100, 2)
    fake_proc(proc, 100, "claude", 1, 1)
    assert wf.agent_process(300, proc) == (100, "claude", 1)


def test_presence_without_an_agent_writes_nothing(tmp_path):
    """Witness: a tree of shells up to pid 1, and an empty /proc - distinguishes
    'None, no file' from a record naming a shell or init."""
    proc = tmp_path / "proc"
    fake_proc(proc, 300, "bash", 200, 3)
    fake_proc(proc, 200, "sh", 1, 2)
    fake_proc(proc, 1, "systemd", 0, 0)
    now = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    pres = tmp_path / "pstore"
    for root in (proc, tmp_path / "no-proc"):
        with mock.patch("os.getppid", return_value=300):
            assert wf.record_presence(pres, tmp_path, "demo", now, root) is None
    assert presence_files(pres) == []


def test_malformed_or_missing_presence_store(tmp_path):
    """Witness: a missing store reads (), a record that is not JSON raises
    ValueError naming its file - distinguishes both from a silent skip."""
    assert wf.read_presence(tmp_path / "absent", tmp_path) == ()
    pres = tmp_path / "pstore"
    pres.mkdir()
    (pres / "bad.json").write_text("{not json")
    with pytest.raises(ValueError, match="bad.json"):
        wf.read_presence(pres, tmp_path)


def test_default_presence_dir_follows_xdg_state_home(monkeypatch, tmp_path):
    """Witness: XDG_STATE_HOME set vs unset - distinguishes 'honours XDG' from
    'always ~/.local/state'."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert wf.default_presence_dir() == tmp_path / "xdg" / "agentic-dev-kit" / "presence"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert wf.default_presence_dir() == Path.home() / ".local/state/agentic-dev-kit/presence"


def test_every_phase_check_has_a_docstring():
    """Witness `check_phase0..6` each asked for its docstring - distinguishes a check whose
    exit-check text (shown on the workflow map) exists from one that is silent (today 0-4, 6)."""
    silent = [n for n in wf.PHASES if not (inspect.getdoc(getattr(wf, f"check_phase{n}")) or "").strip()]
    assert silent == []


# --- #54: phase 6 reads the brief and the reports from the store -------------

def stored_demo_evidence(store: Path, repo: Path) -> None:
    """The delivered feature's brief, reports and matrix `demo-mutation`, in the store only."""
    mod = wf._report_store()
    conn = mod.open_store(store)
    rows = [report_row("I1", claim="coverage"), report_row("I2", claim="coverage")]
    mod.add_review_rows(conn, rows, FEATURE, "demo-spec", repo, datetime.now())
    mod.add_brief_items(conn, "owner/demo", FEATURE, ["I1", "I2"], datetime.now())
    conn.close()
    put_stored_matrix(store, "demo-mutation", MATRIX)


def test_phase6_reads_brief_and_reports_from_the_store(delivered, tmp_path):
    repo, kit = delivered
    drop(BRIEF(repo, kit[0]))
    drop(REPORTS(repo, kit[0]))
    store = tmp_path / "s" / "reports.sqlite"
    # witness: the evidence is only in the store — with the store ignored the files are gone and
    # phase 6 fails; with an empty store the same spec still fails (the store is what makes it pass)
    mod = wf._report_store()
    mod.open_store(store).close()
    empty = run_wf(repo, kit, "status", store=store)
    assert empty.returncode == 1 and "no brief for demo in the store (wf.py report brief)" in empty.stdout, empty.stdout
    stored_demo_evidence(store, repo)
    full = run_wf(repo, kit, "status", store=store)
    assert full.returncode == 0 and "DELIVERED" in full.stdout, full.stdout
    gone = run_wf(repo, kit, "status", store=tmp_path / "other" / "reports.sqlite")
    assert "no brief for demo in the store (wf.py report brief)" in gone.stdout
    assert not (tmp_path / "other").exists()


def test_phase6_takes_the_store_from_the_variable_when_there_is_no_flag(delivered, tmp_path):
    repo, kit = delivered
    drop(BRIEF(repo, kit[0]))
    drop(REPORTS(repo, kit[0]))
    store = tmp_path / "v" / "reports.sqlite"
    stored_demo_evidence(store, repo)
    # witness: the store only through $AGENT_REPORT_STORE — distinguishes "resolved like report"
    # from a flag-only option
    via_env = run_wf(repo, kit, "status", env={"AGENT_REPORT_STORE": str(store)})
    assert via_env.returncode == 0 and "DELIVERED" in via_env.stdout, via_env.stdout


def test_phase6_without_a_stored_brief_names_report_brief(delivered, tmp_path):
    repo, kit = delivered
    store = tmp_path / "s" / "reports.sqlite"
    wf._report_store().open_store(store).close()
    # witness: `reports/demo-brief.json`, the matrix and the reports are all on disk and valid, the
    # store is empty - a reader that still looked at the files would pass; the message names the command
    assert BRIEF(repo, kit[0]).is_file()
    r = run_wf(repo, kit, "status", store=store)
    assert r.returncode == 1, r.stdout
    assert "FAIL phase 6: " in r.stdout and "no brief for demo in the store (wf.py report brief)" in r.stdout, r.stdout


def test_phase6_malformed_store_is_exit_two(delivered, tmp_path):
    repo, kit = delivered
    store = tmp_path / "s" / "reports.sqlite"
    store.parent.mkdir()
    store.write_text("this is not a database, just text that is long enough " * 20)
    r = run_wf(repo, kit, "status", store=store)
    assert r.returncode == 2 and "report store" in r.stderr, (r.stdout, r.stderr)


def test_derive_without_a_store_has_no_evidence(delivered):
    repo, kit = delivered
    # witness: a store that would deliver the feature, passed or not - the window calls derive with no store
    assert wf.derive(repo, FEATURE).phase == 6
    assert wf.derive(repo, FEATURE, evidence_store(kit)).phase is None


# --- #55: phase 6 finds a matrix by name, and by path (Decision 6) ------------

def put_stored_matrix(store: Path, name: str, rows: list[dict]) -> None:
    """A matrix in the store, written straight to the table (the reader is under test)."""
    wf._report_store().open_store(store).close()
    with sqlite3.connect(store) as c:
        c.executemany(
            "INSERT INTO mutation_result (name, seq, recorded_at, mutant_id, kind, outcome)"
            " VALUES (?,?,?,?,?,?)",
            [(name, k, "t", r["id"], r["kind"], r["outcome"]) for k, r in enumerate(rows)])


def cite(repo: Path, kit: tuple[Path, Path], value: str) -> None:
    """Every Delivery row cites `value`, and the matrix file of the fixture is gone."""
    path = SPECF(repo, kit[0])
    path.write_text(path.read_text().replace(MATRIX_REF, f"Mutation: {value}"))
    drop(MATRIXF(repo, kit[0]))


BLIND = [{**r, "outcome": "blind-test"} if r["id"] == "M2" else r for r in MATRIX]


@pytest.mark.parametrize("value", ["m", "reports/m.jsonl"])
def test_delivery_resolves_a_matrix_by_name_and_by_old_path(delivered, tmp_path, value):
    repo, kit = delivered
    cite(repo, kit, value)
    store = tmp_path / "s" / "reports.sqlite"
    stored_demo_evidence(store, repo)
    put_stored_matrix(store, "m", MATRIX)
    ok = run_wf(repo, kit, "status", store=store)
    assert ok.returncode == 0 and "PASS phase 6" in ok.stdout, ok.stdout
    # witness: the same name with one blind-test row, and no file at reports/m.jsonl — the name
    # and the old path give the same verdict and the same problem (the spec invariant);
    # distinguishes "a path that has no file is not found" from "it reaches the stored matrix"
    other = tmp_path / "s2" / "reports.sqlite"
    stored_demo_evidence(other, repo)
    put_stored_matrix(other, "m", BLIND)
    bad = run_wf(repo, kit, "status", store=other)
    assert bad.returncode == 1 and "FAIL phase 6: " in bad.stdout, bad.stdout
    assert "m: M2 is blind-test, not pinned" in bad.stdout, bad.stdout


def test_phase6_resolves_a_delivery_path_through_the_alias(delivered, tmp_path):
    repo, kit = delivered
    cite(repo, kit, "handoffs/x-reports/m.jsonl")
    wd = tmp_path / "evidence"
    write_jsonl(wd / "handoffs" / "x-reports" / "m.jsonl", MATRIX)
    store = tmp_path / "s" / "reports.sqlite"
    stored_demo_evidence(store, repo)
    mod = wf._report_store()
    conn = mod.open_store(store)
    mod.import_files(conn, wd, [Path("handoffs")], datetime.now())
    conn.close()
    drop(wd / "handoffs" / "x-reports" / "m.jsonl")
    # witness: a blind-test matrix at reports/m.jsonl stays on disk, in the kit - the stored `m` is
    # all pinned and is the one judged by the path's alias; distinguishes "a path is its alias" from
    # "a path is read from the disk" and from "the file of that stem is read"
    write_jsonl(kit[0] / "reports" / "m.jsonl", BLIND)
    ok = run_wf(repo, kit, "status", store=store)
    assert ok.returncode == 0 and "PASS phase 6" in ok.stdout, ok.stdout
    edit(SPECF(repo, kit[0]), "handoffs/x-reports/m.jsonl", "handoffs/y-reports/never-imported.jsonl")
    gone = run_wf(repo, kit, "status", store=store)
    assert "no mutation matrix handoffs/y-reports/never-imported.jsonl in the store" in gone.stdout, gone.stdout


def test_a_bare_name_is_the_stored_matrix_only(delivered, tmp_path):
    repo, kit = delivered
    cite(repo, kit, "m")
    write_jsonl(kit[0] / "reports" / "m.jsonl", MATRIX)
    store = tmp_path / "s" / "reports.sqlite"
    stored_demo_evidence(store, repo)
    # witness: no stored `m` and a valid reports/m.jsonl on disk - fails naming the name and the
    # store; then a stored blind `m` beside a pinned file - the stored one is judged
    none = run_wf(repo, kit, "status", store=store)
    assert "no mutation matrix m in the store" in none.stdout, none.stdout
    put_stored_matrix(store, "m", BLIND)
    blind = run_wf(repo, kit, "status", store=store)
    assert "m: M2 is blind-test, not pinned" in blind.stdout, blind.stdout


def test_a_value_that_is_neither_a_path_nor_a_name_fails(delivered, tmp_path):
    repo, kit = delivered
    cite(repo, kit, "m n")
    r = run_wf(repo, kit, "status", store=tmp_path / "s" / "reports.sqlite")
    # witness: a value with a space and no slash — distinguishes "refused as not a name"
    # from "looked up as a name" (which would say `no mutation matrix`)
    assert "m n is not a matrix name" in r.stdout and "no mutation matrix" not in r.stdout, r.stdout


def test_a_name_with_a_malformed_store_is_exit_two(delivered, tmp_path):
    repo, kit = delivered
    cite(repo, kit, "m")
    store = tmp_path / "s" / "reports.sqlite"
    store.parent.mkdir()
    store.write_text("this is not a database, just text that is long enough " * 20)
    r = run_wf(repo, kit, "status", store=store)
    # witness: a name that has to be looked up in a store that is no database - distinguishes
    # a reason on stderr with exit 2 from a traceback
    assert r.returncode == 2 and "report store" in r.stderr and "Traceback" not in r.stderr, (r.stdout, r.stderr)


# --- #100 (gate-sections): one map of the headings the gates contract -----------------------------

# The contract as the gates stood before GATE_SECTIONS: (phase, heading), spelled out here on purpose
# (a literal of the external contract, not read from the map under test). 11 headings.
CONTRACTED = [(0, "Phase plan"), (1, "Problem Statement"), (1, "Solution"), (1, "User Stories"),
              (1, "Premises"), (1, "Model"), (2, "Product review"), (3, "System Architecture"),
              (3, "Model"), (4, "Tickets"), (6, "Delivery")]


def gate_fixture_specs() -> dict[str, str]:
    """The fixture specs the gate-section tests run on, by name: the module's own `SPEC` (phases 0-4)
    and `SPEC56` (delivered), the `Model skipped` variant and the `no domain laws` variant. They stand
    in for the delivered specs the refactor was first measured on, which an exported tree does not have."""
    model = SPEC[SPEC.index("## Model"):SPEC.index("## Product review")]
    laws = SPEC[SPEC.index("### System invariants"):SPEC.index("## Tickets")]
    return {
        "spec": SPEC,
        "spec56": SPEC56,
        "model-skipped": swap(model, "Model skipped — no entities move in time.\n\n"),
        "no-domain-laws": swap(laws, "This feature has no domain laws: it only moves files.\n\n"),
    }


def all_headings_spec_text() -> str:
    """`SPEC56` plus every `CONTRACTED` heading it lacks (as an empty `##` section), so that the one
    spec carries all 11 headings; each is asserted present."""
    text = SPEC56
    for _, heading in CONTRACTED:
        if heading not in wf.parse_sections(text):
            text += f"\n## {heading}\n\nfiller\n"
    sections = wf.parse_sections(text)
    for _, heading in CONTRACTED:
        assert heading in sections, heading
    return text


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    """A repo root the fixture specs' checks read: the mockup `SPEC` names."""
    (tmp_path / "docs" / "mockups").mkdir(parents=True)
    (tmp_path / "docs" / "mockups" / "demo.md").write_text("mockup\n")
    return tmp_path


def _spec_of(path: Path, text: str) -> "wf.Spec":
    return wf.Spec(path, text, wf.parse_front_matter(text), wf.parse_sections(text))


def _check_of(phase: int, text: str, path: Path, root: Path) -> tuple[str, ...]:
    spec = _spec_of(path, text)
    if phase == 6:
        return wf.check_phase6(spec, root, None)
    return wf.check_phase(phase, spec, wf.parse_plan(spec), root)


def _rename_heading(text: str, heading: str, new: str) -> str:
    import re
    return re.sub(rf"^## {re.escape(heading)}\s*$", f"## {new}", text, flags=re.M)


def test_the_checks_give_pinned_problems_on_every_fixture_spec_intact_and_with_each_heading_renamed(fixture_root):
    """witness the 4 fixture specs of `gate_fixture_specs()`, each intact (phases 0-4) and with each
    contracted heading renamed away (64 records, hashed): the problems of every check, byte for byte,
    as pinned when the fixtures replaced the delivered specs - distinguishes a refactor that changed a
    message, an order or a condition."""
    records = []
    for name, text in gate_fixture_specs().items():
        path = fixture_root / "docs" / "specs" / f"{name}.md"
        for phase in (0, 1, 2, 3, 4):
            records.append((name, "intact", phase, _check_of(phase, text, path, fixture_root)))
        for phase, heading in CONTRACTED:
            changed = _rename_heading(text, heading, f"{heading}-renamed")
            records.append((name, "drop " + heading, phase, _check_of(phase, changed, path, fixture_root)))
    blob = json.dumps(records, sort_keys=True, default=list)
    # Characterisation pin, not a proof by itself: 64 = 4 fixture specs x (5 intact + 11 renamed headings); the
    # hash was taken from the first run on these fixtures (the old pin hashed the owner's real specs). Re-pin it
    # only when a check's problems or the fixtures change on purpose, after reading the diff of the records.
    assert (len(records), hashlib.sha256(blob.encode()).hexdigest()[:16]) == (64, "2cf64b56ff46c87f")


@pytest.mark.parametrize("phase,heading", CONTRACTED, ids=[f"{h}@{p}" for p, h in CONTRACTED])
def test_removing_a_contracted_section_of_a_fixture_spec_makes_its_phase_check_fail_naming_it(phase, heading, fixture_root):
    """witness `all_headings_spec_text()` (a spec that carries every contracted heading) with `heading`
    renamed away: the check of `phase` must answer a problem that contains the heading - distinguishes
    a heading in the list that its check no longer requires (the list and the check drifted)."""
    path = fixture_root / "docs" / "specs" / "all.md"
    text = all_headings_spec_text()
    assert heading in wf.parse_sections(text), "the witness spec must carry the heading"
    problems = _check_of(phase, _rename_heading(text, heading, f"{heading}-renamed"), path, fixture_root)
    assert any(heading in p for p in problems), problems


def test_gate_sections_has_an_entry_for_every_phase_and_phase_5_has_none():
    expected = {0: ("Phase plan",),
                1: ("Problem Statement", "Solution", "User Stories", "Premises", "Model"),
                2: ("Product review",), 3: ("System Architecture", "Model"), 4: ("Tickets",), 5: (),
                6: ("Delivery",)}
    assert wf.GATE_SECTIONS == expected


@pytest.mark.parametrize("phase", [0, 1, 2, 3, 4, 6])
def test_each_check_reads_its_heading_names_from_the_map(monkeypatch, phase, fixture_root):
    """witness `all_headings_spec_text()` with every contracted heading renamed `<name> X` and the
    map renamed the same way: the check answers what it answers for the intact spec and the intact
    map - distinguishes a check that holds the heading as its own literal (it would say `no ## ...`)."""
    path = fixture_root / "docs" / "specs" / "all.md"
    text = all_headings_spec_text()
    before = _check_of(phase, text, path, fixture_root)
    renamed = text
    for heading in dict.fromkeys(h for _, h in CONTRACTED):
        renamed = _rename_heading(renamed, heading, f"{heading} X")
    monkeypatch.setattr(wf, "GATE_SECTIONS", {n: tuple(f"{h} X" for h in hs) for n, hs in wf.GATE_SECTIONS.items()})
    assert _check_of(phase, renamed, path, fixture_root) == before


@pytest.mark.parametrize("phase,first", [(2, "Product review"), (3, "System Architecture"), (4, "Tickets"),
                                         (6, "Delivery")])
def test_phase_started_is_true_for_the_first_contracted_heading_of_phases_2_3_4_and_6(phase, first):
    started = _spec_of(ROOT / "x.md", f"## {first}\n\ntext\n")
    other = _spec_of(ROOT / "x.md", "## Elsewhere\n\ntext\n")
    assert wf.phase_started(phase, started) is True
    assert wf.phase_started(phase, other) is False


def test_phase_started_follows_the_heading_the_map_names(monkeypatch):
    """witness the phase-4 heading renamed in the map only - distinguishes a phase_started that
    reads the map from one that holds `Tickets`."""
    monkeypatch.setitem(wf.GATE_SECTIONS, 4, ("Work items",))
    assert wf.phase_started(4, _spec_of(ROOT / "x.md", "## Work items\n\nx\n")) is True
    assert wf.phase_started(4, _spec_of(ROOT / "x.md", "## Tickets\n\nx\n")) is False


def test_ticket_refs_follow_the_heading_the_map_names_for_phase_4(monkeypatch):
    """witness the phase-4 heading renamed in the map only, the tickets under the new name - distinguishes
    a `_ticket_refs` (shared by phases 4 and 6) that reads the map from one that holds `Tickets`."""
    monkeypatch.setitem(wf.GATE_SECTIONS, 4, ("Work items",))
    spec = _spec_of(ROOT / "x.md", "## Work items\n\n- #9 — one\n- #10 — two\n\n## Tickets\n\n- #11 — old name\n")
    assert wf._ticket_refs(spec) == ["#9", "#10"]


def test_a_missing_contracted_section_of_a_model_with_more_than_five_violations_is_still_named(delivered):
    """witness the delivered demo whose model lost `## Model Changes` and gained six elements no diagram
    cites (more than 5 violations, the missing section the first) - distinguishes a phase-6 problem that
    keeps the section the guard names from one that shows only the last 5 lines of the guard's output."""
    repo, _ = delivered
    model = repo / "docs" / "models" / "demo.md"
    text = model.read_text()
    text = text[:text.index("## Model changes")]
    text = text.replace("## Flow", "".join(
        f"| `demo.extra{n}` | symbol | `demo.greet` | `demo.py` |\n" for n in range(6)) + "\n## Flow")
    model.write_text(text)
    path = repo / "docs" / "specs" / "demo.md"
    problems = wf.check_phase6(_spec_of(path, path.read_text()), repo, None)
    failing = [p for p in problems if "fails check_model.py" in p]
    assert len(failing) == 1, problems
    assert "## Model Changes" in failing[0], failing[0]


# --- the ticket spec gate and `wf.py post-spec` (#116) -------------------------

GATE_SPEC = """\
---
feature: demo
repo: owner/demo
---

# Spec: demo

## Problem Statement

PS-TEXT line one.

## Solution

SOL-TEXT line one.

## User Stories

1. US-TEXT as an owner.

## Premises

| # | Premise | Command | Output | Verdict |
|---|---|---|---|---|
| P1 | The sky | `echo blue` | blue | confirmed |

## Tickets

- #9 — one
- #10 — two
- PROJ-5 — a Jira ticket

## Delivery

- #9 — PR: https://github.com/owner/demo/pull/1 · Model: n/a — x · Mutation: n/a — x
- #10 — PR: https://github.com/owner/demo/pull/2 · Model: n/a — x · Mutation: n/a — x
- PROJ-5 — PR: https://github.com/owner/demo/pull/3 · Model: n/a — x · Mutation: n/a — x
"""
SECTIONS = ("Problem Statement", "Solution", "User Stories", "Premises")
ALL_HEADINGS = "\n".join(f"## {n}\nx" for n in SECTIONS)
NO_SPEC_BODY = "fix the typo in the docs"


def gate_repo(tmp_path: Path, text: str = GATE_SPEC) -> Path:
    root = tmp_path / "gate-repo"
    root.mkdir()
    write_spec(root, text, "demo")
    return root


class RecordingReader:
    """A fake `ticket_reader`: answers per number from `issues`, remembers every call."""

    def __init__(self, issues: dict[int, object]):
        self.issues = issues
        self.calls: list[tuple[str, list[int]]] = []

    def __call__(self, repo: str, numbers: list[int]) -> dict:
        self.calls.append((repo, list(numbers)))
        return {n: self.issues[n] for n in numbers}


def unread(reason: str):
    return wf._audit().Unread("gh issue view", reason)


def issue_dict(number: int, body: str = "", comments: tuple[str, ...] = ()) -> dict:
    return {"number": number, "state": "OPEN", "body": body, "comments": [{"body": c} for c in comments]}


def test_delivery_tickets_splits_github_numbers_from_other_refs_in_order_without_repeats(tmp_path):
    text = GATE_SPEC.replace("## Delivery\n", "## Delivery\n\n- #10 #9 — PR: u · Model: m · Mutation: n\n"
                             "- https://example.com/x/3 — PR: u · Model: m · Mutation: n\n")
    spec = wf.load_spec(gate_repo(tmp_path, text), "demo")
    assert wf.delivery_tickets(spec) == ([10, 9], ["https://example.com/x/3", "PROJ-5"])


def test_ticket_spec_check_fails_a_ticket_with_no_marker_and_names_the_ticket_and_the_command(tmp_path):
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: issue_dict(10, "## Solution")})
    problems, _ = wf.ticket_spec_check("demo", gate_repo(tmp_path), reader)
    assert problems == [("demo: ticket #9 has no spec section and no 'Skipped — reason' note: "
                         "run python3 wf.py post-spec demo")]


def test_ticket_spec_check_uses_issue_has_spec_so_a_marker_only_in_a_comment_passes(tmp_path):
    reader = RecordingReader({9: issue_dict(9, "prose", ("## Solution\nfix",)), 10: issue_dict(10, "## Solution")})
    assert wf.ticket_spec_check("demo", gate_repo(tmp_path), reader)[0] == []


def test_ticket_spec_check_notes_an_unreadable_ticket_with_its_reason_and_does_not_fail(tmp_path):
    reader = RecordingReader({9: unread("RuntimeError: connection refused"), 10: issue_dict(10, "## Solution")})
    problems, notes = wf.ticket_spec_check("demo", gate_repo(tmp_path), reader)
    assert problems == []
    assert "demo: ticket #9 not read: RuntimeError: connection refused" in notes


def test_ticket_spec_check_notes_a_jira_ref_as_not_judged_and_does_not_fail(tmp_path):
    reader = RecordingReader({9: issue_dict(9, "## Solution"), 10: issue_dict(10, "## Solution")})
    problems, notes = wf.ticket_spec_check("demo", gate_repo(tmp_path), reader)
    assert problems == []
    assert notes == ["demo: ticket PROJ-5 not judged: not a GitHub issue of owner/demo"]
    assert all("PROJ-5" not in str(call) for call in reader.calls)


def test_ticket_spec_check_with_no_spec_or_no_delivery_gives_nothing(tmp_path):
    reader = RecordingReader({})
    assert wf.ticket_spec_check("demo", tmp_path, reader) == ([], [])
    no_delivery = gate_repo(tmp_path, GATE_SPEC[:GATE_SPEC.index("## Delivery")])
    assert wf.ticket_spec_check("demo", no_delivery, reader) == ([], [])
    assert reader.calls == []


def test_ticket_spec_check_reads_each_ticket_once_even_when_two_rows_name_it(tmp_path):
    text = GATE_SPEC + "- #9 — PR: https://github.com/owner/demo/pull/4 · Model: n/a — x · Mutation: n/a — x\n"
    reader = RecordingReader({9: issue_dict(9, "## Solution"), 10: issue_dict(10, "## Solution")})
    wf.ticket_spec_check("demo", gate_repo(tmp_path, text), reader)
    assert reader.calls == [("owner/demo", [9, 10])]


def test_next_on_a_delivered_feature_fails_when_a_ticket_has_no_marker_and_exits_1(delivered, fake_gh):
    repo, kit = delivered
    fake_gh.issue(9, NO_SPEC_BODY)
    r = run_wf(repo, kit, "next", env=fake_gh.env)
    assert r.returncode == 1, r.stdout
    assert "FAIL tickets: demo: ticket #9 has no spec section" in r.stdout
    assert "python3 wf.py post-spec demo" in r.stdout


def test_next_on_a_delivered_feature_with_a_marker_still_prints_delivered_and_exits_0(delivered, fake_gh):
    repo, kit = delivered
    fake_gh.issue(9, "## Solution\nfix")
    r = run_wf(repo, kit, "next", env=fake_gh.env)
    assert r.returncode == 0 and "DELIVERED" in r.stdout and "FAIL tickets" not in r.stdout, r.stdout
    assert "ticket https://example.com/owner/demo/issues/2 not judged" in r.stdout


def test_next_on_a_delivered_feature_says_an_unreadable_ticket_was_not_read_and_still_exits_0(delivered, fake_gh):
    repo, kit = delivered
    r = run_wf(repo, kit, "next", env=fake_gh.env)  # the fake has no issue 9: every attempt fails
    assert r.returncode == 0 and "DELIVERED" in r.stdout, r.stdout
    assert "ticket #9 not read:" in r.stdout


def test_next_on_a_feature_not_delivered_never_calls_gh(repo, kit, fake_gh):
    write_spec(repo, swap("`echo blue`", ""))
    r = run_wf(repo, kit, "next", env=fake_gh.env)
    assert r.returncode == 1 and "FAIL phase 1" in r.stdout, r.stdout
    assert fake_gh.calls() == []


def test_status_never_calls_gh_even_on_a_delivered_feature(delivered, fake_gh):
    repo, kit = delivered
    r = run_wf(repo, kit, "status", env=fake_gh.env)
    assert r.returncode == 0 and "DELIVERED" in r.stdout, r.stdout
    assert fake_gh.calls() == []


def test_derive_never_reads_or_writes_the_tracker_even_on_a_delivered_feature(delivered, monkeypatch):
    repo, kit = delivered

    def boom(*args, **kwargs):
        raise AssertionError("derive touched the tracker")
    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        assert cmd[0] != "gh", cmd
        return real_run(cmd, *args, **kwargs)
    for name in ("read_tickets", "post_comment", "ticket_spec_check", "run_post_spec"):
        monkeypatch.setattr(wf, name, boom)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    assert wf.derive(repo, FEATURE, evidence_store(kit)).phase is None


def test_end_fails_a_delivered_feature_whose_ticket_has_no_marker_and_prints_the_note_of_an_unreadable_one(
        tmp_path, capsys):
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / "handoff-demo-20261116.md").write_text(handoff_with(""))
    repo = gate_repo(tmp_path)
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: unread("RuntimeError: connection refused")})
    code = wf.run_end(wdir, ("demo",), repo, ticket_reader=reader)
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL: demo: ticket #9 has no spec section" in out
    assert "ticket #10 not read: RuntimeError: connection refused" in out
    assert "FAIL: demo: ticket #10" not in out


def test_end_passes_when_every_ticket_carries_a_marker(tmp_path, capsys):
    wdir = make_gate_dir(tmp_path)
    (wdir / "handoffs" / f"handoff-demo-{date.today():%Y%m%d}.md").write_text(handoff_with(""))
    reader = RecordingReader({9: issue_dict(9, "## Solution"), 10: issue_dict(10, "Skipped — x")})
    code = wf.run_end(wdir, ("demo",), gate_repo(tmp_path), ticket_reader=reader)
    out = capsys.readouterr().out
    assert code == 0 and "FAIL" not in out, out


def test_missing_sections_counts_only_a_line_that_is_exactly_the_heading():
    prose = {"body": "see the `## Solution` below and ### Problem Statement", "comments": []}
    assert wf.missing_sections(prose) == list(SECTIONS)
    heading_in_comment = {"body": "prose", "comments": [{"body": "x\n## Solution\ny"}]}
    assert wf.missing_sections(heading_in_comment) == ["Problem Statement", "User Stories", "Premises"]


def test_missing_sections_does_not_count_a_deeper_heading_with_the_same_name():
    deeper = {"body": "### Problem Statement\nx\n#### Solution\ny", "comments": []}
    assert wf.missing_sections(deeper) == list(SECTIONS)


def test_split_refs_lists_each_ticket_once_in_first_seen_order():
    assert wf.split_refs(["#5", "AVS-1", "#5", "#7", "AVS-1"]) == ([5, 7], ["AVS-1"])


def test_missing_sections_is_empty_when_all_four_are_present_across_body_and_comments():
    issue = {"body": "## Problem Statement\nx\n## Solution\ny",
             "comments": [{"body": "## User Stories\nz"}, {"body": "## Premises\nw"}]}
    assert wf.missing_sections(issue) == []


def test_spec_comment_has_the_real_headings_in_the_spec_order_and_the_spec_text(tmp_path):
    spec = wf.load_spec(gate_repo(tmp_path), "demo")
    premises = spec.sections["Premises"].strip()
    assert wf.spec_comment(spec, list(SECTIONS)) == (
        "## Problem Statement\n\nPS-TEXT line one.\n\n## Solution\n\nSOL-TEXT line one.\n\n"
        f"## User Stories\n\n1. US-TEXT as an owner.\n\n## Premises\n\n{premises}")


def test_spec_comment_of_only_the_missing_sections_omits_the_others(tmp_path):
    spec = wf.load_spec(gate_repo(tmp_path), "demo")
    body = wf.spec_comment(spec, ["Solution"])
    assert body == "## Solution\n\nSOL-TEXT line one."


def test_spec_comment_over_the_limit_raises_naming_the_section(tmp_path):
    spec = wf.load_spec(gate_repo(tmp_path, GATE_SPEC.replace(
        "SOL-TEXT line one.", "x" * (wf.COMMENT_LIMIT - len("## Solution\n\n") + 1))), "demo")
    assert len(wf.spec_comment(spec, ["Problem Statement"])) < wf.COMMENT_LIMIT
    with pytest.raises(ValueError, match=r"Solution.*65001|65001.*Solution"):
        wf.spec_comment(spec, ["Solution"])


def test_spec_comment_at_the_limit_is_accepted(tmp_path):
    spec = wf.load_spec(gate_repo(tmp_path, GATE_SPEC.replace(
        "SOL-TEXT line one.", "x" * (wf.COMMENT_LIMIT - len("## Solution\n\n")))), "demo")
    assert len(wf.spec_comment(spec, ["Solution"])) == wf.COMMENT_LIMIT


def test_spec_comment_with_an_empty_section_raises(tmp_path):
    empty = wf.load_spec(gate_repo(tmp_path, GATE_SPEC.replace("SOL-TEXT line one.", "")), "demo")
    with pytest.raises(ValueError, match="Solution"):
        wf.spec_comment(empty, ["Solution"])


def test_spec_comment_with_an_absent_section_raises(tmp_path):
    absent = wf.load_spec(gate_repo(tmp_path, GATE_SPEC.replace("## Solution", "## Other")), "demo")
    with pytest.raises(ValueError, match="Solution"):
        wf.spec_comment(absent, ["Solution"])


def test_posted_sections_are_the_four_spec_sections_in_this_order():
    assert wf.POSTED_SECTIONS == ("Problem Statement", "Solution", "User Stories", "Premises")


class RecordingCommenter:
    def __init__(self, fail_on: tuple[int, ...] = ()):
        self.posts: list[tuple[str, int, str]] = []
        self.fail_on = fail_on

    def __call__(self, repo: str, number: int, body: str) -> None:
        if number in self.fail_on:
            raise RuntimeError("HTTP 502")
        self.posts.append((repo, number, body))


def test_post_spec_posts_one_comment_with_the_missing_sections_and_none_to_a_ticket_that_has_them_all(
        tmp_path, capsys):
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: issue_dict(10, ALL_HEADINGS)})
    commenter = RecordingCommenter()
    code = wf.run_post_spec("demo", gate_repo(tmp_path), reader, commenter)
    out = capsys.readouterr().out
    assert code == 0
    assert [(r, n) for r, n, _ in commenter.posts] == [("owner/demo", 9)]
    assert len(commenter.posts) == 1 and commenter.posts[0][2].startswith("## Problem Statement\n\nPS-TEXT")
    assert "#9: posted Problem Statement, Solution, User Stories, Premises" in out
    assert "#10: already carries the spec sections, nothing posted" in out


def test_post_spec_only_posts_the_sections_the_ticket_lacks(tmp_path):
    reader = RecordingReader({9: issue_dict(9, "## Solution\nby hand"), 10: issue_dict(10, ALL_HEADINGS)})
    commenter = RecordingCommenter()
    wf.run_post_spec("demo", gate_repo(tmp_path), reader, commenter)
    assert len(commenter.posts) == 1
    body = commenter.posts[0][2]
    assert "## Problem Statement" in body and "## Solution" not in body


def test_post_spec_twice_posts_once(tmp_path, fake_gh):
    repo = gate_repo(tmp_path)
    fake_gh.issue(9, NO_SPEC_BODY)
    fake_gh.issue(10, ALL_HEADINGS)
    env = {**os.environ, **fake_gh.env}
    cmd = [sys.executable, str(WF), "post-spec", "demo", "--repo-root", str(repo)]
    first = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    second = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    assert first.returncode == 0 and second.returncode == 0, first.stdout + first.stderr + second.stderr
    assert [p["number"] for p in fake_gh.posted()] == [9]
    assert "#9: already carries the spec sections, nothing posted" in second.stdout
    assert "--body-file" in fake_gh.posted()[0]["args"] and fake_gh.posted()[0]["body"].startswith("## Problem Statement")


def test_post_spec_never_edits_the_issue_body(tmp_path, fake_gh):
    repo = gate_repo(tmp_path)
    fake_gh.issue(9, NO_SPEC_BODY)
    fake_gh.issue(10, ALL_HEADINGS)
    env = {**os.environ, **fake_gh.env}
    subprocess.run([sys.executable, str(WF), "post-spec", "demo", "--repo-root", str(repo)],
                   capture_output=True, text=True, check=False, env=env)
    assert fake_gh.issue_json(9)["body"] == NO_SPEC_BODY
    assert fake_gh.posted(), "the run must have posted, or the body check proves nothing"
    assert not [c for c in fake_gh.calls() if c.startswith("issue edit")]


def test_post_spec_skips_a_non_github_ticket_and_says_so(tmp_path, capsys):
    reader = RecordingReader({9: issue_dict(9, ALL_HEADINGS), 10: issue_dict(10, ALL_HEADINGS)})
    wf.run_post_spec("demo", gate_repo(tmp_path), reader, RecordingCommenter())
    assert "ticket PROJ-5 not judged: not a GitHub issue of owner/demo" in capsys.readouterr().out
    assert reader.calls == [("owner/demo", [9, 10])]


def test_post_spec_takes_the_tickets_from_the_tickets_section_not_from_delivery(tmp_path):
    text = GATE_SPEC[:GATE_SPEC.index("## Delivery")]
    reader = RecordingReader({9: issue_dict(9, ALL_HEADINGS), 10: issue_dict(10, ALL_HEADINGS)})
    assert wf.run_post_spec("demo", gate_repo(tmp_path, text), reader, RecordingCommenter()) == 0
    assert reader.calls == [("owner/demo", [9, 10])]


def test_post_spec_an_unreadable_ticket_is_a_fail_line_exit_1_and_the_other_tickets_are_still_tried(
        tmp_path, capsys):
    reader = RecordingReader({9: unread("RuntimeError: connection refused"), 10: issue_dict(10, NO_SPEC_BODY)})
    commenter = RecordingCommenter()
    code = wf.run_post_spec("demo", gate_repo(tmp_path), reader, commenter)
    assert code == 1
    assert "FAIL: ticket #9 not read: RuntimeError: connection refused" in capsys.readouterr().out
    assert [n for _, n, _ in commenter.posts] == [10]


def test_post_spec_a_failed_post_is_a_fail_line_exit_1_and_the_other_tickets_are_still_tried(tmp_path, capsys):
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: issue_dict(10, NO_SPEC_BODY)})
    commenter = RecordingCommenter(fail_on=(9,))
    code = wf.run_post_spec("demo", gate_repo(tmp_path), reader, commenter)
    assert code == 1
    assert "FAIL: ticket #9 not posted: HTTP 502" in capsys.readouterr().out
    assert [n for _, n, _ in commenter.posts] == [10]


def test_post_spec_a_section_over_the_limit_posts_nothing_names_the_section_and_exits_1(tmp_path, capsys):
    text = GATE_SPEC.replace("SOL-TEXT line one.", "x" * wf.COMMENT_LIMIT)
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: issue_dict(10, ALL_HEADINGS)})
    commenter = RecordingCommenter()
    code = wf.run_post_spec("demo", gate_repo(tmp_path, text), reader, commenter)
    assert code == 1 and commenter.posts == []
    out = capsys.readouterr().out
    assert "FAIL: ticket #9 not posted:" in out and "Solution" in out


def test_post_spec_with_no_spec_file_or_no_repo_is_exit_2(tmp_path, capsys):
    reader = RecordingReader({})
    assert wf.run_post_spec("demo", tmp_path, reader, RecordingCommenter()) == 2
    no_repo = gate_repo(tmp_path, GATE_SPEC.replace("repo: owner/demo\n", ""))
    assert wf.run_post_spec("demo", no_repo, reader, RecordingCommenter()) == 2
    assert reader.calls == []
    assert capsys.readouterr().err.count("wf:") == 2


def test_post_spec_a_spec_that_lacks_a_section_is_exit_2_and_posts_nothing(tmp_path, capsys):
    reader = RecordingReader({9: issue_dict(9, NO_SPEC_BODY), 10: issue_dict(10, NO_SPEC_BODY)})
    commenter = RecordingCommenter()
    text = GATE_SPEC.replace("## User Stories", "## Stories")
    assert wf.run_post_spec("demo", gate_repo(tmp_path, text), reader, commenter) == 2
    assert commenter.posts == [] and "User Stories" in capsys.readouterr().err


def test_post_spec_with_no_github_ticket_in_tickets_says_there_is_nothing_to_post_and_exits_0(tmp_path, capsys):
    text = GATE_SPEC.replace("- #9 — one\n- #10 — two\n", "")
    text = text[:text.index("## Delivery")]
    reader = RecordingReader({})
    assert wf.run_post_spec("demo", gate_repo(tmp_path, text), reader, RecordingCommenter()) == 0
    assert reader.calls == [] and "nothing to post" in capsys.readouterr().out


def test_post_comment_sends_the_body_on_stdin_to_gh_issue_comment(tmp_path, fake_gh, monkeypatch):
    fake_gh.issue(7, "x")
    monkeypatch.setenv("PATH", fake_gh.env["PATH"])
    monkeypatch.setenv("FAKE_GH_DIR", fake_gh.env["FAKE_GH_DIR"])
    wf.post_comment("owner/demo", 7, "## Solution\n\nbody")
    assert len(fake_gh.posted()) == 1
    [posted] = fake_gh.posted()
    assert posted["args"] == ["issue", "comment", "7", "--repo", "owner/demo", "--body-file", "-"]
    assert posted["body"] == "## Solution\n\nbody"


def test_post_comment_raises_on_a_failing_gh_a_missing_gh_and_a_timeout(tmp_path, fake_gh, monkeypatch):
    monkeypatch.setenv("PATH", fake_gh.env["PATH"])
    monkeypatch.setenv("FAKE_GH_DIR", fake_gh.env["FAKE_GH_DIR"])
    with pytest.raises(RuntimeError, match="refused|could not"):
        wf.post_comment("owner/demo", 8, "b")  # the fake has no issue 8: exit 1
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError):
        wf.post_comment("owner/demo", 7, "b")
    monkeypatch.setattr(wf.subprocess, "run", mock.Mock(side_effect=wf.subprocess.TimeoutExpired("gh", 30)))
    with pytest.raises(RuntimeError, match="timed out|Timeout|30"):
        wf.post_comment("owner/demo", 7, "b")


def test_read_tickets_returns_the_issue_dicts_from_gh_in_one_pooled_read(fake_gh, monkeypatch):
    fake_gh.issue(3, "b3", ("c",))
    fake_gh.issue(4, "b4")
    monkeypatch.setenv("PATH", fake_gh.env["PATH"])
    monkeypatch.setenv("FAKE_GH_DIR", fake_gh.env["FAKE_GH_DIR"])
    got = wf.read_tickets("owner/demo", [4, 3])
    assert list(got) == [4, 3] and got[3]["body"] == "b3" and got[3]["comments"] == [{"body": "c"}]
    assert sorted(fake_gh.calls()) == [
        "issue view 3 --repo owner/demo --json number,state,body,comments",
        "issue view 4 --repo owner/demo --json number,state,body,comments"]


def test_read_tickets_returns_unread_with_a_reason_when_gh_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    got = wf.read_tickets("owner/demo", [5])
    assert 5 in got and not isinstance(got[5], dict) and "gh" in got[5].reason
    assert wf.read_tickets("owner/demo", []) == {}
