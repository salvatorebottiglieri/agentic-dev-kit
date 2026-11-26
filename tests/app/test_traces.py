"""Tests for app/traces.py, the projection behind the Open and History tabs.

Seam (agreed): `snapshot()` and its parts over REAL temporary git repos and
linked worktrees built in tmp_path (never the owner's repos). Spec fixtures
derive from the SPEC constant of tests/test_harness.py, a spec valid for every
phase, so `wf.derive` reports it delivered.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
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


report_store = load("report_store", ROOT / "report_store.py")
traces = load("traces", APP / "traces.py")
catalog = load("catalog", APP / "catalog.py")
harness = load("harness_fixture_for_app", ROOT / "tests" / "test_harness.py")

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                         cwd=cwd, capture_output=True, text=True, check=True, env=GIT_ENV)
    return out.stdout


def make_repo(root: Path, name: str, origin: str | None = None) -> Path:
    primary = root / name
    primary.mkdir(parents=True)
    git(primary, "init", "-q", "-b", "master")
    if origin:
        git(primary, "remote", "add", "origin", origin)
    (primary / "README.md").write_text("x\n")
    commit(primary, "init")
    return primary


def commit(tree: Path, message: str) -> None:
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", message)


def add_worktree(primary: Path, suffix: str, branch: str) -> Path:
    path = primary.parent / f"{primary.name}-{suffix}"
    git(primary, "worktree", "add", "-q", "-b", branch, str(path))
    return path


def write_claim(primary: Path, slug: str, text: str = "claimed\n") -> Path:
    claims = primary / ".git" / "agentic-sessions"
    claims.mkdir(exist_ok=True)
    (claims / slug).write_text(text)
    return claims / slug


DELIVERED = harness.SPEC  # phases 5-6 skipped with approval: derive says delivered


def delivered_spec(slug: str, branch: str) -> str:
    return (DELIVERED.replace("feature: demo\n", f"feature: {slug}\n", 1)
            .replace("branch: feature/demo\n", f"branch: {branch}\n", 1))


def undelivered_spec(slug: str, branch: str, approved: bool = False) -> str:
    """Phase plan only: derive says phase 0 unapproved, or phase 1 once approved."""
    rows = "".join(f"| {n} · P | run | — |\n" for n in range(7))
    approval = 'Approved: 2026-11-16, owner in chat — "Ok"\n' if approved else ""
    return (f"---\nfeature: {slug}\nrepo: owner/x\nbranch: {branch}\n---\n\n## Phase plan\n\n"
            f"| Phase | Decision | Reason |\n|---|---|---|\n{rows}\n{approval}")


def write_spec(tree: Path, slug: str, text: str) -> None:
    path = tree / "docs" / "specs" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    mockup = tree / "docs" / "mockups" / "demo.md"
    mockup.parent.mkdir(parents=True, exist_ok=True)
    mockup.write_text("mockup\n")


NO_PRESENCE = Path("/nonexistent/presence-store-test")  # a missing store reads as ()
WORKFLOW: list[Path] = []   # the isolated workflow dir of the run (set by the fixture)


@pytest.fixture(scope="module", autouse=True)
def isolated_workflow(tmp_path_factory):
    """A workflow dir holding the real wf.py/audit.py but NO handoffs or reports, so a
    snapshot never reads the owner's corpus (it would add a history row per handoff)."""
    wdir = tmp_path_factory.mktemp("workflow")
    for name in ("wf.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    WORKFLOW[:] = [wdir]
    yield wdir
    WORKFLOW.clear()


def snap(*roots: Path, workflow: Path | None = None, presence: Path | None = None):
    return traces.snapshot(tuple(roots), workflow or WORKFLOW[0], NOW, presence or NO_PRESENCE)


def snap_with_other(*roots: Path, **kw):
    """`snap` with the #93 `other` rows listed in `open` too: for the tests whose subject is a
    worktree's facts (where, signals, agent, slug), not whether it is a feature row."""
    result = snap(*roots, **kw)
    return replace(result, open=(*result.open, *result.other))


def keys(rows) -> set[tuple[str, str]]:
    return {(r.repo, r.slug) for r in rows}


# --- invariants -------------------------------------------------------------

def test_rows_join_on_repo_and_exact_slug(tmp_path):
    """Witness: two clones whose origins differ in case AND form
    (`https://host/scm/acme/x.git` vs `git@host:ACME/X.git`) both claiming `s`,
    a claim `s-resume` in the same repo, and `s` again in another repo -
    distinguishes 'join on case-folded repo + exact slug' (3 rows, the first
    holding both claims) from a case-sensitive repo join (4 rows), a slug-only
    join (2 rows) and a slug-prefix join (`s-resume` folded into `s`)."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/x.git")
    two = make_repo(root, "two", "git@host:ACME/X.git")
    other = make_repo(root, "other", "https://host/scm/other/y.git")
    write_claim(one, "s")
    write_claim(one, "s-resume")
    write_claim(two, "s")
    write_claim(other, "s")
    rows = snap(root).open
    assert keys(rows) == {("acme/x", "s"), ("acme/x", "s-resume"), ("other/y", "s")}
    joined = next(r for r in rows if (r.repo, r.slug) == ("acme/x", "s"))
    assert sorted(t.path for t in joined.traces) == sorted(
        [str(one / ".git/agentic-sessions/s"), str(two / ".git/agentic-sessions/s")])
    assert len({r.id for r in rows}) == 3


def test_open_iff_live_trace_or_undelivered_spec(tmp_path):
    """Witness: in one repo a delivered spec (`done`), a delivered spec that
    also has a claim (`done-claimed`), a claim-only slug, an undelivered spec in
    the primary and a worktree-only slug - distinguishes 'open iff a live trace
    or an undelivered spec' from 'open iff the spec is undelivered' (claim-only
    and worktree-only would land in history) and from 'every spec is open'
    (`done` must be history)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "done", delivered_spec("done", "feature/done"))
    write_spec(primary, "done-claimed", delivered_spec("done-claimed", "feature/dc"))
    write_spec(primary, "wip", undelivered_spec("wip", "feature/wip"))
    commit(primary, "specs")
    write_claim(primary, "done-claimed")
    write_claim(primary, "claim-only")
    add_worktree(primary, "wt-only", "feature/wt-only")
    result = snap(root)
    assert {r.slug for r in result.open} == {"done-claimed", "claim-only", "wip"}
    assert {r.slug for r in result.other} == {"wt-only"}
    assert {r.slug for r in result.history} == {"done"}
    assert result.partial is False
    done = result.history[0]
    assert done.phase.kind == "delivered" and done.phase.phase is None
    wip = next(r for r in result.open if r.slug == "wip")
    assert wip.phase.kind == "derived" and wip.phase.phase == 0
    assert wip.phase.verdicts[-1]["status"] in {"todo", "fail"}
    claim_only = next(r for r in result.open if r.slug == "claim-only")
    assert claim_only.phase == traces.PhaseView("no-spec", None, ())


def live_record(presence: Path, root: Path, slug: str, *, alive: bool) -> None:
    """A record of THIS test process (the scan reads the real /proc); `alive=False` is the same
    pid with other start ticks, i.e. a reused pid."""
    comm, _, ticks = harness.wf.proc_stat(os.getpid())
    harness.write_record(presence, "live", slug=slug, repo_root=str(root), pid=os.getpid(),
                         process=comm, start_ticks=ticks if alive else ticks + 1)


def test_a_live_agent_keeps_a_delivered_row_open(tmp_path):
    """Witness: a delivered spec `done` in the primary, no worktree, no claim, whose only trace
    besides the spec is a live agent - distinguishes 'open iff the agent is alive' (open) from
    the old rule (history); the same record with a reused pid (dead) goes back to history."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "done", delivered_spec("done", "feature/done"))
    commit(primary, "specs")
    presence = tmp_path / "presence"
    live_record(presence, primary, "done", alive=True)
    alive = snap(root, presence=presence)
    assert [r.slug for r in alive.open] == ["done"] and alive.history == ()
    assert alive.open[0].agent["pid"] == os.getpid()
    live_record(presence, primary, "done", alive=False)
    gone = snap(root, presence=presence)
    assert gone.open == () and [r.slug for r in gone.history] == ["done"]


def test_open_follows_an_unknown_phase_not_a_git_error(tmp_path):
    """Witness A: a delivered spec, no worktree, no claim, whose only error is a
    git failure (`base` names a ref that does not exist, so `rev-list` fails) -
    history. Witness B: a spec whose derive raises (phase unknown) - open.
    Together they distinguish the amended law ('open iff the phase is unknown')
    from 'ignore errors' (B would be history) and from 'any error is open'
    (A would be open)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "done", delivered_spec("done", "feature/done"))
    write_spec(primary, "broken", delivered_spec("broken", "feature/broken"))
    commit(primary, "specs")
    real = traces.load_wf(ROOT)

    def derive(repo_root, feature, store=None):
        if feature == "broken":
            raise RuntimeError("guard crashed")
        return real.derive(repo_root, feature, store)

    fake = SimpleNamespace(derive=derive, parse_front_matter=real.parse_front_matter)
    repo = traces.Repo(primary, "acme/alpha", "origin/nothing-here", ())
    rows, _ = traces.group_rows(traces.repo_traces(repo, fake), (repo,), fake)
    by_slug = {r.slug: r for r in rows}
    assert "rev-list" in by_slug["done"].error and by_slug["done"].phase.kind == "delivered"
    assert not traces.is_open(by_slug["done"])
    assert by_slug["broken"].phase.kind == "no-spec"
    assert traces.is_open(by_slug["broken"])


def test_a_worktree_copy_on_the_spec_branch_wins_over_any_primary_copy(tmp_path):
    """Witness: the primary's `feat` copy names another branch (then none) while
    the worktree on `feature/feat` holds it delivered - distinguishes 'derive on
    the tree whose checked-out branch is the spec's branch' (delivered) from
    'the primary's copy picks the tree' (phase 0)."""
    for primary_branch in ("feature/other", None):
        root = tmp_path / ("roots-" + str(primary_branch).replace("/", "-"))
        primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
        stale = undelivered_spec("feat", "feature/other")
        if primary_branch is None:
            stale = stale.replace("branch: feature/other\n", "")
        write_spec(primary, "feat", stale)
        commit(primary, "specs")
        wt = add_worktree(primary, "feat", "feature/feat")
        write_spec(wt, "feat", delivered_spec("feat", "feature/feat"))
        commit(wt, "advance the spec")
        result = snap(root)
        feat = next(r for r in result.open + result.history if r.slug == "feat")
        assert feat.phase.kind == "delivered", primary_branch


def test_an_unreadable_spec_is_a_source_error_and_the_repo_stays(tmp_path):
    """Witness: one spec that is not UTF-8 beside a claim and a good spec in the
    same repo - distinguishes 'a SourceError naming that file, partial, other
    rows kept' from 'the whole repo vanishes' (no rows, error names the repo); and
    witness `bad.md` - distinguishes an error row in open from the file silently
    absent or an error row in history."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "good", undelivered_spec("good", "feature/good"))
    bad = primary / "docs" / "specs" / "bad.md"
    bad.write_bytes(b"---\nfeature: bad\n---\n\xff\xfe\n")
    commit(primary, "specs")
    write_claim(primary, "alive")
    result = snap(root)
    assert result.partial is True
    assert [e.source for e in result.errors] == [str(bad)]
    assert "UTF-8" in result.errors[0].message
    assert {r.slug for r in result.open} == {"good", "alive", "bad"}
    row = next(r for r in result.open if r.slug == "bad")
    assert row.repo == "acme/alpha" and "UTF-8" in row.error
    assert all(r.error is None for r in result.open if r.slug != "bad")
    assert result.history == ()


def test_phase_comes_from_the_tree_with_the_branch(tmp_path):
    """Witness: master holds a stale `feat` spec (phase 0, unapproved) and the
    worktree on `feature/feat` holds it delivered; `other` is inherited by the
    worktree from master - distinguishes 'derive on the tree with the branch'
    (row `feat` delivered) from 'always the primary' (phase 0), and 'a
    worktree contributes every inherited spec' (`other` would get two spec
    traces, one stale copy per worktree)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "feat", undelivered_spec("feat", "feature/feat"))
    write_spec(primary, "other", delivered_spec("other", "feature/other"))
    commit(primary, "specs")
    wt = add_worktree(primary, "feat", "feature/feat")
    write_spec(wt, "feat", delivered_spec("feat", "feature/feat"))
    commit(wt, "advance the spec")
    result = snap(root)
    feat = next(r for r in result.open if r.slug == "feat")
    assert feat.phase.kind == "delivered"
    other = next(r for r in result.history if r.slug == "other")
    specs = [t for t in other.traces if t.kind == "spec"]
    assert [t.path for t in specs] == [str(primary / "docs/specs/other.md")]
    assert keys(result.open + result.history) == {("acme/alpha", "feat"), ("acme/alpha", "other")}
    assert result.partial is False
    # control: with the branch not checked out anywhere, the primary answers
    git(primary, "worktree", "remove", "--force", str(wt))
    after = snap(root)
    assert next(r for r in after.open if r.slug == "feat").phase.phase == 0


def test_unreadable_source_makes_the_snapshot_partial(tmp_path):
    """Witness: a primary whose `.git/HEAD` is garbage next to a healthy repo
    with a claim - distinguishes 'report it: partial true + a SourceError naming
    the path, healthy repo still listed' from 'skip it silently' (partial
    false) and from 'abort the scan' (no rows)."""
    root = tmp_path / "roots"
    good = make_repo(root, "good", "https://host/scm/acme/good.git")
    write_claim(good, "alive")
    broken = make_repo(root, "broken", "https://host/scm/acme/broken.git")
    (broken / ".git" / "HEAD").write_text("not a ref\n")
    result = snap(root)
    assert result.partial is True
    assert [e.source for e in result.errors] == [str(broken)]
    assert result.errors[0].message
    assert sorted(r.slug for r in result.open) == ["?", "alive"]
    row = next(r for r in result.open if r.slug == "?")
    assert row.repo == f"local:{broken}"
    assert row.where.path == str(broken) and row.where.kind == "primary"
    assert row.phase.kind == "no-spec" and row.traces == ()
    assert row.error == result.errors[0].message
    assert next(r for r in result.open if r.slug == "alive").error is None
    assert result.history == ()


def test_a_repo_failing_after_discovery_makes_the_snapshot_partial(tmp_path):
    """Witness: a primary whose linked worktree `.git` file points to a gitdir that
    does not exist (`git worktree list` in the primary still exits 0, `git log`
    in the worktree exits 128) next to a healthy repo with a claim -
    distinguishes 'report the scan failure: partial true + a SourceError naming
    the primary, healthy repo still listed' from 'drop it silently'; witness
    `sick` and `broken` - distinguish an error row in open from rows silently
    absent or an error row in history."""
    root = tmp_path / "roots"
    good = make_repo(root, "good", "https://host/scm/acme/good.git")
    write_claim(good, "alive")
    sick = make_repo(root, "sick", "https://host/scm/acme/sick.git")
    worktree = add_worktree(sick, "feat", "feature/feat")
    (worktree / ".git").write_text(f"gitdir: {sick / '.git' / 'worktrees' / 'gone'}\n")
    assert traces.read_repo(sick).worktrees  # discovery reads the repo fine
    result = snap(root)
    assert result.partial is True
    assert [e.source for e in result.errors] == [str(sick)]
    assert result.errors[0].message
    assert sorted(r.slug for r in result.open) == ["?", "alive"]
    row = next(r for r in result.open if r.slug == "?")
    assert row.repo == "acme/sick" and row.where.path == str(sick)
    assert row.error == result.errors[0].message and "exit" in row.error
    assert row.phase.kind == "no-spec" and row.traces == ()
    assert result.history == ()


def test_two_clones_of_one_origin_failing_after_discovery_keep_one_error_row_each(tmp_path):
    """Witness: two clones with the same origin URL, each with a worktree whose
    gitdir is gone - distinguishes 'one `?` row per unreadable repo, each naming
    its own path' from 'rows keyed by (repo, slug) collapse into one'."""
    root = tmp_path / "roots"
    sick = []
    for name in ("one", "two"):
        primary = make_repo(root, name, "https://host/scm/acme/same.git")
        worktree = add_worktree(primary, "feat", "feature/feat")
        (worktree / ".git").write_text(f"gitdir: {primary / '.git' / 'worktrees' / 'gone'}\n")
        sick.append(primary)
    result = snap(root)
    rows = [r for r in result.open if r.slug == "?"]
    assert sorted(r.where.path for r in rows) == sorted(str(p) for p in sick)
    assert {r.repo for r in rows} == {"acme/same"}
    assert len({r.id for r in rows}) == 2
    assert len(result.errors) == 2


def test_a_bad_spec_row_has_the_id_it_has_once_the_spec_is_readable(tmp_path):
    """Witness: the same (repo, slug) `bad` first as an unreadable spec, then
    fixed - distinguishes 'the normal id rule' from an id that also hashes the
    source path (the page would lose its selection)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    bad = primary / "docs" / "specs" / "bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"---\nfeature: bad\n---\n\xff\xfe\n")
    commit(primary, "specs")
    before = next(r for r in snap(root).open if r.slug == "bad")
    write_spec(primary, "bad", undelivered_spec("bad", "feature/bad"))
    commit(primary, "fix")
    after = next(r for r in snap(root).open if r.slug == "bad")
    assert after.error is None
    assert before.id == after.id


# --- identity and discovery -------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://host/scm/acme/x.git", "acme/x"),
    ("https://github.com/Owner/Name", "owner/name"),
    ("git@github.com:O/N.git", "o/n"),
    ("ssh://git@host:7999/ACME/x.git", "acme/x"),
    ("https://host/scm/acme/x.git/", "acme/x"),
])
def test_repo_identity_is_the_case_folded_owner_name(url, expected):
    assert traces.repo_identity(url, Path("/p/repo")) == expected


@pytest.mark.parametrize("url", [None, "", "x", "https://[bad/x/y"])
def test_repo_identity_without_a_usable_origin_is_local_path(url):
    """Witness: no URL, one with no `owner/name` pair, or an unparsable one
    (`https://[bad/x/y`, where urlsplit raises) - distinguishes
    `local:<primary path>` from a guessed or empty identity and from a raise."""
    assert traces.repo_identity(url, Path("/p/repo")) == "local:/p/repo"


def test_discover_finds_primary_trees_only(tmp_path):
    """Witness: a primary, its linked worktree (`.git` is a file), a plain
    directory and a repo nested two levels down - distinguishes 'direct
    children whose .git is a directory' from 'any .git' (the worktree would be
    a second repo) and from a recursive walk."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    add_worktree(primary, "x", "feature/x")
    (root / "plain").mkdir()
    make_repo(root / "plain", "deep")
    repos, errors = traces.discover_repos((root,))
    assert [r.primary for r in repos] == [primary]
    assert errors == ()


def test_discover_reports_an_unreadable_root_and_empty_roots(tmp_path):
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    repos, errors = traces.discover_repos((missing, empty))
    assert repos == ()
    assert [e.source for e in errors] == [str(missing)]


def test_discover_turns_a_broken_repo_into_a_source_error(tmp_path):
    root = tmp_path / "roots"
    broken = make_repo(root, "broken")
    (broken / ".git" / "HEAD").write_text("nope\n")
    make_repo(root, "fine")
    repos, errors = traces.discover_repos((root,))
    assert [r.primary.name for r in repos] == ["fine"]
    assert [e.source for e in errors] == [str(broken)]
    assert "git worktree list" in errors[0].message


def test_read_repo_reads_identity_base_and_linked_worktrees(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/ACME/Alpha.git")
    git(primary, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    wt = add_worktree(primary, "feat", "feature/feat")
    detached = add_worktree(primary, "det", "feature/det")
    git(detached, "checkout", "-q", "--detach")
    repo = traces.read_repo(primary)
    assert repo.identity == "acme/alpha" and repo.base == "origin/master"
    by_path = {w.path: w for w in repo.worktrees}
    assert set(by_path) == {wt, detached}
    assert by_path[wt].branch == "feature/feat"
    assert by_path[wt].head == git(wt, "rev-parse", "HEAD").strip()
    assert by_path[detached].branch is None


def test_read_repo_without_origin_is_local_with_no_base(tmp_path):
    """Witness: a repo with no `origin` and no `origin/HEAD` - distinguishes
    identity `local:<path>` and base None from a raised error."""
    primary = make_repo(tmp_path, "solo")
    repo = traces.read_repo(primary)
    assert repo.identity == f"local:{primary}"
    assert repo.base is None and repo.worktrees == ()


def test_read_repo_raises_git_error_on_a_broken_repo(tmp_path):
    primary = make_repo(tmp_path, "broken")
    (primary / ".git" / "HEAD").write_text("nope\n")
    with pytest.raises(traces.GitError, match="exit"):
        traces.read_repo(primary)


def test_git_out_returns_stdout_and_raises_git_error(tmp_path):
    primary = make_repo(tmp_path, "r")
    assert traces.git_out(primary, "rev-parse", "--is-inside-work-tree").strip() == "true"
    with pytest.raises(traces.GitError, match=r"git rev-parse --verify nope: exit \d+"):
        traces.git_out(primary, "rev-parse", "--verify", "nope")
    with pytest.raises(traces.GitError):
        traces.git_out(tmp_path / "does-not-exist", "status")


# --- traces -----------------------------------------------------------------

def test_worktree_slug_strips_the_primary_prefix_or_keeps_the_name():
    """Witness: `alpha-feat-x` under `alpha` and `beta-thing` under `alpha` -
    distinguishes 'minus `<primary>-`' (`feat-x`) from 'the part after the last
    dash' (`x`) and from guessing a match when the prefix is missing (the
    whole name, `beta-thing`)."""
    primary = Path("/h/alpha")
    assert traces.worktree_slug(Path("/h/alpha-feat-x"), primary) == "feat-x"
    assert traces.worktree_slug(Path("/h/beta-thing"), primary) == "beta-thing"
    assert traces.worktree_slug(Path("/h/alpha-"), primary) == "alpha-"


def test_repo_traces_lists_worktrees_claims_and_workflow_specs(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "ok", undelivered_spec("ok", "feature/ok"))
    write_spec(primary, "renamed", undelivered_spec("another", "feature/r"))   # feature != stem
    (primary / "docs/specs/architecture.md").write_text("# Architecture\n")      # no front matter
    commit(primary, "specs")
    wt = add_worktree(primary, "feat", "feature/feat")
    claim = write_claim(primary, "sess", "free text\nsecond line\n")
    os.utime(claim, (1_700_000_000, 1_700_000_000))
    repo = traces.read_repo(primary)
    found = {(t.kind, t.slug): t for t in traces.repo_traces(repo, traces.load_wf(ROOT))}
    assert set(found) == {("spec", "ok"), ("worktree", "feat"), ("claim", "sess")}
    assert found[("claim", "sess")].text == "free text\nsecond line\n"
    assert found[("claim", "sess")].at == 1_700_000_000
    assert found[("claim", "sess")].repo == "acme/alpha"
    assert found[("worktree", "feat")].branch == "feature/feat"
    assert found[("worktree", "feat")].path == str(wt)
    assert found[("worktree", "feat")].at == int(git(wt, "log", "-1", "--format=%ct"))
    assert found[("spec", "ok")].branch == "feature/ok"


def test_repo_traces_of_a_repo_with_nothing_is_empty(tmp_path):
    repo = traces.read_repo(make_repo(tmp_path, "bare"))
    assert traces.repo_traces(repo, traces.load_wf(ROOT)) == []


def test_load_wf_loads_wf_py_by_path_and_raises_when_missing(tmp_path):
    assert hasattr(traces.load_wf(ROOT), "derive")
    with pytest.raises(OSError):
        traces.load_wf(tmp_path)


# --- rows -------------------------------------------------------------------

def test_where_of_a_worktree_row_counts_dirty_and_ahead(tmp_path):
    """Witness: a worktree one commit ahead of origin/master with two untracked
    files - distinguishes `dirty=2, ahead=1` from a boolean dirty flag and from
    counting all commits."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    git(primary, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    wt = add_worktree(primary, "feat", "feature/feat")
    (wt / "a.txt").write_text("a")
    commit(wt, "ahead")
    (wt / "b.txt").write_text("b")
    (wt / "c.txt").write_text("c")
    row = snap_with_other(root).open[0]
    assert row.where == traces.Where("worktree", str(wt), "feature/feat", 2, 1, "origin/master")


def test_where_ahead_is_none_without_a_base_and_primary_rows_say_primary(tmp_path):
    """Witness: a repo with no origin/HEAD - distinguishes `ahead None` from 0;
    and a claim-only row is located at the primary tree."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "feat", "feature/feat")
    write_claim(primary, "claimed")
    by_slug = {r.slug: r for r in snap_with_other(root).open}
    assert by_slug["feat"].where.ahead is None and by_slug["feat"].where.base is None
    assert by_slug["feat"].where.dirty == 0
    claimed = by_slug["claimed"].where
    assert (claimed.kind, claimed.path, claimed.branch) == ("primary", str(primary), "master")
    assert claimed.dirty == 0 and claimed.ahead is None
    assert by_slug["claimed"].claim["text"] == "claimed\n"
    assert datetime.fromisoformat(by_slug["claimed"].claim["since"]).tzinfo is not None
    assert wt.exists()


def test_rows_are_sorted_by_last_activity_newest_first(tmp_path):
    """Witness: claims with mtimes out of alphabetical order - distinguishes
    newest-first from name order and from discovery order."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    for slug, at in (("b", 1_700_000_200), ("a", 1_700_000_100), ("c", 1_700_000_300)):
        os.utime(write_claim(primary, slug), (at, at))
    rows = snap(root).open
    assert [r.slug for r in rows] == ["c", "b", "a"]
    assert [r.last_activity for r in rows] == [1_700_000_300, 1_700_000_200, 1_700_000_100]


def test_a_derive_failure_sets_the_row_error_and_the_snapshot_partial(tmp_path):
    """Witness: a derive that raises on one spec while another derives fine -
    distinguishes 'that row carries the error, phase no-spec, the other row
    is intact, snapshot partial' from a crashed scan or an error-free row."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "bad", undelivered_spec("bad", "feature/bad"))
    write_spec(primary, "good", undelivered_spec("good", "feature/good"))
    commit(primary, "specs")
    real = traces.load_wf(ROOT)

    def derive(repo_root, feature, store=None):
        if feature == "bad":
            raise RuntimeError("guard crashed")
        return real.derive(repo_root, feature, store)

    fake = SimpleNamespace(derive=derive, parse_front_matter=real.parse_front_matter)
    repo = traces.read_repo(primary)
    rows, errors = traces.group_rows(traces.repo_traces(repo, fake), (repo,), fake)
    by_slug = {r.slug: r for r in rows}
    assert "guard crashed" in by_slug["bad"].error
    assert by_slug["bad"].phase.kind == "no-spec"
    assert by_slug["good"].error is None and by_slug["good"].phase.kind == "derived"
    assert len(errors) == 1 and "guard crashed" in errors[0].message


def test_a_missing_worktree_directory_sets_the_row_error(tmp_path):
    """A worktree whose directory vanished: git cannot run there; the row says
    so (dirty and ahead None) instead of the scan failing."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    gone = Path(tmp_path / "alpha-gone")
    repo = traces.Repo(primary, "acme/alpha", None,
                       (traces.Worktree(gone, "feature/gone", "0" * 40),))
    trace = traces.Trace("worktree", "acme/alpha", "gone", str(gone), "feature/gone", 1.0, "")
    rows, errors = traces.group_rows([trace], (repo,), traces.load_wf(ROOT))
    assert rows[0].error and rows[0].where.dirty is None and rows[0].where.ahead is None
    assert rows[0].where.kind == "worktree"
    assert len(errors) == 1


def test_row_phase_without_a_spec_is_no_spec(tmp_path):
    assert traces.row_phase(None, traces.load_wf(ROOT)) == \
        traces.PhaseView("no-spec", None, ())


def test_is_open_reads_the_traces_and_the_phase():
    def row(kinds, phase, error=None):
        ts = tuple(traces.Trace(k, "r", "s", "p", None, 0.0, "") for k in kinds)
        return traces.Row("i", "r", "s", 0.0, phase, traces.Where("none", None, None, None, None, None),
                          None, None, (), ts, error)
    derived = traces.PhaseView("derived", 3, ())
    delivered = traces.PhaseView("delivered", None, ())
    assert traces.is_open(row(["worktree"], delivered))
    assert traces.is_open(row(["claim"], delivered))
    assert traces.is_open(row(["spec"], derived))
    assert not traces.is_open(row(["spec"], delivered))


def test_a_computing_row_is_open():
    """Witness a spec row whose phase is still `computing` beside the same row `delivered` -
    distinguishes 'a phase not known yet keeps the row in Open' from computing falling to History
    (the rule that only `derived` or a spec without a phase is open)."""
    def row(phase):
        spec = traces.Trace("spec", "r", "s", "p", None, 0.0, "")
        return traces.Row("i", "r", "s", 0.0, phase, traces.Where("none", None, None, None, None, None),
                          None, None, (), (spec,), None)
    assert traces.is_open(row(traces.COMPUTING))
    assert not traces.is_open(row(traces.PhaseView("delivered", None, ())))


def test_snapshot_over_empty_roots_is_complete_and_empty(tmp_path):
    result = snap(tmp_path)
    assert result == traces.Snapshot(NOW.isoformat(), False, (), (), ())


def test_snapshot_raises_when_wf_py_is_missing(tmp_path):
    with pytest.raises(OSError):
        traces.snapshot((tmp_path,), tmp_path, NOW)


# --- wire form --------------------------------------------------------------

def test_to_json_is_the_spec_wire_shape(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_claim(primary, "claimed", "hello\n")
    wire = traces.to_json(snap(root))
    assert json.loads(json.dumps(wire)) == wire          # plain JSON types only
    assert set(wire) == {"generated_at", "partial", "errors", "open", "history", "other_worktrees"}
    assert wire["generated_at"] == NOW.isoformat() and wire["partial"] is False
    row = wire["open"][0]
    assert set(row) == {"id", "repo", "slug", "last_activity", "phase", "where", "claim",
                        "agent", "signals", "traces", "error", "links"}
    assert row["repo"] == "acme/alpha" and row["slug"] == "claimed"
    assert row["phase"] == {"kind": "no-spec", "phase": None, "verdicts": [], "refreshing": False}
    assert set(row["where"]) == {"kind", "path", "branch", "dirty", "ahead", "base", "base_note"}
    assert row["where"]["base_note"] is not None and row["where"]["base"] is None
    git(primary, "update-ref", "refs/remotes/origin/develop", "HEAD")
    with_base = traces.to_json(snap(root))["open"][0]["where"]
    assert with_base["base"] == "origin/develop" and with_base["base_note"] is None
    assert row["claim"]["text"] == "hello\n" and row["claim"]["since"]
    assert row["agent"] is None and row["error"] is None
    assert row["signals"] == [{"name": "claim-no-agent", "rule": "SESSION-ISOLATION.md § Claims",
                               "label": "claim, no agent recorded", "path": None}]
    assert [(t["kind"], t["path"]) for t in row["traces"]] == [
        ("claim", str(primary / ".git/agentic-sessions/claimed"))]
    assert row["traces"][0]["id"]
    assert datetime.fromisoformat(row["last_activity"]).tzinfo is not None


def test_snapshot_carries_the_primary_of_every_repo_and_never_serializes_it(tmp_path):
    """Witness two repos, one of them with a linked worktree - distinguishes 'the primary path of
    each repo' from the worktree path or an empty tuple; `to_json` keeps the spec's keys (and `other_worktrees`, #93)."""
    root = tmp_path / "roots"
    alpha = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    beta = make_repo(root, "beta", "https://host/scm/acme/beta.git")
    add_worktree(alpha, "f", "feature/f")
    result = snap(root)
    assert set(result.primaries) == {("acme/alpha", str(alpha)), ("acme/beta", str(beta))}
    assert set(traces.to_json(result)) == {"generated_at", "partial", "errors", "open", "history",
                                           "other_worktrees"}


def test_to_json_carries_errors_and_verdicts(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "wip", undelivered_spec("wip", "feature/wip"))
    commit(primary, "spec")
    broken = make_repo(root, "broken")
    (broken / ".git" / "HEAD").write_text("x\n")
    wire = traces.to_json(snap(root))
    assert wire["partial"] is True
    assert wire["errors"] == [{"source": str(broken), "message": wire["errors"][0]["message"]}]
    failing = next(r for r in wire["open"] if r["slug"] == "?")
    assert failing["repo"] == f"local:{broken}" and failing["error"]
    verdict = next(r for r in wire["open"] if r["slug"] == "wip")["phase"]["verdicts"][-1]
    assert set(verdict) == {"phase", "status", "problems", "reason"}
    assert isinstance(verdict["problems"], list)


# --- #25: handoffs, agents, signals, detail ---------------------------------

def make_workflow(tmp_path: Path) -> Path:
    """A workflow dir of its own (real wf.py/audit.py) for tests that write handoffs/reports."""
    wdir = tmp_path / "workflow"
    wdir.mkdir()
    for name in ("wf.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    return wdir


def write_handoff(wdir: Path, name: str, repo_line: str | None = "- **Repo**: acme/alpha",
                  next_session: str | None = None) -> Path:
    """handoffs/handoff-<name>.md in the shape of the real corpus (Repo line under the title)."""
    lines = [f"# Handoff {name}", "", "- **Session**: 2026-11-16"]
    if repo_line is not None:
        lines.append(repo_line)
    lines += ["", "## Done", "", "- built it", ""]
    if next_session is not None:
        lines += ["## Next session", "", next_session, ""]
    lines += ["## Ground truth", "", "- ok", ""]
    path = wdir / "handoffs" / f"handoff-{name}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines))
    return path


def row_of(result, slug: str, repo: str | None = None):
    found = [r for r in (*result.open, *result.history)
             if r.slug == slug and (repo is None or r.repo == repo)]
    assert len(found) == 1, (slug, repo, [(r.repo, r.slug) for r in found])
    return found[0]


def alive_record(presence: Path, proc: Path, name: str, root: Path, slug: str, *,
                 pid: int = 4242, comm: str = "claude", ticks: int = 360000) -> None:
    harness.fake_proc(proc, pid, comm, 1, ticks)
    harness.write_record(presence, name, slug=slug, repo_root=str(root), pid=pid,
                         process=comm, start_ticks=ticks)


def fake_stat(proc: Path, btime: int = 1_700_000_000) -> None:
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text(f"cpu  1 2 3 4\nbtime {btime}\nprocesses 9\n")


def test_handoff_traces_reads_slug_repo_and_the_file_name_date(tmp_path):
    """Witness: handoffs `x-20261116`, `evidence-resolution-20260830b` (the corpus's lettered
    date), one without a Repo line and one with `(nessuno)` - distinguishes parsing the date
    from the file name (the files are written now, so mtime would give today) and the optional
    letter, a slug cut at the first `-`, and 'repo unknown' for a missing or tokenless line."""
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "x-20261116", "- **Repo**: `ACME/Alpha` (Bitbucket Server)")
    write_handoff(wdir, "evidence-resolution-20260830b", "- **Repo**: acme/workflow")
    write_handoff(wdir, "old-20250101", None)
    write_handoff(wdir, "none-20250102", "- **Repo**: (nessuno - cross-repo)")
    (wdir / "handoffs" / "example-5001-reports").mkdir()          # not handoff files:
    (wdir / "handoffs" / "example-4931.patch").write_text("p")    # ignored, not errors
    found, errors = traces.handoff_traces(wdir)
    assert errors == []
    by_slug = {t.slug: t for t in found}
    assert set(by_slug) == {"x", "evidence-resolution", "old", "none"}
    assert all(t.kind == "handoff" and t.branch is None and t.text == "" for t in found)
    assert by_slug["x"].repo == "acme/alpha"
    assert by_slug["evidence-resolution"].repo == "acme/workflow"
    assert by_slug["old"].repo == "unknown" and by_slug["none"].repo == "unknown"
    assert by_slug["x"].path == str(wdir / "handoffs" / "handoff-x-20261116.md")
    assert datetime.fromtimestamp(by_slug["x"].at).date().isoformat() == "2026-11-16"
    assert datetime.fromtimestamp(by_slug["evidence-resolution"].at).date().isoformat() == "2026-08-30"


def test_handoff_traces_without_a_handoffs_dir_is_empty(tmp_path):
    wdir = make_workflow(tmp_path)
    assert traces.handoff_traces(wdir) == ([], [])


def test_an_unreadable_handoff_is_a_source_error_and_a_trace_of_repo_unknown(tmp_path):
    """A handoff that is not valid UTF-8 (and one with an impossible date) become SourceErrors
    naming the file AND a trace of repo unknown under the file-name slug (story 27); the
    readable one is returned as before. Witness: `bad` (bad bytes, valid date) and `date`
    (impossible date) - distinguishes an error with no trace (the row would be missing) from
    error + trace."""
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "good-20261116")
    bad = wdir / "handoffs" / "handoff-bad-20261116.md"
    bad.write_bytes(b"\xff\xfe not utf-8")
    impossible = wdir / "handoffs" / "handoff-date-20261399.md"
    impossible.write_text("x")
    found, errors = traces.handoff_traces(wdir)
    assert {e.source for e in errors} == {str(bad), str(impossible)}
    assert all(e.message for e in errors)
    by_slug = {t.slug: t for t in found}
    assert set(by_slug) == {"good", "bad", "date"}
    assert by_slug["good"].repo == "acme/alpha"
    assert by_slug["bad"].repo == traces.REPO_UNKNOWN and by_slug["date"].repo == traces.REPO_UNKNOWN
    assert by_slug["bad"].path == str(bad) and by_slug["date"].path == str(impossible)


@pytest.mark.parametrize("text,expected", [
    ("# t\n\n- **Repo**: acme/workflow\n", "acme/workflow"),
    ("- **Repo**: ACME/ticket-fixer (Bitbucket Server, `git.example.com`)\n", "acme/ticket-fixer"),
    ("- **Repo**: `acme/vault`\n", "acme/vault"),
    ("- **Repo**: acme/workflow + acme/kit\n",
     "acme/workflow"),
    ("- **Repo**: (nessuno - cross-repo)\n", None),
    ("# t\n\nRepo: `owner/name`\n## Repo state\n", None),
    ("- **Session**: x/y\n- **Branch**: a/b\n", None),
    ("", None),
])
def test_handoff_repo_is_the_first_owner_name_of_the_repo_line(text, expected):
    assert traces.handoff_repo(text) == expected


def test_a_handoff_joins_the_row_of_the_same_repo_and_slug_case_insensitively(tmp_path):
    """Witness: a handoff `ACME/x` and an origin `.../acme/x.git` with a claim `s`, a handoff
    of repo `acme/other` for the same slug, and one with no Repo line - distinguishes the
    case-folded exact join (handoff on the claim's row), a slug-only join (all three folded
    into one row) and a case-sensitive repo join (a separate `ACME/x` row)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "x", "https://host/scm/acme/x.git")
    write_claim(primary, "s")
    wdir = make_workflow(tmp_path)
    handoff = write_handoff(wdir, "s-20261115", "- **Repo**: ACME/x")
    write_handoff(wdir, "s-20261114", "- **Repo**: acme/other")
    write_handoff(wdir, "s-20261113", None)
    result = snap(root, workflow=wdir)
    row = row_of(result, "s", "acme/x")
    assert [t.path for t in row.traces if t.kind == "handoff"] == [str(handoff)]
    assert row.claim is not None
    assert row_of(result, "s", "acme/other").traces[0].kind == "handoff"
    assert row_of(result, "s", "unknown").traces[0].kind == "handoff"
    assert len([r for r in (*result.open, *result.history) if r.slug == "s"]) == 3


def test_a_handoff_alone_never_opens_a_row_and_lands_in_history(tmp_path):
    """Witness: a handoff for a known repo, one with no Repo line (`repo unknown`) and one for a
    repo that is not on disk - all three are history rows; the open list stays empty. Distinguishes
    'a handoff is a trace that does not open' from open-on-any-trace. A phase-less, where-less row
    must not ask git anything (the repo of `known` is real, the other two have none)."""
    root = tmp_path / "roots"
    make_repo(root, "known", "https://host/scm/acme/known.git")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "known-20261116", "- **Repo**: acme/known")
    write_handoff(wdir, "legacy-20250101", None)
    write_handoff(wdir, "elsewhere-20261116", "- **Repo**: someone/else")
    result = snap(root, workflow=wdir)
    assert result.open == () and result.partial is False
    assert {(r.repo, r.slug) for r in result.history} == {
        ("acme/known", "known"), ("unknown", "legacy"), ("someone/else", "elsewhere")}
    legacy = row_of(result, "legacy", "unknown")
    assert legacy.where.kind == "none" and legacy.phase.kind == "no-spec" and legacy.error is None
    assert row_of(result, "known").where.kind == "none"


def test_an_unreadable_handoff_makes_the_snapshot_partial_and_is_an_error_row_in_open(tmp_path):
    """Story 27 / I8. Witness: one readable handoff `ok` and one with bad bytes `bad`, plus one
    with an impossible date `date` - the snapshot is partial with one SourceError per bad file,
    and each has a row (repo unknown) in OPEN whose `error` is that SourceError's message;
    `ok` stays a history row with no error. Distinguishes an error only in the banner (no row)
    from an error row, and a history row from an open one."""
    root = tmp_path / "roots"
    make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "ok-20261116")
    bad = wdir / "handoffs" / "handoff-bad-20261116.md"
    bad.write_bytes(b"\xff\xfe")
    (wdir / "handoffs" / "handoff-date-20261399.md").write_text("x")
    result = snap(root, workflow=wdir)
    assert result.partial is True and len(result.errors) == 2
    message = {Path(e.source).name: e.message for e in result.errors}
    assert {r.slug for r in result.history} == {"ok"} and row_of(result, "ok").error is None
    assert {r.slug for r in result.open} == {"bad", "date"}
    for slug, file in (("bad", bad.name), ("date", "handoff-date-20261399.md")):
        row = row_of(result, slug, traces.REPO_UNKNOWN)
        assert row in result.open and row.error == message[file]


def test_the_wire_repo_of_a_legacy_handoff_row_is_null(tmp_path):
    """Spec X2: `repo | null`. Witness: a legacy handoff (no Repo line) next to one of repo
    `acme/known` - the first is `null` on the wire and in the detail, the second its name.
    Distinguishes the internal sentinel leaking to the wire from null."""
    root = tmp_path / "roots"
    make_repo(root, "known", "https://host/scm/acme/known.git")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "known-20261116", "- **Repo**: acme/known")
    write_handoff(wdir, "legacy-20250101", None)
    result = snap(root, workflow=wdir)
    wire = {r["slug"]: r["repo"] for r in traces.to_json(result)["history"]}
    assert wire == {"known": "acme/known", "legacy": None}
    legacy = traces.feature_detail(row_of(result, "legacy"))
    assert legacy["repo"] is None


def read_store(presence: Path, proc: Path) -> tuple:
    return harness.wf.read_presence(presence, proc)


def test_attach_agents_attaches_an_alive_record_of_the_rows_own_trees(tmp_path):
    """Witness: slug `s` exists in two repos, each with its worktree, each with its own alive
    record, plus slug `p` (claim only) recorded in its repo's primary tree - each row gets the
    record of ITS tree (pids 111, 222, 333). Distinguishes 'repo_root is one of the row's trees'
    from a slug-only match (both `s` rows would take the same record)."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    two = make_repo(root, "two", "https://host/scm/acme/two.git")
    wt1, wt2 = add_worktree(one, "s", "feature/s"), add_worktree(two, "s", "feature/s")
    write_claim(one, "p")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    alive_record(presence, proc, "a", wt1, "s", pid=111)
    alive_record(presence, proc, "b", wt2, "s", pid=222, comm="other-agent")
    alive_record(presence, proc, "c", one, "p", pid=333)
    rows = list(snap_with_other(root).open)
    by = {(r.repo, r.slug): r for r in traces.attach_agents(rows, read_store(presence, proc), proc)}
    assert by[("acme/one", "s")].agent["pid"] == 111
    assert by[("acme/one", "s")].agent["process"] == "claude"
    assert by[("acme/two", "s")].agent["pid"] == 222
    assert by[("acme/two", "s")].agent["process"] == "other-agent"
    assert by[("acme/one", "p")].agent["pid"] == 333


def test_a_process_is_active_only_on_its_newest_record(tmp_path):
    """Witness: pid 111 has two records, `old` (10:00, tree of `a`) and `new` (11:00, tree of
    `b`); a second process, pid 222, also records `a` (10:30) - `b` takes pid 111, `a` takes
    pid 222 (so a feature stays active through another process) and pid 111 is not on `a`.
    Distinguishes 'one process, one active feature' from 'every alive record attaches'."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    wta, wtb = add_worktree(one, "a", "feature/a"), add_worktree(one, "b", "feature/b")
    c = add_worktree(one, "c", "feature/c")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    harness.fake_proc(proc, 111, "claude", 1, 360000)
    harness.write_record(presence, "old", slug="a", repo_root=str(wta), pid=111, process="claude",
                         start_ticks=360000, recorded_at="2026-11-16T10:00:00+01:00")
    harness.write_record(presence, "new", slug="b", repo_root=str(wtb), pid=111, process="claude",
                         start_ticks=360000, recorded_at="2026-11-16T11:00:00+01:00")
    harness.fake_proc(proc, 222, "claude", 1, 360000)
    harness.write_record(presence, "other", slug="a", repo_root=str(wta), pid=222, process="claude",
                         start_ticks=360000, recorded_at="2026-11-16T10:30:00+01:00")
    harness.write_record(presence, "solo", slug="c", repo_root=str(c), pid=111, process="claude",
                         start_ticks=360000, recorded_at="2026-11-16T09:00:00+01:00")
    rows = list(snap_with_other(root).open)
    by = {r.slug: r for r in traces.attach_agents(rows, read_store(presence, proc), proc)}
    assert by["b"].agent["pid"] == 111
    assert by["a"].agent["pid"] == 222
    assert by["c"].agent is None


def test_the_row_a_process_left_goes_back_to_history(tmp_path):
    """Witness: delivered spec `a` (primary, no worktree, no claim) whose only live trace is
    pid 111's older record; the same process's newer record is on `b`. `a` is not open; with
    the newer record on `a` itself it is. Distinguishes 'one process, one active feature' from
    'every alive record keeps its row open'."""
    root = tmp_path / "roots"
    primary = make_repo(root, "one", "https://host/scm/acme/one.git")
    write_spec(primary, "a", delivered_spec("a", "feature/a"))
    commit(primary, "specs")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    harness.fake_proc(proc, 111, "claude", 1, 360000)
    harness.write_record(presence, "old", slug="a", repo_root=str(primary), pid=111,
                         process="claude", start_ticks=360000, recorded_at="2026-11-16T10:00:00+01:00")
    harness.write_record(presence, "new", slug="b", repo_root=str(primary), pid=111,
                         process="claude", start_ticks=360000, recorded_at="2026-11-16T11:00:00+01:00")
    row = next(r for r in snap(root).history if r.slug == "a")
    left = traces.attach_agents([row], read_store(presence, proc), proc)[0]
    assert left.agent is None and not traces.is_open(left)
    harness.write_record(presence, "new", slug="a", repo_root=str(primary), pid=111,
                         process="claude", start_ticks=360000, recorded_at="2026-11-16T11:00:00+01:00")
    kept = traces.attach_agents([row], read_store(presence, proc), proc)[0]
    assert kept.agent["pid"] == 111 and traces.is_open(kept)


def test_a_record_for_the_same_slug_in_another_repos_tree_does_not_attach(tmp_path):
    """Witness: the only record is for slug `s` in repo two's worktree; the row `s` of repo one
    stays without agent. Distinguishes the repo_root check from matching on the slug alone."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    two = make_repo(root, "two", "https://host/scm/acme/two.git")
    add_worktree(one, "s", "feature/s")
    wt2 = add_worktree(two, "s", "feature/s")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    alive_record(presence, proc, "b", wt2, "s", pid=222)
    rows = traces.attach_agents(list(snap_with_other(root).open), read_store(presence, proc), proc)
    by = {r.repo: r for r in rows}
    assert by["acme/one"].agent is None
    assert by["acme/two"].agent["pid"] == 222


def test_a_dead_record_does_not_attach(tmp_path):
    """Witness: a record whose pid does not exist (alive False) on the row's own tree."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    wt = add_worktree(one, "s", "feature/s")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    harness.write_record(presence, "dead", slug="s", repo_root=str(wt), pid=999)
    rows = list(snap_with_other(root).open)
    attached = traces.attach_agents(rows, read_store(presence, proc), proc)
    assert [r.agent for r in attached] == [None]


def test_since_is_btime_plus_start_ticks_over_clk_tck(tmp_path):
    """Witness: btime 1_700_000_000 and start ticks 360000 -> started btime + 360000/CLK_TCK s.
    Distinguishes the formula from the record's `recorded_at` or the raw tick count."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    wt = add_worktree(one, "s", "feature/s")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc, btime=1_700_000_000)
    alive_record(presence, proc, "a", wt, "s", ticks=360000)
    row = row_of(snap_with_other(root, presence=presence), "s")
    # the injected proc root is the app's only /proc reader here: snapshot reads the real one,
    # so the formula is pinned through attach_agents with the fake root
    rows = traces.attach_agents(list(snap_with_other(root).open), read_store(presence, proc), proc)
    expected = 1_700_000_000 + 360000 / os.sysconf("SC_CLK_TCK")
    assert datetime.fromisoformat(rows[0].agent["since"]).timestamp() == pytest.approx(expected)
    assert row.slug == "s"


def test_an_unreadable_proc_stat_leaves_the_agent_with_no_since(tmp_path):
    """/proc/stat unreadable: the agent is still attached (pid, process), since None, no raise."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    wt = add_worktree(one, "s", "feature/s")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    alive_record(presence, proc, "a", wt, "s")          # no <proc>/stat file
    rows = traces.attach_agents(list(snap_with_other(root).open), read_store(presence, proc), proc)
    assert rows[0].agent == {"pid": 4242, "process": "claude", "since": None}


def test_a_malformed_presence_record_is_a_source_error_and_the_snapshot_partial(tmp_path):
    """Witness: one malformed record in the store: SourceError naming the file, partial, no
    agent on any row, the rest of the rows intact."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    write_claim(one, "p")
    presence = tmp_path / "presence"
    presence.mkdir()
    (presence / "bad.json").write_text("{not json")
    result = snap(root, presence=presence)
    assert result.partial is True
    assert [e.source for e in result.errors] == [str(presence / "bad.json")]
    assert "malformed presence record" in result.errors[0].message
    assert row_of(result, "p").agent is None


def test_a_missing_presence_store_leaves_every_agent_none(tmp_path):
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    write_claim(one, "p")
    result = snap(root, presence=tmp_path / "no-such-store")
    assert result.partial is False and row_of(result, "p").agent is None


def test_an_attached_agent_adds_a_presence_trace_and_serialises(tmp_path):
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    wt = add_worktree(one, "s", "feature/s")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    alive_record(presence, proc, "a", wt, "s")
    rows = traces.attach_agents(list(snap_with_other(root).open), read_store(presence, proc), proc)
    assert [t.kind for t in rows[0].traces if t.kind == "presence"] == ["presence"]
    wire = json.loads(json.dumps(traces.row_json(rows[0])))
    assert wire["agent"]["pid"] == 4242 and "since" in wire["agent"]
    assert "presence" in {t["kind"] for t in wire["traces"]}


# --- signals ----------------------------------------------------------------

def worktree_rows(tmp_path: Path, with_base: bool = True):
    """Rows over a repo with an origin whose origin/HEAD is (or is not) set, to vary one fact
    at a time: base `master` is `origin/master` in a bare clone."""
    root = tmp_path / "roots"
    if with_base:
        bare = tmp_path / "origin.git"
        source = make_repo(tmp_path / "seed", "seed")
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)], check=True,
                       env=GIT_ENV, capture_output=True)
        root.mkdir()
        subprocess.run(["git", "clone", "-q", str(bare), str(root / "alpha")], check=True,
                       env=GIT_ENV, capture_output=True)
        primary = root / "alpha"
        git(primary, "remote", "set-url", "origin", "https://host/scm/acme/alpha.git")
    else:
        primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    return root, primary


def removable_names(result) -> dict[str, list[str]]:
    return {r.slug: [s["name"] for s in r.signals] for r in (*result.open, *result.history)}


def test_removable_needs_all_three_conditions(tmp_path):
    """Witness: five worktree rows over a repo with a known base - `ok` (clean, reachable, no
    claim), `dirty` (untracked file), `ahead` (one commit past the base), `claimed` (clean and
    reachable but with a claim) - and the same clean worktree in a repo with NO base. Only `ok`
    is removable; the base-less one carries `base-unknown` and not `removable`. Each condition
    is removed alone, so dropping any one of the three conjuncts (or the base check) fails."""
    root, primary = worktree_rows(tmp_path)
    add_worktree(primary, "ok", "feature/ok")
    dirty = add_worktree(primary, "dirty", "feature/dirty")
    (dirty / "x.txt").write_text("x")
    ahead = add_worktree(primary, "ahead", "feature/ahead")
    (ahead / "a.txt").write_text("a")
    commit(ahead, "ahead")
    add_worktree(primary, "claimed", "feature/claimed")
    write_claim(primary, "claimed")
    names = removable_names(snap_with_other(root))
    assert names["ok"] == ["removable"]
    assert names["dirty"] == [] and names["ahead"] == []
    assert names["claimed"] == ["claim-no-agent"]
    nobase_root = tmp_path / "nobase"
    nobase = make_repo(nobase_root, "beta", "https://host/scm/acme/beta.git")
    add_worktree(nobase, "clean", "feature/clean")
    assert removable_names(snap_with_other(nobase_root))["clean"] == ["base-unknown"]


def test_removable_is_not_claimed_for_a_row_without_a_worktree(tmp_path):
    """A claim-only row and a spec-only row sit on the primary tree: never `removable`."""
    root, primary = worktree_rows(tmp_path)
    write_claim(primary, "just-claim")
    write_spec(primary, "spec-only", delivered_spec("spec-only", "feature/so"))
    commit(primary, "spec")
    names = removable_names(snap(root))
    assert "removable" not in names["just-claim"] + names["spec-only"]
    assert "base-unknown" not in names["just-claim"] + names["spec-only"]


def test_claim_no_agent_disappears_when_an_alive_record_exists(tmp_path):
    """Witness: the same claim row, first without a store then with an alive record on the
    primary tree - distinguishes 'claim and no alive presence' from 'claim' alone."""
    root = tmp_path / "roots"
    one = make_repo(root, "one", "https://host/scm/acme/one.git")
    write_claim(one, "p")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    assert removable_names(snap(root))["p"] == ["claim-no-agent"]
    alive_record(presence, proc, "a", one, "p")
    rows = traces.attach_agents(list(snap(root).open), read_store(presence, proc), proc)
    assert traces.row_signals(rows[0]) == ()


def test_phase_fail_follows_a_failing_verdict(tmp_path):
    """Witness: an unapproved spec derives `fail` at phase 0 (no approval line) while an
    approved one only has a todo verdict - distinguishes 'any verdict fail' from 'phase derived'."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "failing", undelivered_spec("failing", "feature/f"))
    write_spec(primary, "fine", undelivered_spec("fine", "feature/g", approved=True))
    commit(primary, "specs")
    result = snap(root)
    failing, fine = row_of(result, "failing"), row_of(result, "fine")
    assert any(v["status"] == "fail" for v in failing.phase.verdicts)
    assert not any(v["status"] == "fail" for v in fine.phase.verdicts)
    names = removable_names(result)
    assert "phase-fail" in names["failing"] and "phase-fail" not in names["fine"]


LIVE_AGENT = {"pid": 1, "process": "claude", "since": None}


def phase_row(kind, statuses, agent, where_kind="primary", base="master", claim=None, row_traces=()):
    """A Row built in memory: only the fields `row_signals` reads vary (#96, #94)."""
    where = traces.Where(where_kind, "/t", "feature/x", 0, 0, base)
    phase = traces.PhaseView(kind, 6, tuple({"status": s} for s in statuses))
    return traces.Row("i", "r", "s", 0.0, phase, where, claim, agent, (), row_traces, None)


def signal_names(row):
    return [s["name"] for s in traces.row_signals(row)]


def test_a_fail_verdict_without_an_agent_raises_phase_fail():
    assert "phase-fail" in signal_names(phase_row("derived", ["fail"], None))


def test_a_fail_verdict_with_a_live_agent_does_not_raise_phase_fail():
    assert "phase-fail" in signal_names(phase_row("derived", ["fail"], None))
    assert "phase-fail" not in signal_names(phase_row("derived", ["fail"], LIVE_AGENT))


def test_no_fail_verdict_never_raises_phase_fail_with_or_without_an_agent():
    for statuses in (["todo"], []):
        for agent in (None, LIVE_AGENT):
            assert "phase-fail" not in signal_names(phase_row("derived", statuses, agent))


def test_a_delivered_row_never_shows_phase_fail():
    for agent in (None, LIVE_AGENT):
        assert "phase-fail" not in signal_names(phase_row("delivered", ["pass", "skip"], agent))


def test_the_other_signals_are_the_same_with_or_without_an_agent_on_a_failing_row():
    bare = signal_names(phase_row("derived", ["fail"], None, where_kind="worktree", base=None))
    live = signal_names(phase_row("derived", ["fail"], LIVE_AGENT, where_kind="worktree", base=None))
    assert "base-unknown" in live
    assert [n for n in bare if n != "phase-fail"] == live


def test_a_claim_without_an_agent_still_raises_claim_no_agent_next_to_phase_fail():
    names = signal_names(phase_row("derived", ["fail"], None, claim={"branch": "b"}))
    assert "claim-no-agent" in names and "phase-fail" in names


HANDOFF = traces.Trace("handoff", "r", "s", "/h", None, 0.0, "")


def test_a_delivered_row_without_a_handoff_and_without_an_agent_raises_handoff_missing():
    assert "handoff-missing" in signal_names(phase_row("delivered", [], None))


def test_a_delivered_row_without_a_handoff_but_with_a_live_agent_does_not_raise_handoff_missing():
    assert "handoff-missing" in signal_names(phase_row("delivered", [], None))
    assert "handoff-missing" not in signal_names(phase_row("delivered", [], LIVE_AGENT))


def test_a_delivered_row_with_a_handoff_never_raises_handoff_missing():
    for agent in (None, LIVE_AGENT):
        assert "handoff-missing" not in signal_names(phase_row("delivered", [], agent, row_traces=(HANDOFF,)))


def test_a_row_that_is_not_delivered_never_raises_handoff_missing():
    for kind in ("derived", "no-spec"):
        for agent in (None, LIVE_AGENT):
            assert "handoff-missing" not in signal_names(phase_row(kind, [], agent))


def test_the_other_signals_are_the_same_with_or_without_an_agent_on_a_delivered_row_without_a_handoff():
    bare = signal_names(phase_row("delivered", [], None, where_kind="worktree", base=None))
    live = signal_names(phase_row("delivered", [], LIVE_AGENT, where_kind="worktree", base=None))
    assert "base-unknown" in live and "base-unknown" in bare
    assert [n for n in bare if n != "handoff-missing"] == live


def test_handoff_missing_only_for_a_delivered_spec_without_a_handoff(tmp_path):
    """Witness: delivered `a` without a handoff (flag), delivered `b` with one (none), an
    undelivered `c` without one (none), and a claim-only row (none)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "a", delivered_spec("a", "feature/a"))
    write_spec(primary, "b", delivered_spec("b", "feature/b"))
    write_spec(primary, "c", undelivered_spec("c", "feature/c", approved=True))
    commit(primary, "specs")
    write_claim(primary, "d")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "b-20261116", "- **Repo**: acme/alpha")
    names = removable_names(snap(root, workflow=wdir))
    assert names["a"] == ["handoff-missing"]
    assert names["b"] == []
    assert "handoff-missing" not in names["c"] + names["d"]


def all_five_signals(tmp_path):
    """One snapshot holding removable, base-unknown, claim-no-agent, phase-fail, handoff-missing."""
    root, primary = worktree_rows(tmp_path)
    add_worktree(primary, "wt", "feature/wt")                         # removable
    write_claim(primary, "cl")                                        # claim-no-agent
    write_spec(primary, "bad", undelivered_spec("bad", "feature/bad"))  # phase-fail
    write_spec(primary, "done", delivered_spec("done", "feature/done"))  # handoff-missing
    commit(primary, "specs")
    nobase_root = tmp_path / "nobase"
    nobase = make_repo(nobase_root, "beta", "https://host/scm/acme/beta.git")
    add_worktree(nobase, "nb", "feature/nb")                          # base-unknown
    result = snap_with_other(root, nobase_root)
    return [s for r in (*result.open, *result.history) for s in r.signals]


# Decision 7: the rule each signal cites, by name (a swap between two allowed rules is caught).
RULE_OF = {
    "removable": "SESSION-ISOLATION.md § Cleanup at session end",
    "base-unknown": "SESSION-ISOLATION.md § Cleanup at session end (no base to prove reachability)",
    "claim-no-agent": "SESSION-ISOLATION.md § Claims",
    "phase-fail": "wf.py status",
    "handoff-missing": "WORKFLOW.md § General rules (I5: a handoff per session; wf.py end)",
}


def test_every_signal_cites_a_known_rule(tmp_path):
    """Witness `phase-fail` carrying "wf.py status" - distinguishes the rule of its own name
    from another allowed rule (a swap between two signals), which membership in a list lets
    through, and from an empty, invented or reworded rule."""
    produced = all_five_signals(tmp_path)
    assert {s["name"] for s in produced} == set(RULE_OF)
    for signal in produced:
        assert signal["rule"] == RULE_OF[signal["name"]], signal
    assert all(set(s) == {"name", "rule", "label", "path"} for s in produced)


def test_each_signal_label_is_the_mockup_wording(tmp_path):
    """Owner 2026-11-16 "Etichetta dal server". Witness: all five signals - the label of each
    name is the literal text of the approved mockup (`phase failing` and `handoff missing`
    are not in the mockup: the owner's list names the signals, the wording follows the
    others). Distinguishes the raw id (`claim-no-agent`) from the human label."""
    labels = {s["name"]: s["label"] for s in all_five_signals(tmp_path)}
    assert labels == {
        "removable": "removable",
        "base-unknown": "base unknown",
        "claim-no-agent": "claim, no agent recorded",
        "phase-fail": "phase failing",
        "handoff-missing": "handoff missing",
    }


def test_row_signals_is_total_over_a_row_with_nothing_known():
    row = traces.Row("i", "r", "s", 0.0, traces.PhaseView("no-spec", None, ()),
                     traces.Where("none", None, None, None, None, None), None, None, (), (), None)
    assert traces.row_signals(row) == ()


def test_row_signals_marks_a_worktree_removable_only_when_its_base_and_reachability_are_both_known():
    """Witness `base=None, ahead=0` - distinguishes `base-unknown` and not removable from removable.
    Witness `base="origin/master", ahead=None` - distinguishes not removable (unknown reachability
    is not reachability) from removable.
    Witness `base="origin/master", ahead=0` - the positive control: removable."""
    def names(ahead, base):
        where = traces.Where("worktree", "/w", "b", 0, ahead, base)
        row = traces.Row("i", "r", "s", 0.0, traces.PhaseView("no-spec", None, ()),
                         where, None, None, (), (), None)
        return [s["name"] for s in traces.row_signals(row)]

    assert "removable" in names(0, "origin/master")
    no_base = names(0, None)
    assert "removable" not in no_base and "base-unknown" in no_base
    assert "removable" not in names(None, "origin/master")


AGENT = {"pid": 4242, "process": "claude", "since": None}


def removable_row(agent, claim=None, dirty=0, ahead=0, base="origin/master"):
    """A row whose own worktree and one other tree are both otherwise removable (#92)."""
    own = traces.Where("worktree", "/w/own", "feature/x", dirty, ahead, base)
    other = traces.Where("worktree", "/w/other", "feature/x", 0, 0, "origin/master")
    row = traces.Row("i", "r", "s", 0.0, traces.PhaseView("no-spec", None, ()),
                     own, claim, agent, (), (), None, trees=(other,))
    return row, own, other


def test_a_row_with_a_live_agent_is_never_removable_for_its_own_worktree():
    row, own, _ = removable_row(AGENT)
    assert traces.removable(row, own) is False


def test_a_row_with_a_live_agent_is_not_removable_for_another_tree_of_the_same_row():
    row, _, other = removable_row(AGENT)
    assert traces.removable(row, other) is False


def test_the_same_row_without_an_agent_is_removable():
    row, own, other = removable_row(None)
    assert traces.removable(row, own) is True
    assert traces.removable(row, other) is True


def test_a_claim_still_blocks_removable_without_an_agent():
    row, own, _ = removable_row(None, claim={"session": "s"})
    assert traces.removable(row, own) is False


def test_a_dirty_tree_still_blocks_removable_without_an_agent():
    row, own, _ = removable_row(None, dirty=1)
    assert traces.removable(row, own) is False


def test_a_tree_ahead_of_the_base_still_blocks_removable_without_an_agent():
    row, own, _ = removable_row(None, ahead=1)
    assert traces.removable(row, own) is False


def test_a_tree_with_an_unknown_base_still_blocks_removable_without_an_agent():
    row, own, _ = removable_row(None, base=None)
    assert traces.removable(row, own) is False


def test_row_signals_has_no_removable_for_a_row_with_a_live_agent():
    def removable_paths(agent):
        row = removable_row(agent)[0]
        return [s["path"] for s in traces.row_signals(row) if s["name"] == "removable"]

    assert removable_paths(None) == ["/w/own", "/w/other"]
    assert removable_paths(AGENT) == []


def test_a_live_agent_suppresses_only_removable_and_leaves_the_other_signals():
    """witness a row whose own tree has no base: `base-unknown` is its own signal, `removable` comes from the
    other tree - distinguishes suppressing `removable` from suppressing every signal of the row."""
    def names(agent):
        return [s["name"] for s in traces.row_signals(removable_row(agent, base=None)[0])]

    assert names(None) == ["base-unknown", "removable"]
    assert names(AGENT) == ["base-unknown"]


# --- detail -----------------------------------------------------------------

NEXT = "- Desktop app — #21.\n- Kit export — #20.\n\n  indented follow-up\n- Follow-up: none other."


def test_handoff_next_returns_the_section_up_to_the_next_heading(tmp_path):
    """Witness: a real-shaped handoff with `## Next session` between `## Done` and
    `## Ground truth`, including a blank line and an indented line - the text is the section
    body, trimmed, without the heading and without the following section."""
    wdir = make_workflow(tmp_path)
    path = write_handoff(wdir, "x-20261116", next_session=NEXT)
    assert traces.handoff_next(path) == NEXT
    last = wdir / "handoffs" / "handoff-last-20261116.md"
    last.write_text("# h\n\n## Next session\n\n- only\n")
    assert traces.handoff_next(last) == "- only"


def test_handoff_next_is_none_without_the_section(tmp_path):
    wdir = make_workflow(tmp_path)
    assert traces.handoff_next(write_handoff(wdir, "y-20261116")) is None
    empty = wdir / "handoffs" / "handoff-empty-20261116.md"
    empty.write_text("# h\n\n## Next session\n\n## Ground truth\n")
    assert traces.handoff_next(empty) is None


def test_handoff_next_raises_oserror_when_the_file_vanished(tmp_path):
    with pytest.raises(OSError):
        traces.handoff_next(tmp_path / "gone.md")


def a_store(tmp_path: Path, *, reviews=(), briefs=(), matrices=(), manifests=()) -> Path:
    """A store under tmp_path: `reviews` (repo, feature, report), `briefs` (repo, feature),
    `matrices` and `manifests` names."""
    store = tmp_path / "s" / "reports.sqlite"
    conn = report_store.open_store(store)
    for repo, feature, report in reviews:
        row = {"repo": repo, "criterion": "c", "item_id": "1", "verdict": "pass", "severity": "fatal",
               "reviewer": "spec", "actor": "spec-1", "claim": "coverage"}
        report_store.add_review_rows(conn, [row], feature, report, tmp_path, STORE_NOW)
    for repo, feature in briefs:
        report_store.add_brief_items(conn, repo, feature, ["1"], STORE_NOW)
    conn.close()
    for name in matrices:
        stored_matrix(store, name)
    for name in manifests:
        stored_manifest(store, name)
    return store


def test_stored_reports_attribute_by_longest_prefix_repo_and_brief(tmp_path):
    """Witness: with known slugs {phase, phase-harness} the store holds a review report of
    `ACME/X` (case-folded) and one of `someone/else` for `phase-harness`, the brief of
    `phase-harness`, a matrix `phase-mutation` and one `phase-harness-mutation`. `phase` gets its
    matrix only (the longer slug owns the other), `phase-harness` its repo's review report and its
    brief but no matrix (known in two repos), the other repo its own report and no brief -
    distinguishes longest-prefix from every-prefix, and the repo check from a slug-only attribution."""
    store = a_store(tmp_path,
                    reviews=[("ACME/X", "phase-harness", "phase-harness-spec-r2"),
                             ("someone/else", "phase-harness", "phase-harness-other")],
                    briefs=[("acme/x", "phase-harness")],
                    matrices=["phase-mutation", "phase-harness-mutation"])
    known = {"phase": frozenset({"acme/x"}),
             "phase-harness": frozenset({"acme/x", "someone/else"})}
    assert traces.stored_reports(store, "phase", "acme/x", known) == [
        ("phase-mutation.jsonl", ("matrix", "phase-mutation"))]
    assert traces.stored_reports(store, "phase-harness", "acme/x", known) == [
        ("phase-harness-spec-r2.jsonl", ("review", "acme/x", "phase-harness", "phase-harness-spec-r2")),
        ("phase-harness-brief.json", ("brief", "acme/x", "phase-harness"))]
    assert traces.stored_reports(store, "phase-harness", "someone/else", known) == [
        ("phase-harness-other.jsonl", ("review", "someone/else", "phase-harness", "phase-harness-other"))]
    assert traces.stored_reports(store, "nothing", "acme/x", known) == []


def test_stored_matrices_go_by_slug_only_when_it_is_known_in_one_repo(tmp_path):
    """Owner 2026-11-16 "Senza repo: basta lo slug". Witness: `solo` known in one repo, `twin`
    known in two; matrices `solo-mutation-1` and `twin-mutation-1` (a matrix has no repo) and a
    review report `solo-other` of `someone/else` - the first is listed under `solo`, the second
    under neither `twin` row, the review report under none of acme/x. Distinguishes slug-only
    attribution (all listed), the old rule (none listed) and 'slug unique' from 'has no repo'."""
    store = a_store(tmp_path, reviews=[("someone/else", "solo", "solo-other")],
                    matrices=["solo-mutation-1", "twin-mutation-1"])
    known = {"solo": frozenset({"acme/x"}), "twin": frozenset({"acme/x", "acme/y"})}
    assert traces.stored_reports(store, "solo", "acme/x", known) == [
        ("solo-mutation-1.jsonl", ("matrix", "solo-mutation-1"))]
    assert traces.stored_reports(store, "twin", "acme/x", known) == []
    assert traces.stored_reports(store, "twin", "acme/y", known) == []
    assert traces.stored_reports(store, "solo", "acme/y", known) == []   # the slug is not known there


def test_the_window_lists_only_stored_reports_and_the_brief(tmp_path):
    """Witness: a report file `reports/s-spec-r1.jsonl` in the install and, in the store, a review
    report `s-spec-r2`, a matrix `s-mutation-1` and the brief of `s` - the detail and the
    documents list the three stored ones and not the file; the brief is `s-brief.json` and its body
    is `brief_text` (one line and a newline). Distinguishes a window that still lists the files
    from one that lists only the store, and a brief that is not shown from one that is."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_claim(primary, "s")
    wdir = make_workflow(tmp_path)
    (wdir / "reports").mkdir(exist_ok=True)
    (wdir / "reports" / "s-spec-r1.jsonl").write_text(
        json.dumps({"criterion": "spec-a", "verdict": "pass", "repo": "acme/alpha"}) + "\n")
    store = a_store(tmp_path, reviews=[("acme/alpha", "s", "s-spec-r2")],
                    briefs=[("acme/alpha", "s")], matrices=["s-mutation-1"])
    result = snap(root, workflow=wdir)
    row = row_of(result, "s")
    kept = traces.stored_reports(store, "s", "acme/alpha", traces.slug_repos((*result.open, *result.history)))
    names = ["s-spec-r2.jsonl", "s-brief.json", "s-mutation-1.jsonl"]
    assert [name for name, _ in kept] == names
    assert [r["name"] for r in traces.feature_detail(row, kept)["reports"]] == names
    docs = [d for d in catalog.row_documents(row, kept, store) if d.kind == "report"]
    assert [d.name for d in docs] == names
    brief = next(d for d in docs if d.name == "s-brief.json")
    conn = report_store.open_store(store)
    try:
        want = report_store.brief_text(conn, "acme/alpha", "s")
    finally:
        conn.close()
    assert want == '{"repo": "acme/alpha", "feature": "s", "items": ["1"]}'
    assert catalog.document_body(brief, store) == (want + "\n").encode()


def test_stored_reports_raise_when_the_store_is_not_a_database(tmp_path):
    """Spec E: an unreadable store is not a silent empty list. Witness: a file that is not SQLite -
    an error (the server's 503), where a missing store gives []."""
    store = tmp_path / "reports.sqlite"
    store.write_text("this is not a database, just text that is long enough " * 20)
    with pytest.raises(sqlite3.Error):
        traces.stored_reports(store, "s", "acme/x", {"s": frozenset({"acme/x"})})



def test_feature_detail_carries_next_session_reports_and_the_row(tmp_path):
    """Witness: a row with two handoffs (an older one with a Next session text, a newer
    lettered one with another) and two stored reports - the detail takes the NEWEST handoff's
    section (the letter breaks the same-day tie), lists the stored report names in the order given,
    and is the row's wire form plus the three keys; a row without handoff has `handoff_next` null."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_claim(primary, "s")
    write_claim(primary, "bare")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "s-20261115", next_session="old text")
    write_handoff(wdir, "s-20261116", next_session="same day")
    write_handoff(wdir, "s-20261116b", next_session="new text")
    result = snap(root, workflow=wdir)
    row = row_of(result, "s")
    stored = [("s-mutation-1.jsonl", ("matrix", "s-mutation-1")),
              ("s-spec-r1.jsonl", ("review", "acme/alpha", "s", "s-spec-r1"))]
    detail = traces.feature_detail(row, stored)
    assert detail["handoff_next"] == "new text"
    assert [r["name"] for r in detail["reports"]] == ["s-mutation-1.jsonl", "s-spec-r1.jsonl"]
    assert detail["documents"] == []
    assert {k: v for k, v in detail.items() if k not in ("handoff_next", "reports", "documents")} \
        == traces.row_json(row)
    bare = traces.feature_detail(row_of(result, "bare"))
    assert bare["handoff_next"] is None and bare["reports"] == []
    assert json.loads(json.dumps(detail)) == detail


def test_the_unknown_pseudo_repo_is_not_a_repo_of_the_slug(tmp_path):
    """Spec 25-X5. Witness `foo` in {acme/alpha, unknown} read as {acme/alpha} - distinguishes
    "known in exactly one repo" from counting the pseudo-repo of a legacy handoff as a second
    repo, which would list the repo-less matrix under no row."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    add_worktree(primary, "foo", "feature/foo")
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "foo-20261115", repo_line=None)
    store = a_store(tmp_path, matrices=["foo-mutation-1"])
    result = snap_with_other(root, workflow=wdir)
    rows = (*result.open, *result.history)
    assert {r.repo for r in rows if r.slug == "foo"} == {"acme/alpha", traces.REPO_UNKNOWN}
    known = traces.slug_repos(rows)
    assert known["foo"] == frozenset({"acme/alpha"})
    assert traces.stored_reports(store, "foo", "acme/alpha", known) == [
        ("foo-mutation-1.jsonl", ("matrix", "foo-mutation-1"))]


def test_a_slug_known_only_through_legacy_handoffs_owns_no_repoless_report(tmp_path):
    """Spec 25-X6. Witness `legacy` mapped to {} with `detail.reports == []` - distinguishes
    "an unknown repo is not a repo" from attributing the repo-less matrix to the slug's only row."""
    wdir = make_workflow(tmp_path)
    write_handoff(wdir, "legacy-20250101", None)
    store = a_store(tmp_path, matrices=["legacy-mutation-1"])
    result = snap(tmp_path / "roots", workflow=wdir)
    rows = (*result.open, *result.history)
    known = traces.slug_repos(rows)
    assert known["legacy"] == frozenset()
    kept = traces.stored_reports(store, "legacy", traces.REPO_UNKNOWN, known)
    detail = traces.feature_detail(row_of(result, "legacy"), kept)
    assert detail["reports"] == []


def test_feature_detail_raises_oserror_when_the_newest_handoff_vanished(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_claim(primary, "s")
    wdir = make_workflow(tmp_path)
    path = write_handoff(wdir, "s-20261116", next_session="x")
    row = row_of(snap(root, workflow=wdir), "s")
    path.unlink()
    with pytest.raises(OSError):
        traces.feature_detail(row)


# --- #35: a worktree belongs to its session's feature -----------------------

def record(presence: Path, name: str, tree: Path, slug: str, at: str) -> None:
    """A presence record (dead pid by default) of `slug` for `tree`, written `at`."""
    harness.write_record(presence, name, slug=slug, repo_root=str(tree), recorded_at=at)


def test_worktree_slug_is_its_last_presence(tmp_path):
    """Witness: the tree `alpha-f-25` has two records, the NEWER one in the file that sorts
    FIRST (`a`: slug `f` at 11:00; `z`: slug `old` at 09:00), both dead, plus a record for a
    removed tree - distinguishes 'newest recorded_at wins' from 'last file read wins' (row
    `old`), from the directory slug (row `f-25`), from 'only a live record binds' (row `f-25`)
    and from a removed tree's record adding a row (`ghost`)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f-25", "feature/f-25")
    gone = add_worktree(primary, "ghost-7", "feature/ghost-7")
    git(primary, "worktree", "remove", "--force", str(gone))
    presence = tmp_path / "presence"
    record(presence, "a", wt, "f", "2026-11-16T11:00:00+01:00")
    record(presence, "z", wt, "old", "2026-11-16T09:00:00+01:00")
    record(presence, "g", gone, "ghost", "2026-11-16T12:00:00+01:00")
    result = snap_with_other(root, presence=presence)
    assert keys(result.open + result.history) == {("acme/alpha", "f")}
    assert result.partial is False
    row = row_of(result, "f")
    assert [t.slug for t in row.traces if t.kind == "worktree"] == ["f"]
    assert row.where.path == str(wt)


def test_a_worktree_without_presence_takes_the_feature_of_its_branch_spec(tmp_path):
    """Witness: worktree `alpha-wt-9` on `feature/feat` holding the spec `feat` whose `branch:`
    is that branch, no presence - distinguishes the feature `feat` (spec by branch) from the
    directory slug `wt-9`, which would make two rows."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "wt-9", "feature/feat")
    write_spec(wt, "feat", undelivered_spec("feat", "feature/feat"))
    commit(wt, "spec")
    result = snap(root)
    assert keys(result.open + result.history) == {("acme/alpha", "feat")}
    assert row_of(result, "feat").where.path == str(wt)


def test_phase_comes_from_the_most_recent_presence_tree(tmp_path):
    """Witness: feature `f` has two bound worktrees whose branches are not the spec's
    `branch:`, with three different copies - the primary's at phase 0, `f-1`'s approved
    (phase 1), `f-2`'s delivered - and the bindings swap which is newer. Distinguishes 'derive
    on the newest binding's tree' (delivered / phase 1) from the primary (phase 0), from the
    first worktree by name (phase 1 both times), and from a branch match (none matches)."""
    for newer, expected_kind, expected_phase in (("f-2", "delivered", None), ("f-1", "derived", 1)):
        root = tmp_path / f"roots-{newer}"
        primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
        write_spec(primary, "f", undelivered_spec("f", "feature/f"))
        commit(primary, "specs")
        one = add_worktree(primary, "f-1", "feature/f-1")
        write_spec(one, "f", undelivered_spec("f", "feature/f", approved=True))
        commit(one, "approve")
        two = add_worktree(primary, "f-2", "feature/f-2")
        write_spec(two, "f", delivered_spec("f", "feature/f"))
        commit(two, "deliver")
        presence = tmp_path / f"presence-{newer}"
        old, new = ("f-1", one), ("f-2", two)
        if newer == "f-1":
            old, new = new, old
        record(presence, "old", old[1], "f", "2026-11-16T09:00:00+01:00")
        record(presence, "new", new[1], "f", "2026-11-16T11:00:00+01:00")
        result = snap(root, presence=presence)
        assert keys(result.open + result.history) == {("acme/alpha", "f")}, newer
        row = row_of(result, "f")
        assert (row.phase.kind, row.phase.phase) == (expected_kind, expected_phase), newer
        assert row.where.path == str(new[1]), newer


def test_a_malformed_presence_store_binds_nothing(tmp_path):
    """Witness: a valid, alive record binds `alpha-f-25` to `f`, but a second file is garbage -
    distinguishes 'no binding at all' (row `f-25`, the directory slug, no agent) from skipping
    only the bad file (row `f`, with its agent)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f-25", "feature/f-25")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    alive_record(presence, proc, "good", wt, "f")
    (presence / "bad.json").write_text("{not json")
    result = snap_with_other(root, presence=presence)
    assert keys(result.open + result.history) == {("acme/alpha", "f-25")}
    assert [e.source for e in result.errors] == [str(presence / "bad.json")]
    assert result.partial is True
    assert row_of(result, "f-25").agent is None


def test_every_worktree_of_a_row_keeps_its_removable(tmp_path):
    """Witness: feature `f` bound to two worktrees, the newer one dirty (the row's own tree, not
    removable) and the older clean and reachable - distinguishes a `removable` for the OLDER
    tree with its own path from none (only the row's tree is read). Then the older one dirty:
    none. Then the older one's directory deleted: the git failure is on Row.error and in the
    errors, and the row keeps its own `where` (the newer tree)."""
    root, primary = worktree_rows(tmp_path)
    old = add_worktree(primary, "f-1", "feature/f-1")
    new = add_worktree(primary, "f-2", "feature/f-2")
    (new / "wip.txt").write_text("wip")
    presence = tmp_path / "presence"
    record(presence, "old", old, "f", "2026-11-16T09:00:00+01:00")
    record(presence, "new", new, "f", "2026-11-16T11:00:00+01:00")
    row = row_of(snap_with_other(root, presence=presence), "f")
    assert row.where.path == str(new)
    assert [(s["name"], s["path"]) for s in row.signals] == [("removable", str(old))]
    (old / "wip.txt").write_text("wip")
    assert row_of(snap_with_other(root, presence=presence), "f").signals == ()
    shutil.rmtree(old)
    result = snap_with_other(root, presence=presence)
    row = row_of(result, "f")
    assert row.error and row.where.path == str(new) and row.where.dirty == 1
    assert result.partial is True and any(str(old) in e.message or str(old) == e.source
                                          for e in result.errors)


def test_every_signal_carries_the_path_it_is_about(tmp_path):
    """Witness: all five signals in one snapshot - `removable` and `base-unknown` name their
    worktree (`alpha-wt`, `beta-nb`), the other three are about the row and carry None.
    Distinguishes a path on every signal (or none) from a path only where a tree is meant."""
    produced = {s["name"]: s.get("path", "MISSING") for s in all_five_signals(tmp_path)}
    assert produced == {
        "removable": str((tmp_path / "roots" / "alpha-wt").resolve()),
        "base-unknown": str((tmp_path / "nobase" / "beta-nb").resolve()),
        "claim-no-agent": None,
        "phase-fail": None,
        "handoff-missing": None,
    }


# DST change 2026-10-25: 02:30+02:00 is 00:30Z, 02:10+01:00 is 01:10Z (the newer instant),
# although the first string sorts after the second.
DST_OLDER = "2026-10-25T02:30:00+02:00"
DST_NEWER = "2026-10-25T02:10:00+01:00"


def test_binding_orders_recorded_at_as_instants_across_a_dst_change(tmp_path):
    """Witness: records `a` at 02:30+02:00 (00:30Z) and `b` at 02:10+01:00 (01:10Z, newer) for
    one tree, in both file orders - distinguishes row `b` (newest instant) from row `a`
    (newest ISO string)."""
    for older_name, newer_name in (("a", "b"), ("b", "a")):
        root = tmp_path / f"roots-{older_name}"
        primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
        wt = add_worktree(primary, "f-25", "feature/f-25")
        presence = tmp_path / f"presence-{older_name}"
        record(presence, older_name, wt, "a", DST_OLDER)
        record(presence, newer_name, wt, "b", DST_NEWER)
        result = snap_with_other(root, presence=presence)
        assert keys(result.open + result.history) == {("acme/alpha", "b")}, older_name


def test_feature_tree_orders_bound_at_as_instants_across_a_dst_change():
    """Witness: spec copies bound at 02:30+02:00 (00:30Z) and 02:10+01:00 (01:10Z, newer) -
    distinguishes the copy and worktree of tree `/r-new` (newest instant) from `/r-old`
    (newest ISO string)."""
    def tree(name: str, bound_at: str) -> list[traces.Trace]:
        return [traces.Trace("worktree", "r", "f", f"/{name}", None, 0.0, "", bound_at),
                traces.Trace("spec", "r", "f", f"/{name}/docs/specs/f.md", None, 0.0, "", bound_at)]
    members = [*tree("r-old", DST_OLDER), *tree("r-new", DST_NEWER)]
    for ordered in (members, members[::-1]):
        spec, worktree = traces.feature_tree(ordered)
        assert (spec.path, worktree.path) == ("/r-new/docs/specs/f.md", "/r-new")


def test_agent_of_a_row_is_the_newest_instant_across_a_dst_change(tmp_path):
    """Witness: two alive records of one row at 02:30+02:00 (00:30Z, pid 1) and 02:10+01:00
    (01:10Z, pid 2) - distinguishes agent pid 2 (newest instant) from pid 1 (newest ISO string)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f", "feature/f")
    proc, presence = tmp_path / "proc", tmp_path / "presence"
    fake_stat(proc)
    for name, pid, at in (("one", 1, DST_OLDER), ("two", 2, DST_NEWER)):
        harness.fake_proc(proc, pid, "claude", 1, 360000)
        harness.write_record(presence, name, slug="f", repo_root=str(wt), pid=pid,
                             process="claude", start_ticks=360000, recorded_at=at)
    rows = traces.attach_agents(list(snap_with_other(root, presence=presence).open),
                                read_store(presence, proc), proc)
    assert [r.agent["pid"] for r in rows if r.slug == "f"] == [2]


def test_a_dead_record_binds_its_worktree_but_shows_no_agent(tmp_path):
    """Witness: a dead record binds `alpha-f-25` to `f` - distinguishes a row `f` holding that
    worktree with `agent` None from row `f-25` (no binding) and from an agent shown for a
    dead process."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f-25", "feature/f-25")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    record(presence, "dead", wt, "f", "2026-11-16T10:00:00+01:00")
    row = row_of(snap_with_other(root, presence=presence), "f")
    assert [t.path for t in row.traces if t.kind == "worktree"] == [str(wt)]
    assert row.agent is None


def test_a_presence_stamp_that_is_not_an_instant_makes_the_store_malformed(tmp_path):
    """Witness: a record stamped "yesterday" next to a valid one - distinguishes a partial
    snapshot with the store listed and the directory slug `f-25` (no binding) from a
    ValueError out of `snapshot`."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f-25", "feature/f-25")
    presence, proc = tmp_path / "presence", tmp_path / "proc"
    fake_stat(proc)
    alive_record(presence, proc, "good", wt, "f")
    record(presence, "bad", wt, "g", "yesterday")
    result = snap_with_other(root, presence=presence)
    assert keys(result.open + result.history) == {("acme/alpha", "f-25")}
    assert [(e.source, e.message.startswith("malformed presence record"))
            for e in result.errors] == [(str(presence), True)]
    assert result.partial is True
    assert row_of(result, "f-25").agent is None


def test_a_naive_and_an_aware_stamp_for_one_tree_make_the_store_malformed(tmp_path):
    """Witness: records `2026-11-16T10:00:00` (naive) and `2026-11-16T11:00:00+01:00` for one
    tree - distinguishes a partial snapshot, no binding (row `f-25`), from a TypeError out of
    `snapshot` and from binding to the aware record (row `f`)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "f-25", "feature/f-25")
    presence = tmp_path / "presence"
    record(presence, "naive", wt, "g", "2026-11-16T10:00:00")
    record(presence, "aware", wt, "f", "2026-11-16T11:00:00+01:00")
    result = snap_with_other(root, presence=presence)
    assert keys(result.open + result.history) == {("acme/alpha", "f-25")}
    assert [e.source for e in result.errors] == [str(presence)]
    assert result.partial is True


def test_presence_beats_the_branch_spec_fallback(tmp_path):
    """Witness: worktree `alpha-wt-9` is bound to `f` while its branch is the `branch:` of the
    spec `g` in that tree - distinguishes a worktree trace in row `f` and none in row `g` from
    the branch fallback (worktree trace in row `g`)."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "f", undelivered_spec("f", "feature/f"))
    commit(primary, "f")
    wt = add_worktree(primary, "wt-9", "feature/g")
    write_spec(wt, "g", undelivered_spec("g", "feature/g"))
    commit(wt, "g")
    presence = tmp_path / "presence"
    record(presence, "p", wt, "f", "2026-11-16T10:00:00+01:00")
    result = snap(root, presence=presence)
    worktree_slugs = [(r.slug, t.path) for r in result.open + result.history
                      for t in r.traces if t.kind == "worktree"]
    assert worktree_slugs == [("f", str(wt))]


def test_the_rows_worktree_is_the_one_in_the_copys_tree_not_the_newest_binding(tmp_path):
    """Witness: `f` bound to `f-1` (older, holds docs/specs/f.md approved) and `f-2` (newer, no
    copy) - distinguishes `where.path` = `f-1` and phase 1 from `where.path` = `f-2`; `f-2`
    must be in `row.trees`."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    one = add_worktree(primary, "f-1", "feature/f-1")
    write_spec(one, "f", undelivered_spec("f", "feature/f", approved=True))
    commit(one, "approve")
    two = add_worktree(primary, "f-2", "feature/f-2")
    presence = tmp_path / "presence"
    record(presence, "old", one, "f", "2026-11-16T09:00:00+01:00")
    record(presence, "new", two, "f", "2026-11-16T11:00:00+01:00")
    row = row_of(snap(root, presence=presence), "f")
    assert (row.phase.kind, row.phase.phase) == ("derived", 1)
    assert row.where.path == str(one)
    assert [w.path for w in row.trees] == [str(two)]


# --- #36: the phase cache ---------------------------------------------------

def git_workflow(tmp_path: Path) -> Path:
    """A committed git install of the workflow (real wf.py/audit.py), so it has a fingerprint."""
    wdir = tmp_path / "wf-install"
    wdir.mkdir()
    for name in ("wf.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    git(wdir, "init", "-q", "-b", "master")
    commit(wdir, "install")
    return wdir


def stub_wf(monkeypatch, derive) -> None:
    """Make `snapshot` load a wf whose derive is `derive(repo_root, feature, store)`."""
    real = traces.load_wf(ROOT)
    wrapped = SimpleNamespace(derive=derive, parse_front_matter=real.parse_front_matter,
                              read_presence=real.read_presence,
                              default_presence_dir=real.default_presence_dir)
    monkeypatch.setattr(traces, "load_wf", lambda workflow_dir: wrapped)


def counting_wf(monkeypatch) -> list[str]:
    """Make `snapshot` load a wf whose derive is the real one, recording each call's feature."""
    real = traces.load_wf(ROOT)
    calls: list[str] = []

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        return real.derive(repo_root, feature, store)

    stub_wf(monkeypatch, derive)
    return calls


def cached_snap(root: Path, workflow: Path, cache: dict):
    return traces.snapshot((root,), workflow, NOW, NO_PRESENCE, cache)


def cache_fixture(tmp_path: Path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "f", undelivered_spec("f", "feature/f"))
    (primary / "src.txt").write_text("one\n")
    commit(primary, "specs")
    return root, primary, git_workflow(tmp_path)


def test_unchanged_tree_is_not_derived_again(tmp_path, monkeypatch):
    """witness two scans of an untouched dirty tree with two specs - distinguishes
    'derive once per spec, second phase equal to the first' (2 calls) from
    'derive at every scan' (4 calls)."""
    root, primary, workflow = cache_fixture(tmp_path)
    write_spec(primary, "g", undelivered_spec("g", "feature/g"))
    commit(primary, "g")
    (primary / "src.txt").write_text("dirty\n")
    calls = counting_wf(monkeypatch)
    cache: dict = {}
    first = cached_snap(root, workflow, cache)
    second = cached_snap(root, workflow, cache)
    assert sorted(calls) == ["f", "g"]
    assert [(r.slug, r.phase) for r in second.open] == [(r.slug, r.phase) for r in first.open]
    assert first.open


def test_phase_cache_misses_on_every_input_change(tmp_path, monkeypatch):
    """witness an already-dirty file edited again (same ` M` status line, new size), a new
    untracked file, a new commit, then a new untracked file of the install (a hit) and an edited
    install file `derive` reads - distinguishes 'fingerprint = HEAD + status + mtime/size of each
    entry' from a status-line-only fingerprint (case 1 would hit), a HEAD-only one (cases 1, 2, 4,
    5 would hit) and from the whole-install git status (case 4 would miss)."""
    root, primary, workflow = cache_fixture(tmp_path)
    (primary / "src.txt").write_text("dirty\n")
    calls = counting_wf(monkeypatch)
    cache: dict = {}
    cached_snap(root, workflow, cache)
    assert calls == ["f"]

    steps = [
        (lambda: (primary / "src.txt").write_text("dirty again, longer\n"), True),
        (lambda: (primary / "new.txt").write_text("new\n"), True),
        (lambda: commit(primary, "next"), True),
        (lambda: (workflow / "reports").mkdir() or (workflow / "reports" / "r.md").write_text("r\n"), False),
        (lambda: (workflow / "audit.py").unlink() or (workflow / "audit.py").symlink_to(ROOT / "wf.py"), True),
    ]
    expected = 1
    for step_no, (step, derives) in enumerate(steps, start=1):
        step()
        expected += derives
        cached_snap(root, workflow, cache)
        assert calls == ["f"] * expected, f"step {step_no} {'did not re-derive' if derives else 're-derived'}"
        cached_snap(root, workflow, cache)   # control: the changed tree is now cached
        assert calls == ["f"] * expected, f"step {step_no} derived twice"


def test_a_derive_failure_is_not_cached(tmp_path, monkeypatch):
    """witness a derive that raises on scan 1 and on scan 2 over an unchanged tree -
    distinguishes 'failures are not stored' (2 calls) from 'failure stored' (1 call)."""
    root, _, workflow = cache_fixture(tmp_path)
    calls: list[str] = []

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        raise RuntimeError("guard crashed")

    stub_wf(monkeypatch, derive)
    cache: dict = {}
    for _ in range(2):
        snapshot = cached_snap(root, workflow, cache)
        assert snapshot.partial
    assert calls == ["f", "f"] and cache == {}


def test_a_tree_without_a_fingerprint_is_always_derived(tmp_path, monkeypatch):
    """witness (a) a workflow install without a readable wf.py and (b) a spec tree where git
    fails (stubbed to None) - distinguishes 'no fingerprint, no cache'
    (2 derives each) from 'None == None counts as a hit' (1 derive)."""
    root, _primary, workflow = cache_fixture(tmp_path)
    calls = counting_wf(monkeypatch)
    cache: dict = {}
    plain = tmp_path / "plain-install"
    plain.mkdir()
    (plain / "audit.py").symlink_to(ROOT / "audit.py")
    assert traces.workflow_fingerprint(plain) is None
    cached_snap(root, plain, cache)
    cached_snap(root, plain, cache)
    assert calls == ["f", "f"] and cache == {}
    monkeypatch.setattr(traces, "tree_fingerprint", lambda tree: None)
    cached_snap(root, workflow, cache)
    cached_snap(root, workflow, cache)
    assert calls == ["f"] * 4 and cache == {}


def test_the_cache_keeps_only_the_last_scans_trees(tmp_path, monkeypatch):
    """witness a worktree holding a spec copy, removed between two scans - distinguishes
    'prune to the last scan's keys' (key gone) from 'never evict' (key stays)."""
    root, primary, workflow = cache_fixture(tmp_path)
    tree = add_worktree(primary, "f", "feature/f")
    write_spec(tree, "w", undelivered_spec("w", "feature/w"))
    counting_wf(monkeypatch)
    cache: dict = {}
    cached_snap(root, workflow, cache)
    assert tree.resolve() in {key_tree.resolve() for key_tree, _ in cache}
    git(primary, "worktree", "remove", "--force", str(tree))
    cached_snap(root, workflow, cache)
    assert {key_tree.resolve() for key_tree, _ in cache} == {primary.resolve()}


def test_snapshot_without_a_cache_derives_every_spec(tmp_path, monkeypatch):
    """witness two scans with cache=None over an unchanged tree - distinguishes 'today's
    behaviour' (2 derives) from a hidden module-level cache (1 derive)."""
    root, _, workflow = cache_fixture(tmp_path)
    calls = counting_wf(monkeypatch)
    traces.snapshot((root,), workflow, NOW, NO_PRESENCE)
    traces.snapshot((root,), workflow, NOW, NO_PRESENCE)
    assert calls == ["f", "f"]


def ignored_workflow(tmp_path: Path) -> Path:
    """A git install whose `reports/` and `handoffs/` are git-ignored (as in a real install)."""
    wdir = tmp_path / "wf-install"
    wdir.mkdir(parents=True)
    for name in ("wf.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    (wdir / ".gitignore").write_text("reports/\nhandoffs/\n")
    git(wdir, "init", "-q", "-b", "master")
    commit(wdir, "install")
    return wdir


def test_the_phase_cache_ignores_report_files(tmp_path, monkeypatch):
    """witness a new reports/r.jsonl and handoffs/x-reports/x.jsonl in an install where both are
    git-ignored, then a write to the store - distinguishes a fingerprint that lists the report
    files (2 derives, a changed fingerprint after the files) from one of the tree and the store
    (1 derive and the same fingerprint after the files; a second derive and a changed fingerprint
    after the store write)."""
    root, _, _ = cache_fixture(tmp_path)
    workflow = ignored_workflow(tmp_path / "ign")
    store = tmp_path / "state" / "reports.sqlite"
    calls = counting_wf(monkeypatch)
    cache: dict = {}

    def scan():
        return traces.snapshot((root,), workflow, NOW, NO_PRESENCE, cache, store=store)

    before = traces.workflow_fingerprint(workflow, store)
    (workflow / "reports").mkdir()
    (workflow / "reports" / "r.jsonl").write_text("{}\n")
    (workflow / "handoffs" / "x-reports").mkdir(parents=True)
    (workflow / "handoffs" / "x-reports" / "x.jsonl").write_text("{}\n")
    assert traces.workflow_fingerprint(workflow, store) == before
    scan()
    scan()
    assert calls == ["f"]
    stored_matrix(store, "m")
    assert traces.workflow_fingerprint(workflow, store) != before
    scan()
    assert calls == ["f", "f"]


def derive_install(tmp_path: Path) -> Path:
    """A git install holding real copies of the files `wf.derive` reads, plus handoffs/ and
    an experiments directory that it does not."""
    wdir = tmp_path / "derive-install"
    wdir.mkdir()
    for name in ("wf.py", "audit.py", "report_store.py"):
        shutil.copy(ROOT / name, wdir / name)
    shutil.copytree(ROOT / "guards", wdir / "guards", ignore=shutil.ignore_patterns("__pycache__"))
    (wdir / "handoffs").mkdir()
    (wdir / "handoffs" / "h.md").write_text("h\n")
    (wdir / "experiments").mkdir()
    (wdir / "experiments" / "e.py").write_text("e\n")
    git(wdir, "init", "-q", "-b", "master")
    commit(wdir, "install")
    return wdir


def test_install_fingerprint_follows_only_derive_inputs(tmp_path):
    """witness a new root file, an edit under the experiments directory, a handoffs-only commit and a
    guards/__pycache__ file (all equal) against an edit of each input, of guards/hooks/ and an
    added then removed guards file (all different) - distinguishes 'content of DERIVE_INPUTS' from
    'HEAD + git status' (the equal cases would differ), from 'mtime only' (an edit that restores
    the mtime would not change it, so the edit rewrites the same size) and from a non-recursive
    walk (the guards/hooks/ case would be equal)."""
    wdir = derive_install(tmp_path)
    hook = next((wdir / "guards" / "hooks").iterdir())
    seen = traces.install_fingerprint(wdir)
    assert seen and all(digest for _, digest in seen)

    (wdir / "new.txt").write_text("new\n")
    (wdir / "experiments" / "e.py").write_text("edited\n")
    (wdir / "handoffs" / "h2.md").write_text("h2\n")
    commit(wdir, "handoff")
    (wdir / "guards" / "__pycache__").mkdir()
    (wdir / "guards" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert traces.install_fingerprint(wdir) == seen

    def changes(path: Path) -> None:
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])   # same size, other content
        assert traces.install_fingerprint(wdir) != seen, f"{path.name} did not change it"
        path.write_bytes(original)
        assert traces.install_fingerprint(wdir) == seen

    for path in (wdir / "wf.py", wdir / "audit.py", wdir / "report_store.py", hook,
                 wdir / "guards" / "gate.py"):
        changes(path)
    added = wdir / "guards" / "extra.py"
    added.write_text("x = 1\n")
    assert traces.install_fingerprint(wdir) != seen
    added.unlink()
    assert traces.install_fingerprint(wdir) == seen
    gate = wdir / "guards" / "gate.py"
    kept = gate.read_bytes()
    gate.unlink()
    assert traces.install_fingerprint(wdir) != seen
    gate.write_bytes(kept)


def test_install_fingerprint_marks_a_missing_listed_file(tmp_path):
    """witness report_store.py removed - distinguishes 'a missing listed file is (path, None)'
    from a fingerprint that skips it (no entry, and a value that could equal an install that
    never had it)."""
    wdir = derive_install(tmp_path)
    before = traces.install_fingerprint(wdir)
    (wdir / "report_store.py").unlink()
    after = traces.install_fingerprint(wdir)
    assert ("report_store.py", None) in after
    assert after != before


def test_install_fingerprint_lists_a_missing_guards_directory_once(tmp_path):
    """witness an install without guards/ - distinguishes 'one (guards, None) entry' from the
    entry emitted twice (once as a listed name, once by a directory check)."""
    wdir = derive_install(tmp_path)
    shutil.rmtree(wdir / "guards")
    assert [entry for entry in traces.install_fingerprint(wdir) if entry[0] == "guards"] == [
        ("guards", None)]


def test_workflow_fingerprint_is_none_only_without_wf_py(tmp_path):
    """witness a plain (non-git) install with the four inputs, then the same without wf.py -
    distinguishes 'content-only, git not asked' (a fingerprint for the non-git install) from
    the old 'None when not a git repo', and 'None only for a missing wf.py' from 'None for any
    missing input'."""
    wdir = derive_install(tmp_path)
    plain = tmp_path / "plain"
    shutil.copytree(wdir, plain, ignore=shutil.ignore_patterns(".git"))
    assert traces.workflow_fingerprint(plain) is not None
    (plain / "report_store.py").unlink()
    assert traces.workflow_fingerprint(plain) is not None
    (plain / "wf.py").unlink()
    assert traces.workflow_fingerprint(plain) is None


def test_derive_reads_only_the_listed_install_files(tmp_path, monkeypatch):
    """witness `wf.derive` on a feature of another repo, watched by an audit hook in this process
    and by a sitecustomize.py audit hook in every guard subprocess - distinguishes 'every opened
    path under the install is in DERIVE_INPUTS' from a derive (or guard) that also reads another
    install file such as LESSONS.md or a private notes directory (the hook would list it)."""
    repo = tmp_path / "other-repo"
    (repo / "docs" / "mockups").mkdir(parents=True)
    (repo / "docs" / "mockups" / "demo.md").write_text("mockup\n")   # phase 2 wants the mockup
    # the `demo` feature through phase 6 (spec, design, model and the code they name): with it phase 5
    # passes and the walk reaches phase 6, where `check_model.py` runs (the evidence goes to a scratch
    # workflow dir, so the install has no store: phase 6 then fails on the brief, after the model check)
    harness.make_delivered(repo, tmp_path / "scratch-workflow")
    git(repo, "init", "-q", "-b", "master")
    commit(repo, "copies")

    log = tmp_path / "opened.log"
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(
        "import os, sys\n"
        "_fd = os.open(os.environ['DERIVE_OPEN_LOG'], os.O_WRONLY | os.O_APPEND | os.O_CREAT)\n"
        "def _hook(event, args):\n"
        "    if event == 'open' and isinstance(args[0], (str, bytes)):\n"
        "        os.write(_fd, os.fsencode(os.path.basename(sys.argv[0])) + b'\\t' + os.fsencode(args[0]) + b'\\n')\n"
        "sys.addaudithook(_hook)\n")
    monkeypatch.setenv("DERIVE_OPEN_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(hook_dir), *filter(None, [os.environ.get("PYTHONPATH")])]))

    opened: list[str] = []
    active = [False]

    def hook(event, args):
        if active[0] and event == "open" and isinstance(args[0], (str, bytes)):
            opened.append(os.fsdecode(args[0]))

    sys.addaudithook(hook)   # a hook cannot be removed: `active` switches it off
    active[0] = True
    try:
        wf = traces.load_wf(ROOT)
        wf.derive(repo, "demo")
    finally:
        active[0] = False

    by_guard = [line.split("\t", 1) for line in log.read_text().splitlines()] if log.exists() else []
    in_guards = [path for _, path in by_guard]
    install = os.path.realpath(ROOT)
    listed = tuple(os.path.join(install, name) for name in traces.DERIVE_INPUTS)
    outside = sorted({
        real for path in opened + in_guards
        if (real := os.path.realpath(path)).startswith(install + os.sep)
        and "__pycache__" not in real.split(os.sep)
        and not any(real == item or real.startswith(item + os.sep) for item in listed)})
    assert in_guards, "the guard subprocesses were not watched"
    assert {guard for guard, _ in by_guard} >= {"check_design.py", "check_model.py"}, \
        "derive did not reach phase 6: check_model.py was not watched"
    assert outside == []


# --- wire ids (#26) ---------------------------------------------------------

def test_trace_ids_are_the_wire_ids():
    """Witness the literal id of a spec trace, pinned as hex - distinguish
    `sha1(kind \\0 path)[:12]` from any other recipe (the
    same path under another kind must give another id, so the kind is in the hash)."""
    spec = traces.Trace("spec", "o/r", "f", "/r/docs/specs/f.md", None, 0.0, "")
    handoff = traces.Trace("handoff", "o/r", "f", "/r/docs/specs/f.md", None, 0.0, "")
    assert traces.trace_id(spec) == "a2df0aa1ba46"
    assert traces.trace_id(handoff) != traces.trace_id(spec)


# --- links (#26, Decision 17) -----------------------------------------------

def test_ticket_link():
    """Witness `EXAMPLE-5166` with `jira_url="https://j.example/"` (trailing slash), the same key
    without a URL, and a GitHub issue URL - distinguish 'key joined to the URL without a double
    slash' from a link built without the base, a key linked with no Jira known, and a URL
    rewritten; free text and a non-http scheme give None (never an href)."""
    issue = "https://github.com/o/r/issues/7"
    assert traces.ticket_link(issue, None) == issue
    assert traces.ticket_link(issue, "https://j.example") == issue
    assert traces.ticket_link("EXAMPLE-5166", "https://j.example/") == \
        "https://j.example/browse/EXAMPLE-5166"
    assert traces.ticket_link("EXAMPLE-5166", "https://j.example") == \
        "https://j.example/browse/EXAMPLE-5166"
    assert traces.ticket_link("EXAMPLE-5166", None) is None
    assert traces.ticket_link("EXAMPLE-5166 and more", "https://j.example") is None
    assert traces.ticket_link("#1", "https://j.example") is None
    assert traces.ticket_link("example-5166", "https://j.example") is None
    assert traces.ticket_link("javascript:alert(1)", "https://j.example") is None
    assert traces.ticket_link(None, "https://j.example") is None
    for bad in ("javascript:alert(1)//", "ftp://j.example", "j.example"):
        assert traces.ticket_link("EXAMPLE-5166", bad) is None
    assert traces.ticket_link("EXAMPLE-5166", "http://j.example") == \
        "http://j.example/browse/EXAMPLE-5166"


def test_pr_link():
    """Witness branch `feature/x` - the `/` must be encoded, a branch without one cannot tell
    `quote(safe='')` from no encoding; Bitbucket's `scm/acme` must come out as `projects/ACME`."""
    github = "https://github.com/o/r/pulls?q=is%3Apr+head%3Afeature%2Fx"
    assert traces.pr_link("https://github.com/o/r.git", "feature/x") == github
    assert traces.pr_link("https://github.com/o/r", "feature/x") == github
    assert traces.pr_link("git@github.com:o/r.git", "feature/x") == github
    bitbucket = ("https://h/ctx/projects/ACME/repos/repo/pull-requests"
                 "?at=refs%2Fheads%2Ffeature%2Fx")
    assert traces.pr_link("https://h/ctx/scm/acme/repo.git", "feature/x") == bitbucket
    assert traces.pr_link("https://h/scm/acme/repo.git", "feature/x") == \
        "https://h/projects/ACME/repos/repo/pull-requests?at=refs%2Fheads%2Ffeature%2Fx"
    assert traces.pr_link("https://gitlab.com/o/r.git", "feature/x") is None
    assert traces.pr_link("https://github.com/o/r.git", None) is None
    assert traces.pr_link(None, "feature/x") is None
    assert traces.pr_link("/home/me/repo", "feature/x") is None
    assert traces.pr_link("https://github.com/o/r/extra/deep.git", "feature/x") is None


def test_links_never_carry_origin_credentials(tmp_path):
    """Witness `https://alice:s3cr3t@github.com/o/r.git` and the Bitbucket form with userinfo AND a
    port, on a real repo whose snapshot is serialized - distinguish a link rebuilt from host and
    path from the origin URL (or its netloc) copied; the wire must hold neither secret anywhere."""
    assert traces.pr_link("https://alice:s3cr3t@github.com/o/r.git", "f") == \
        "https://github.com/o/r/pulls?q=is%3Apr+head%3Af"
    assert traces.pr_link("https://alice:s3cr3t@h:7443/ctx/scm/acme/repo.git", "f") == \
        "https://h/ctx/projects/ACME/repos/repo/pull-requests?at=refs%2Fheads%2Ff"
    assert traces.pr_link("https://alice:s3cr3t@evil.example@github.com/o/r.git", "f") == \
        "https://github.com/o/r/pulls?q=is%3Apr+head%3Af"   # the host is what follows the last `@`
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://alice:s3cr3t@h:7443/ctx/scm/acme/alpha.git")
    write_spec(primary, "f", tracked_spec("f", "feature/f", "EXAMPLE-1"))
    commit(primary, "spec")
    wire = json.dumps(traces.to_json(snap(root)))
    assert "s3cr3t" not in wire and "alice" not in wire and "7443" not in wire
    assert "projects/ACME/repos/alpha/pull-requests" in wire


def tracked_spec(slug: str, branch: str, tracker: str) -> str:
    return undelivered_spec(slug, branch).replace(f"branch: {branch}\n",
                                                  f"branch: {branch}\ntracker: {tracker}\n", 1)


def test_row_links_use_the_worktree_branch(tmp_path):
    """Witness a row in a worktree on `feature/x-26` bound by presence to a spec whose `branch:`
    is `feature/x`, and the same spec in a repo with no worktree - distinguish 'where.branch of a
    worktree' from the spec's branch always (`feature/x`) and from the primary's `master`."""
    origin = "https://h/scm/acme/alpha.git"
    pr = "https://h/projects/ACME/repos/alpha/pull-requests?at=refs%2Fheads%2F"
    jira = "https://j.example/"
    bound = tmp_path / "bound"
    primary = make_repo(bound / "roots", "alpha", origin)
    write_spec(primary, "f", tracked_spec("f", "feature/x", "EXAMPLE-5166"))
    commit(primary, "spec")
    wt = add_worktree(primary, "f-26", "feature/x-26")
    presence = tmp_path / "presence"
    record(presence, "a", wt, "f", "2026-11-16T11:00:00+01:00")
    with_wt = row_of(traces.snapshot((bound / "roots",), WORKFLOW[0], NOW, presence, jira_url=jira), "f")
    assert with_wt.where.kind == "worktree" and with_wt.where.branch == "feature/x-26"
    assert with_wt.links == {"pr": pr + "feature%2Fx-26",
                             "ticket": "https://j.example/browse/EXAMPLE-5166",
                             "tracker": "EXAMPLE-5166"}
    alone = tmp_path / "alone"
    primary2 = make_repo(alone / "roots", "alpha", origin)
    write_spec(primary2, "f", tracked_spec("f", "feature/x", "EXAMPLE-5166"))
    commit(primary2, "spec")
    on_primary = row_of(snap(alone / "roots"), "f")
    assert on_primary.where.kind == "primary" and on_primary.where.branch == "master"
    assert on_primary.links == {"pr": pr + "feature%2Fx", "ticket": None, "tracker": "EXAMPLE-5166"}
    wire = traces.row_json(with_wt)
    assert wire["links"] == with_wt.links


def test_a_row_without_spec_or_origin_has_empty_links(tmp_path):
    """Witness a claim-only row in a repo with no origin and a handoff-only row - distinguish
    'links absent as nulls' from a missing key or a crash on a row with no spec."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha")
    write_claim(primary, "c")
    wire = traces.to_json(snap(root))
    assert wire["open"][0]["links"] == {"pr": None, "ticket": None, "tracker": None}


# --- report-store #56: the window reads the store -----------------------------

STORE_NOW = datetime(2026, 11, 17, 12, 0)


def stored_matrix(store: Path, name: str) -> None:
    conn = report_store.open_store(store)
    report_store.add_matrix(conn, name, [{"id": "M1", "kind": "mutant", "outcome": "pinned"}], STORE_NOW)
    conn.close()


def stored_manifest(store: Path, name: str) -> None:
    # by SQL: a valid prover manifest is long; only a stored one is needed here
    conn = report_store.open_store(store)
    with conn:
        conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES (?,?,'{}')",
                     (name, STORE_NOW.isoformat()))
    conn.close()


def test_a_stored_matrix_and_manifest_are_attributed_by_name(tmp_path):
    """witness a slug `f-x` also known (the longest prefix wins) and `f` known in two repos -
    distinguishes the longest-known-slug rule of Decision 8 from 'every name starting with
    `f-`', and a slug in exactly one repo from a slug in two (the name is then on no row)."""
    store = tmp_path / "s" / "reports.sqlite"
    for name in ("f-mutation-1", "f-x-mutation-1"):
        stored_matrix(store, name)
    stored_manifest(store, "f-mutation-manifest-1")
    one = {"f": frozenset({"acme/alpha"}), "f-x": frozenset({"acme/alpha"})}
    assert traces.stored_reports(store, "f", "acme/alpha", one) == [
        ("f-mutation-1.jsonl", ("matrix", "f-mutation-1")),
        ("f-mutation-manifest-1.json", ("manifest", "f-mutation-manifest-1"))]
    assert traces.stored_reports(store, "f-x", "acme/alpha", one) == [
        ("f-x-mutation-1.jsonl", ("matrix", "f-x-mutation-1"))]
    two = {"f": frozenset({"acme/alpha", "acme/beta"}), "f-x": frozenset({"acme/alpha"})}
    assert traces.stored_reports(store, "f", "acme/alpha", two) == []
    assert traces.stored_reports(store, "f", "acme/beta", two) == []


def test_stored_reports_of_a_missing_store_are_none_and_create_nothing(tmp_path):
    """witness a store path under a directory that does not exist, and None - distinguishes
    `open_existing` from `open_store` (which would create the directory and the file)."""
    store = tmp_path / "absent" / "reports.sqlite"
    known = {"f": frozenset({"acme/alpha"})}
    assert traces.stored_reports(store, "f", "acme/alpha", known) == []
    assert traces.stored_reports(None, "f", "acme/alpha", known) == []
    assert not store.parent.exists()


DELIVERY_NA = ("\n## Delivery\n\n"
               + "".join(f"- {ref} — PR: https://example.com/pr/1 · Model: n/a — docs only · "
                         "Mutation: n/a — docs only\n"
                         for ref in ("#9", "https://example.com/owner/demo/issues/2")))


def stored_phase_fixture(tmp_path: Path):
    """A committed repo whose spec `demo` passes phases 0-5 and needs, at phase 6, the brief and
    the reviewer reports: nothing but the store can hold them."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    spec = harness.SPEC.replace("| 6 · Implement-loop | skip | docs only |", "| 6 · Implement-loop | run | — |")
    write_spec(primary, "demo", spec + DELIVERY_NA)
    commit(primary, "spec")
    return root, git_workflow(tmp_path)


def test_a_store_write_derives_the_phase_again(tmp_path, monkeypatch):
    """witness the same tree and the same `reports/` listing in every snapshot, the store absent,
    then created with the brief and the reports, then untouched - distinguishes a fingerprint that
    includes the store's (mtime, size) (derive, derive, hit: 2 calls, the phase changes) from one
    that ignores the store (1 call, a stale phase 6)."""
    root, workflow = stored_phase_fixture(tmp_path)
    store = tmp_path / "state" / "reports.sqlite"
    calls = counting_wf(monkeypatch)
    cache: dict = {}

    def phase():
        found = traces.snapshot((root,), workflow, NOW, NO_PRESENCE, cache, store=store)
        row = next(r for r in (*found.open, *found.history) if r.slug == "demo")
        return row.phase.kind, row.phase.phase

    assert phase() == ("derived", 6)
    conn = report_store.open_store(store)
    rows = [{"repo": "owner/demo", "criterion": "a", "item_id": i, "verdict": "pass", "severity": "fatal",
             "reviewer": "spec", "actor": "spec-1", "claim": "coverage"} for i in ("I1", "I2")]
    report_store.add_review_rows(conn, rows, "demo", "demo-spec", tmp_path, STORE_NOW)
    report_store.add_brief_items(conn, "owner/demo", "demo", ["I1", "I2"], STORE_NOW)
    conn.close()
    assert phase() == ("delivered", None)
    assert calls == ["demo", "demo"]
    assert phase() == ("delivered", None) and calls == ["demo", "demo"]


# --- report-store #56 review r1: one loader, one slug rule ----------------------

def test_load_report_store_is_the_installs_module_loaded_once_by_path():
    """witness two calls - distinguishes a cached module from a fresh exec per call, and the
    install's own file (the parent of app/) from whatever `report_store` sys.path finds."""
    first = traces.load_report_store()
    assert first is traces.load_report_store()
    assert Path(first.__file__) == Path(traces.__file__).resolve().parent.parent / "report_store.py"


def test_slug_owner_is_the_longest_known_slug_that_prefixes_the_name_with_a_dash():
    """witness `f-x` known beside `f` (the longest wins), a name equal to a slug (the shorter
    prefix owns it, as report_files always did) and a name that starts with a slug without the
    `-` - distinguishes longest from first match and prefix-with-dash from a bare startswith."""
    known = {"f": frozenset(), "f-x": frozenset()}
    assert traces._slug_owner("f-x-spec", known) == "f-x"
    assert traces._slug_owner("f-spec", known) == "f"
    assert traces._slug_owner("f-x", known) == "f"
    assert traces._slug_owner("f", known) is None
    assert traces._slug_owner("fx-spec", known) is None
    assert traces._slug_owner("g-spec", known) is None


def test_loading_the_report_store_writes_no_bytecode_next_to_it(tmp_path, monkeypatch):
    copy = tmp_path / "report_store.py"
    shutil.copy(traces.REPORT_STORE_PATH, copy)
    monkeypatch.setattr(traces, "REPORT_STORE_PATH", copy)
    monkeypatch.setattr(traces, "_REPORT_STORE", None)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    traces.load_report_store()
    assert not (tmp_path / "__pycache__").exists()


# --- #65: the phases come from background jobs --------------------------------

WAIT = 10   # seconds a test lets a gate or a job take before it fails instead of hanging


def jobs_snap(root: Path, workflow: Path, cache: dict, jobs):
    return traces.snapshot((root,), workflow, NOW, NO_PRESENCE, cache, jobs=jobs)


def test_a_running_phase_job_is_not_started_twice(tmp_path, monkeypatch):
    """witness two snapshots taken while the job of `f` is gated, then one after it ends -
    distinguishes 'one job per (tree, slug), the second scan finds it running and answers
    computing' (1 derive) from a job started by every scan (2 derives); the third scan reads
    the phase the job stored."""
    root, _, workflow = cache_fixture(tmp_path)
    real = traces.load_wf(ROOT)
    calls: list[str] = []
    started, gate = threading.Event(), threading.Event()

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        started.set()
        assert gate.wait(WAIT)
        return real.derive(repo_root, feature, store)

    stub_wf(monkeypatch, derive)
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=2)
    try:
        first = jobs_snap(root, workflow, cache, jobs)
        assert started.wait(WAIT)
        second = jobs_snap(root, workflow, cache, jobs)
        assert [row_of(s, "f").phase.kind for s in (first, second)] == ["computing", "computing"]
        gate.set()
        assert jobs.wait_idle(WAIT)
        third = jobs_snap(root, workflow, cache, jobs)
    finally:
        gate.set()
        jobs.close()
    assert calls == ["f"]
    assert (row_of(third, "f").phase.kind, row_of(third, "f").phase.phase) == ("derived", 0)


def test_concurrent_phases_equal_sequential(tmp_path, monkeypatch):
    """witness four features (unapproved, approved, delivered, approved) whose derives finish in
    the order d, c, b, a - the reverse of the scan - distinguishes 'rows and phases do not depend
    on which job ends first' (the snapshot with jobs equals the one without) from phases
    assigned in completion order; the first scan with jobs is all computing, so the equality is
    not vacuous."""
    root, primary, workflow = cache_fixture(tmp_path)
    texts = {"a": undelivered_spec("a", "feature/a"),
             "b": undelivered_spec("b", "feature/b", approved=True),
             "c": delivered_spec("c", "feature/c"),
             "d": undelivered_spec("d", "feature/d", approved=True)}
    for slug, text in texts.items():
        write_spec(primary, slug, text)
    commit(primary, "features")
    real = traces.load_wf(ROOT)
    finished = {slug: threading.Event() for slug in texts}
    after = {"a": "b", "b": "c", "c": "d"}   # a waits for b, b for c, c for d; d goes first
    order: list[str] = []

    def derive(repo_root, feature, store=None):
        if feature in after:
            assert finished[after[feature]].wait(WAIT)
        result = real.derive(repo_root, feature, store)
        if feature in finished:   # `f` of the fixture is not one of the four
            order.append(feature)
            finished[feature].set()
        return result

    stub_wf(monkeypatch, derive)
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=4)
    try:
        cold = jobs_snap(root, workflow, cache, jobs)
        assert jobs.wait_idle(WAIT)
        warm = jobs_snap(root, workflow, cache, jobs)
    finally:
        jobs.close()
    assert {row_of(cold, slug).phase.kind for slug in texts} == {"computing"}
    assert order == ["d", "c", "b", "a"]
    assert warm == traces.snapshot((root,), workflow, NOW, NO_PRESENCE)
    assert {row_of(warm, slug).phase.kind for slug in texts} == {"derived", "delivered"}


def test_a_failing_phase_stays_on_its_row(tmp_path, monkeypatch):
    """witness one derive that raises, on a fake clock - distinguishes 'the error is held 10 s
    per fingerprint on its row, the other rows intact' from a retry at every scan (calls grow at
    9.9 s), a hold that ignores the fingerprint (the rewritten spec is not derived again), and
    a hold that never expires (no call past 10 s): `bad` is derived 1 time at 0 s, still 1 at
    9.9 s, 2 after its spec is rewritten at 9.9 s, still 2 at 19.0 s and 3 at 19.95 s."""
    root, primary, workflow = cache_fixture(tmp_path)
    write_spec(primary, "bad", undelivered_spec("bad", "feature/bad"))
    commit(primary, "bad")
    real = traces.load_wf(ROOT)
    calls: list[str] = []

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        if feature == "bad":
            raise RuntimeError("guard crashed")
        return real.derive(repo_root, feature, store)

    stub_wf(monkeypatch, derive)
    now = [0.0]
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=2, clock=lambda: now[0])

    def scan_at(seconds: float):
        now[0] = seconds
        result = jobs_snap(root, workflow, cache, jobs)
        assert jobs.wait_idle(WAIT)
        return result

    try:
        scan_at(0.0)
        failed = scan_at(0.0)
        assert calls.count("bad") == 1
        assert "phase: guard crashed" in row_of(failed, "bad").error
        assert row_of(failed, "f").error is None and row_of(failed, "f").phase.kind == "derived"
        scan_at(9.9)
        assert calls.count("bad") == 1
        write_spec(primary, "bad", undelivered_spec("bad", "feature/bad", approved=True))
        scan_at(9.9)
        assert calls.count("bad") == 2
        scan_at(19.0)
        assert calls.count("bad") == 2
        scan_at(19.95)
        scan_at(19.95)
        assert calls.count("bad") == 3
    finally:
        jobs.close()


def test_a_tree_without_fingerprint_is_derived_in_the_request(tmp_path, monkeypatch):
    """witness (a) a workflow install without a readable wf.py and (b) a spec tree where git fails
    (fingerprint stubbed to None), each scanned twice with jobs - distinguishes 'no fingerprint,
    derived in the request, never cached' (derived at once, 2 derives, empty cache) from a job
    that would stay `computing` forever (Decision 13)."""
    root, _, workflow = cache_fixture(tmp_path)
    calls = counting_wf(monkeypatch)
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=2)
    plain = tmp_path / "plain-install"
    plain.mkdir()
    (plain / "audit.py").symlink_to(ROOT / "audit.py")
    try:
        for install in (plain, plain):
            assert row_of(jobs_snap(root, install, cache, jobs), "f").phase.kind == "derived"
        assert calls == ["f", "f"] and cache == {}
        monkeypatch.setattr(traces, "tree_fingerprint", lambda tree: None)
        for _ in range(2):
            assert row_of(jobs_snap(root, workflow, cache, jobs), "f").phase.kind == "derived"
        assert calls == ["f"] * 4 and cache == {}
        assert jobs.wait_idle(0)
    finally:
        jobs.close()


def test_closing_the_jobs_leaves_nothing_to_wait_for(tmp_path, monkeypatch):
    """witness one worker, the job of `f` gated and the job of `g` queued behind it, then
    `close()` and the gate opened - distinguishes 'the cancelled job leaves the running set, so
    wait_idle ends once the running job does and the queued derive never runs' from a cancelled
    future that stays in the set (wait_idle times out)."""
    root, primary, workflow = cache_fixture(tmp_path)
    write_spec(primary, "g", undelivered_spec("g", "feature/g"))
    commit(primary, "g")
    real = traces.load_wf(ROOT)
    calls: list[str] = []
    started, gate = threading.Event(), threading.Event()

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        started.set()
        assert gate.wait(WAIT)
        return real.derive(repo_root, feature, store)

    stub_wf(monkeypatch, derive)
    jobs = traces.PhaseJobs(workers=1)
    try:
        first = jobs_snap(root, workflow, {}, jobs)
        assert started.wait(WAIT)
        assert {row_of(first, s).phase.kind for s in ("f", "g")} == {"computing"}
        jobs.close()
        gate.set()
        assert jobs.wait_idle(5)
    finally:
        gate.set()
        jobs.close()
    assert len(calls) == 1


# --- #66: the phase cache file ------------------------------------------------

def file_cache() -> dict:
    """A cache of one derived and one delivered phase, fingerprints as nested tuples."""
    verdict = {"phase": 0, "status": "pass", "problems": ["a", "b"], "reason": "ok"}
    return {
        (Path("/t/one"), "f"): (("head", (("?? x", 1, 2), ("?? y", None, None))),
                                ((("head2", ()), (3, 4)), None),
                                traces.PhaseView("derived", 3, (verdict,))),
        (Path("/t/two"), "g"): (("head3", ()), (("head4", ()), None),
                                traces.PhaseView("delivered", None, ())),
    }


def entry_of(**changes) -> dict:
    """One well-formed file entry, with `changes` applied (a value `...` drops the key)."""
    entry = {"tree": "/t/ok", "slug": "good", "fingerprint": ["head", []],
             "workflow_fingerprint": [["head", []], None],
             "phase": {"kind": "derived", "phase": 2, "verdicts": [
                 {"phase": 0, "status": "pass", "problems": [], "reason": "ok"}]}}
    for key, value in changes.items():
        if value is ...:
            del entry[key]
        else:
            entry[key] = value
    return entry


def test_a_cache_file_round_trips_the_phases(tmp_path):
    """witness a cache of a derived and a delivered phase with nested-tuple fingerprints, saved to
    a file in a directory that does not exist yet, then loaded - distinguishes a loader that turns
    the JSON arrays back into tuples (equal cache, so equal to a fingerprint taken now) from one
    that leaves lists (a loaded fingerprint would never compare equal)."""
    cache = file_cache()
    path = tmp_path / "new-dir" / "phase-cache.json"
    traces.save_phase_cache(path, cache)
    loaded = traces.load_phase_cache(path)
    assert loaded == cache
    fingerprint, workflow_fp, phase = loaded[(Path("/t/one"), "f")]
    assert isinstance(fingerprint, tuple) and isinstance(fingerprint[1][0], tuple)
    assert isinstance(workflow_fp[0], tuple)
    assert isinstance(phase.verdicts, tuple) and phase.verdicts[0]["problems"] == ["a", "b"]


GOOD_KEY = (Path("/t/ok"), "good")


@pytest.mark.parametrize("name, content", [
    ("missing", None),
    ("a directory in its place", "DIR"),
    ("not JSON", "{ not json"),
    ("a JSON list", "[]"),
    ("another version", json.dumps({"version": 2, "entries": [entry_of()]})),
    ("no version", json.dumps({"entries": [entry_of()]})),
    ("entries not a list", json.dumps({"version": 1, "entries": {}})),
], ids=lambda value: value if len(value or "") < 30 else "content")
def test_a_bad_cache_file_is_an_empty_cache(tmp_path, name, content):
    """witness each broken envelope beside what would be a valid entry - distinguishes 'the whole
    file is ignored and nothing raises' from a loader that raises or keeps the entry."""
    path = tmp_path / "phase-cache.json"
    if content == "DIR":
        path.mkdir()
    elif content is not None:
        path.write_text(content)
    assert traces.load_phase_cache(path) == {}


@pytest.mark.parametrize("bad", [
    entry_of(slug=7, tree="/t/bad"),
    entry_of(tree="/t/bad", fingerprint=None),
    entry_of(tree="/t/bad", workflow_fingerprint=None),
    entry_of(tree="/t/bad", phase={"kind": "computing", "phase": None, "verdicts": []}),
    entry_of(tree="/t/bad", phase={"kind": "derived", "phase": "2", "verdicts": []}),
    entry_of(tree="/t/bad", phase={"kind": "derived", "phase": 2, "verdicts": [
        {"phase": 0, "problems": [], "reason": "ok"}]}),
    entry_of(tree="/t/bad", phase={"kind": "derived", "phase": 2, "verdicts": {}}),
    entry_of(tree="/t/bad", phase=...),
    entry_of(tree=...),
    "not an object",
], ids=["slug not a string", "null fingerprint", "null workflow fingerprint",
        "kind computing", "phase a string", "verdict without status",
        "verdicts not a list", "no phase", "no tree", "entry not an object"])
def test_a_malformed_entry_is_skipped_and_the_others_kept(tmp_path, bad):
    """witness a valid entry and a second valid one beside each kind of malformed one, which has
    a key of its own (a missing key, a null fingerprint, a kind that is not derived/delivered, a
    verdict without `status`, ...) - distinguishes an entry-by-entry check (exactly the two valid
    keys load, with their own values) from an all-or-nothing load or a missing check (the bad
    entry loads under its key, or a key too many)."""
    good = entry_of()
    other = entry_of(tree="/t/other", slug="second")
    path = tmp_path / "phase-cache.json"
    path.write_text(json.dumps({"version": 1, "entries": [bad, good, other]}))
    loaded = traces.load_phase_cache(path)
    assert set(loaded) == {GOOD_KEY, (Path("/t/other"), "second")}
    for entry in (good, other):
        view = loaded[(Path(entry["tree"]), entry["slug"])][2]
        assert (view.kind, view.phase, len(view.verdicts)) == ("derived", 2, 1)


def test_a_cache_file_is_replaced_whole(tmp_path, monkeypatch):
    """witness a save over an existing file, then a save whose `os.replace` raises - distinguishes
    'a temporary file in the same directory renamed over the old one, nothing left behind, the old
    file intact on failure, the error raised' from an in-place write (a half file) or a leftover."""
    path = tmp_path / "phase-cache.json"
    traces.save_phase_cache(path, file_cache())
    assert [p.name for p in tmp_path.iterdir()] == ["phase-cache.json"]
    before = path.read_bytes()

    def refuse(src, dst):
        raise OSError("disk says no")

    monkeypatch.setattr(traces.os, "replace", refuse)
    with pytest.raises(OSError, match="disk says no"):
        traces.save_phase_cache(path, {})
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["phase-cache.json"]


def test_a_save_that_fails_for_any_reason_leaves_no_temporary_file(tmp_path, monkeypatch):
    """witness a save whose `os.replace` raises a RuntimeError (not an OSError) - distinguishes
    'the temporary file is removed and the error propagates' from a cleanup that only covers
    OSError (a `.phase-cache-*` file left in the state directory)."""
    def refuse(src, dst):
        raise RuntimeError("not an OS error")

    monkeypatch.setattr(traces.os, "replace", refuse)
    with pytest.raises(RuntimeError, match="not an OS error"):
        traces.save_phase_cache(tmp_path / "phase-cache.json", file_cache())
    assert list(tmp_path.iterdir()) == []


# --- #91 #95: the base of a repo ------------------------------------------------

FOUR_REFS = ("origin/develop", "origin/HEAD", "origin/main", "origin/master")


def repo_with_refs(tmp_path: Path, refs: tuple[str, ...], head: str | None = None) -> Path:
    """A real repo whose `refs/remotes/origin/<name>` are `refs`, and `origin/HEAD` a symbolic ref
    to `origin/<head>` (which need not exist)."""
    primary = make_repo(tmp_path / "roots", "alpha", "https://host/scm/acme/alpha.git")
    for name in refs:
        git(primary, "update-ref", f"refs/remotes/origin/{name}", "HEAD")
    if head is not None:
        git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{head}")
    return primary


def test_resolve_base_prefers_origin_develop_over_origin_head(tmp_path):
    primary = repo_with_refs(tmp_path, ("develop", "master"), head="master")
    assert traces.resolve_base(primary) == ("origin/develop", None)


def test_resolve_base_uses_origin_head_when_there_is_no_develop(tmp_path):
    primary = repo_with_refs(tmp_path, ("trunk", "main", "master"), head="trunk")
    assert traces.resolve_base(primary) == ("origin/trunk", None)


def test_resolve_base_falls_back_to_origin_main_when_origin_head_is_not_set(tmp_path):
    primary = repo_with_refs(tmp_path, ("main", "master"))
    assert traces.resolve_base(primary) == ("origin/main", None)


def test_resolve_base_falls_back_to_origin_master_when_main_is_missing_too(tmp_path):
    primary = repo_with_refs(tmp_path, ("master",))
    assert traces.resolve_base(primary) == ("origin/master", None)


def test_resolve_base_skips_an_origin_head_that_points_at_a_missing_ref(tmp_path):
    primary = repo_with_refs(tmp_path, ("master",), head="gone")
    assert traces.resolve_base(primary) == ("origin/master", None)


def test_resolve_base_is_none_with_the_reason_naming_the_four_refs_when_none_exists(tmp_path):
    primary = repo_with_refs(tmp_path, ("feature-x",))
    base, note = traces.resolve_base(primary)
    assert base is None
    assert isinstance(note, str) and all(ref in note for ref in FOUR_REFS), note


def test_read_repo_carries_origin_develop_and_no_note_when_develop_exists(tmp_path):
    repo = traces.read_repo(repo_with_refs(tmp_path, ("develop", "master"), head="master"))
    assert (repo.base, repo.base_note) == ("origin/develop", None)


def test_read_repo_has_no_base_and_names_origin_develop_in_the_note_when_none_of_the_four_exists(tmp_path):
    repo = traces.read_repo(repo_with_refs(tmp_path, ("feature-x",)))
    assert repo.base is None
    assert isinstance(repo.base_note, str) and "origin/develop" in repo.base_note


def test_read_repo_has_no_base_and_a_note_when_the_repo_has_no_origin(tmp_path):
    repo = traces.read_repo(make_repo(tmp_path, "solo"))
    assert repo.base is None and isinstance(repo.base_note, str) and repo.base_note


def test_a_repo_with_only_master_keeps_its_base_and_its_ahead(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    git(primary, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    wt = add_worktree(primary, "feat", "feature/feat")
    (wt / "a.txt").write_text("a")
    commit(wt, "ahead")
    where = snap_with_other(root).open[0].where
    assert (where.base, where.ahead, where.base_note) == ("origin/master", 1, None)


def test_a_worktree_merged_into_develop_has_ahead_zero_and_is_removable(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    git(primary, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    (primary / "later.txt").write_text("later")
    commit(primary, "integrated in develop")
    git(primary, "update-ref", "refs/remotes/origin/develop", "HEAD")
    add_worktree(primary, "feat", "feature/feat")
    result = snap_with_other(root)
    where = result.open[0].where
    assert (where.base, where.ahead) == ("origin/develop", 0)
    assert removable_names(result)["feat"] == ["removable"]


def test_the_same_worktree_is_one_ahead_of_master_once_develop_is_gone(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    git(primary, "update-ref", "refs/remotes/origin/master", "HEAD")
    git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    (primary / "later.txt").write_text("later")
    commit(primary, "integrated in develop")
    git(primary, "update-ref", "refs/remotes/origin/develop", "HEAD")
    add_worktree(primary, "feat", "feature/feat")
    git(primary, "update-ref", "-d", "refs/remotes/origin/develop")
    where = snap_with_other(root).open[0].where
    assert (where.base, where.ahead) == ("origin/master", 1)


def test_a_worktree_in_a_repo_with_no_base_says_base_unknown_with_the_note(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    add_worktree(primary, "feat", "feature/feat")
    row = snap_with_other(root).open[0]
    assert "base-unknown" in [s["name"] for s in row.signals]
    assert row.where.base is None and row.where.ahead is None
    assert isinstance(row.where.base_note, str) and all(ref in row.where.base_note for ref in FOUR_REFS)



def test_a_row_whose_git_read_fails_in_a_repo_with_no_base_keeps_the_reason(tmp_path):
    primary = make_repo(tmp_path / "roots", "alpha", "https://host/scm/acme/alpha.git")
    wt = add_worktree(primary, "feat", "feature/feat")
    repo = traces.read_repo(primary)
    assert repo.base is None and repo.base_note
    found = traces.repo_traces(repo, traces.load_wf(ROOT))
    shutil.rmtree(wt)   # the worktree vanishes between discovery and the git read
    rows, _ = traces.group_rows(found, (repo,), traces.load_wf(ROOT))
    row = next(r for r in rows if r.slug == "feat")
    assert row.error and row.where.base is None
    assert row.where.base_note == repo.base_note


# --- #93: a worktree is an Open feature row only when something says it is a feature ------------

def mem_row(slug: str = "release-0.0.23", branch: str | None = "release-0.0.23", *,
            kinds: tuple[str, ...] = ("worktree",), claim: dict | None = None,
            agent: dict | None = None, error: str | None = None) -> "traces.Row":
    """A Row in memory: one trace per kind in `kinds`, a plain name unless a test says otherwise."""
    made = tuple(traces.Trace(kind, "acme/x", slug, f"/w/{slug}", branch, 1.0, "") for kind in kinds)
    return traces.Row(
        id="i" * 12, repo="acme/x", slug=slug, last_activity=1.0,
        phase=traces.PhaseView("no-spec", None, ()),
        where=traces.Where("worktree", f"/w/{slug}", branch, 0, 0, "origin/master"),
        claim=claim, agent=agent, signals=(), traces=made, error=error)


def test_a_row_with_a_spec_trace_alone_is_a_feature_row():
    assert traces.is_feature_row(mem_row(kinds=("spec",))) is True


def test_a_row_with_a_claim_alone_is_a_feature_row():
    assert traces.is_feature_row(mem_row(claim={"text": "c", "since": "2026-11-19T10:00:00+00:00"})) is True


def test_a_row_with_a_live_agent_alone_is_a_feature_row():
    assert traces.is_feature_row(mem_row(agent={"pid": 7, "process": "claude", "since": None})) is True


def test_a_row_with_a_handoff_trace_alone_is_a_feature_row():
    assert traces.is_feature_row(mem_row(kinds=("handoff",))) is True


def test_a_worktree_row_with_no_condition_and_no_ticket_reference_is_not_a_feature_row():
    row = mem_row()
    assert traces.is_feature_row(row) is False
    assert traces.is_other_worktree(row) is True


TICKET_NAMES = ["fix/EXAMPLE-5235-kb-preserve-cells", "feature/EXAMPLE-3894-kb-key-guard",
                "feature/workflow-window-87", "example-5157-s58", "feature/DSF-14643-x"]
PLAIN_NAMES = ["chore/release-0.0.23", "release-0.0.67", "v0.0.68", "backlog/b-038-e2e-tests", "rc",
               "feature/audit-speed", "main", "develop", "chore/bump-0.0.59", "hotfix/0.0.65",
               "7zip-1", "feature/3d-2", "feature/3dx-2"]


@pytest.mark.parametrize("where", ["slug", "branch"])
@pytest.mark.parametrize("name,is_feature",
                         [(n, True) for n in TICKET_NAMES] + [(n, False) for n in PLAIN_NAMES])
def test_the_names_of_the_spec_decide_as_the_spec_says(name, is_feature, where):
    row = mem_row(slug=name, branch="plain") if where == "slug" else mem_row(slug="plain", branch=name)
    assert traces.is_feature_row(row) is is_feature


def test_a_row_with_no_branch_and_a_plain_slug_is_not_a_feature_row():
    assert traces.is_feature_row(mem_row(slug="backlog", branch=None)) is False


def test_an_unreadable_row_stays_open_and_is_not_an_other_worktree():
    row = mem_row(slug="?", branch=None, kinds=(), error="not a git repository")
    assert traces.is_open(row) is True
    assert traces.is_other_worktree(row) is False


def test_a_row_that_is_not_open_is_never_an_other_worktree():
    """No feature condition at all, so only `is_open` can keep a closed row out of the third list."""
    row = replace(mem_row(slug="done", branch=None, kinds=()),
                  phase=traces.PhaseView("delivered", None, ()), where=traces.Where("none", None, None, None, None, None))
    assert traces.is_feature_row(row) is False
    assert traces.is_open(row) is False
    assert traces.is_other_worktree(row) is False


def test_snapshot_lists_a_worktree_with_no_condition_apart_and_never_in_history(tmp_path):
    """Witness: a primary with a worktree on `release-0.0.23`, one on `feature/wt-only` and the
    undelivered spec `wip` - distinguishes 'the worktree-only rows go to a third list' from the old
    rule (both in open) and from 'dropped into history'."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    write_spec(primary, "wip", undelivered_spec("wip", "feature/wip"))
    commit(primary, "specs")
    add_worktree(primary, "release-0.0.23", "release-0.0.23")
    add_worktree(primary, "wt-only", "feature/wt-only")
    result = snap(root)
    assert {r.slug for r in result.other} == {"release-0.0.23", "wt-only"}
    assert [r.slug for r in result.open] == ["wip"]
    assert result.history == ()


def test_a_worktree_row_whose_agent_starts_moves_from_other_to_open(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    tree = add_worktree(primary, "wt-only", "feature/wt-only")
    presence = tmp_path / "presence"
    before = snap(root, presence=presence)
    assert [r.slug for r in before.other] == ["wt-only"] and before.open == ()
    live_record(presence, tree, "wt-only", alive=True)
    after = snap(root, presence=presence)
    assert [r.slug for r in after.open] == ["wt-only"] and after.other == ()
    assert after.open[0].agent["pid"] == os.getpid()


def test_to_json_carries_the_other_worktrees_as_an_additive_key(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    assert traces.to_json(snap(root))["other_worktrees"] == []
    add_worktree(primary, "release-0.0.23", "release-0.0.23")
    wire = traces.to_json(snap(root))
    assert json.loads(json.dumps(wire)) == wire
    assert [r["slug"] for r in wire["other_worktrees"]] == ["release-0.0.23"]
    assert set(wire["other_worktrees"][0]) == {"id", "repo", "slug", "last_activity", "phase", "where",
                                               "claim", "agent", "signals", "traces", "error", "links"}
    assert wire["open"] == [] and wire["history"] == []


def test_the_open_count_of_the_wire_is_the_features_only(tmp_path):
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    add_worktree(primary, "EXAMPLE-5235-kb", "feature/EXAMPLE-5235-kb")
    add_worktree(primary, "release-0.0.23", "release-0.0.23")
    add_worktree(primary, "backlog", "backlog/b-038")
    wire = traces.to_json(snap(root))
    assert [r["slug"] for r in wire["open"]] == ["EXAMPLE-5235-kb"]
    assert len(wire["other_worktrees"]) == 2


# --- steady-rows #111: a refreshing row keeps its last known phase -------------

def seeded_stale(tmp_path, monkeypatch, old: traces.PhaseView, *, derive_error: str | None = None):
    """A cache fixture whose `f` entry holds `old` under fingerprints that no longer match
    (the tree was edited after it was stored), with a gated derive that returns the real phase
    (or raises `derive_error`) once `gate` is set. Returns (root, workflow, cache, jobs, gate,
    calls); the caller closes `jobs`."""
    root, primary, workflow = cache_fixture(tmp_path)
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=2)
    counting_wf(monkeypatch)
    jobs_snap(root, workflow, cache, jobs)
    assert jobs.wait_idle(WAIT)
    ((key, (fingerprint, workflow_fp, _)),) = cache.items()
    cache[key] = (fingerprint, workflow_fp, old)
    (primary / "src.txt").write_text("edited\n")   # the tree fingerprint no longer matches
    real = traces.load_wf(ROOT)
    calls: list[str] = []
    gate = threading.Event()

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        assert gate.wait(WAIT)
        if derive_error is not None:
            raise RuntimeError(derive_error)
        return real.derive(repo_root, feature, store)

    stub_wf(monkeypatch, derive)
    return root, workflow, cache, jobs, gate, calls


def test_a_cache_file_with_the_old_install_fingerprint_loads_and_refreshes(tmp_path, monkeypatch):
    """witness a version-1 cache file whose entry carries the pre-#122 `workflow_fingerprint`
    ([[HEAD, [[status, mtime, size]]], store_stamp]) - distinguishes 'loaded and mismatched' (the
    row answers its phase refreshing, then derives again) from 'dropped as malformed' (the row
    would answer computing) and from 'matched' (no derive)."""
    root, _, workflow = cache_fixture(tmp_path)
    cache: dict = {}
    jobs = traces.PhaseJobs(workers=2)
    calls = counting_wf(monkeypatch)
    jobs_snap(root, workflow, cache, jobs)
    assert jobs.wait_idle(WAIT)
    ((key, (fingerprint, _, _)),) = cache.items()
    calls.clear()
    old_shape = ["a1b2c3", [["M wf.py", 1700000000000000000, 10]]], [1700000000000000000, 20]
    entry = {"tree": str(key[0]), "slug": key[1], "fingerprint": fingerprint,
             "workflow_fingerprint": old_shape,
             "phase": {"kind": "delivered", "phase": 3, "verdicts": []}}
    path = tmp_path / "phase-cache.json"
    path.write_text(json.dumps({"version": traces.CACHE_VERSION, "entries": [entry]}))
    loaded = traces.load_phase_cache(path)
    assert list(loaded) == [key]
    try:
        row = row_of(jobs_snap(root, workflow, loaded, jobs), "f")
        assert (row.phase.kind, row.phase.phase, row.phase.refreshing) == ("delivered", 3, True)
        assert jobs.wait_idle(WAIT)
        assert calls == ["f"]
        assert row_of(jobs_snap(root, workflow, loaded, jobs), "f").phase.refreshing is False
    finally:
        jobs.close()


def test_a_stale_entry_answers_its_phase_refreshing(tmp_path, monkeypatch):
    """witness a stale entry of kind `delivered` and phase 3 while the real phase is derived 0,
    job gated - distinguishes 'the last known kind and phase marked refreshing' from `computing`
    (the old answer) and from the new phase; a fresh hit afterwards answers refreshing false."""
    old = traces.PhaseView("delivered", 3, ())
    root, workflow, cache, jobs, gate, _ = seeded_stale(tmp_path, monkeypatch, old)
    try:
        row = row_of(jobs_snap(root, workflow, cache, jobs), "f")
        assert (row.phase.kind, row.phase.phase, row.phase.refreshing) == ("delivered", 3, True)
        gate.set()
        assert jobs.wait_idle(WAIT)
        fresh = row_of(jobs_snap(root, workflow, cache, jobs), "f")
        assert fresh.phase.refreshing is False
    finally:
        gate.set()
        jobs.close()


def test_a_refreshing_delivered_row_stays_in_history(tmp_path, monkeypatch):
    """witness a stale `delivered` entry (not `derived`: `derived` is Open either way and would
    not tell 'placed by kind' from 'refreshing is Open'), and a never-derived spec `g` -
    distinguishes history for the refreshing row from open, and `computing` open for `g`."""
    old = traces.PhaseView("delivered", None, ())
    root, workflow, cache, jobs, gate, _ = seeded_stale(tmp_path, monkeypatch, old)
    primary = root / "alpha"
    try:
        write_spec(primary, "g", undelivered_spec("g", "feature/g"))
        commit(primary, "g")
        # the commit changed HEAD: f is stale again, and g was never derived
        result = jobs_snap(root, workflow, cache, jobs)
        assert [r.slug for r in result.history] == ["f"]
        assert result.history[0].phase.refreshing is True
        g = row_of(result, "g")
        assert g in result.open and (g.phase.kind, g.phase.refreshing) == ("computing", False)
    finally:
        gate.set()
        jobs.close()


def test_a_refreshing_phase_is_replaced_when_its_job_ends(tmp_path, monkeypatch):
    """witness a stale entry of phase 3 and a real phase derived 0 - distinguishes 'the answer
    after the job is the new phase 0, not refreshing' from keeping the old 3, and derive is
    called once, not at every scan."""
    old = traces.PhaseView("derived", 3, ())
    root, workflow, cache, jobs, gate, calls = seeded_stale(tmp_path, monkeypatch, old)
    try:
        assert row_of(jobs_snap(root, workflow, cache, jobs), "f").phase.refreshing is True
        assert row_of(jobs_snap(root, workflow, cache, jobs), "f").phase.refreshing is True
        gate.set()
        assert jobs.wait_idle(WAIT)
        row = row_of(jobs_snap(root, workflow, cache, jobs), "f")
    finally:
        gate.set()
        jobs.close()
    assert (row.phase.kind, row.phase.phase, row.phase.refreshing) == ("derived", 0, False)
    assert calls == ["f"]


def test_a_failing_refresh_shows_the_error(tmp_path, monkeypatch):
    """witness a stale entry present while `derive` raises - distinguishes 'the held error wins
    over the last known phase' (no-spec plus `phase: boom`) from the old phase kept."""
    old = traces.PhaseView("delivered", 3, ())
    root, workflow, cache, jobs, gate, _ = seeded_stale(tmp_path, monkeypatch, old,
                                                        derive_error="boom")
    try:
        assert row_of(jobs_snap(root, workflow, cache, jobs), "f").phase.refreshing is True
        gate.set()
        assert jobs.wait_idle(WAIT)
        row = row_of(jobs_snap(root, workflow, cache, jobs), "f")
    finally:
        gate.set()
        jobs.close()
    assert row.phase.kind == "no-spec" and row.phase.refreshing is False
    assert row.error is not None and "phase: boom" in row.error


def test_the_cache_never_stores_refreshing(tmp_path, monkeypatch):
    """witness a full refresh cycle - distinguishes a cache of fresh phases only (no entry with
    refreshing true) and a saved file whose phase objects have exactly kind, phase, verdicts
    from one that persisted the flag."""
    old = traces.PhaseView("derived", 3, ())
    root, workflow, cache, jobs, gate, _ = seeded_stale(tmp_path, monkeypatch, old)
    try:
        assert row_of(jobs_snap(root, workflow, cache, jobs), "f").phase.refreshing is True
        gate.set()
        assert jobs.wait_idle(WAIT)
    finally:
        gate.set()
        jobs.close()
    assert cache and all(entry[2].refreshing is False for entry in cache.values())
    path = tmp_path / "cache.json"
    traces.save_phase_cache(path, cache)
    phases = [e["phase"] for e in json.loads(path.read_text())["entries"]]
    assert phases and all(set(p) == {"kind", "phase", "verdicts"} for p in phases)


def test_row_json_carries_refreshing(tmp_path):
    """witness a fresh, a refreshing, a computing and a no-spec phase on one row each -
    distinguishes the key present and boolean (true only for the refreshing one) from a key
    missing or derived from the kind."""
    wire = {}
    for name, phase in (("fresh", traces.PhaseView("derived", 1, ())),
                        ("refreshing", traces.PhaseView("delivered", None, (), refreshing=True)),
                        ("computing", traces.COMPUTING),
                        ("no-spec", traces.PhaseView("no-spec", None, ()))):
        root = tmp_path / name
        primary = make_repo(root, "alpha")
        write_claim(primary, "c")
        row = replace(row_of(snap(root), "c"), phase=phase)
        wire[name] = traces.row_json(row)["phase"]["refreshing"]
    assert wire == {"fresh": False, "refreshing": True, "computing": False, "no-spec": False}
