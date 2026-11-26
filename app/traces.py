"""The projection behind the window: repos -> traces -> feature rows -> open / history.

Read-only by construction: git is only asked questions (never `status` without
optional locks disabled), repo and workflow files are only read. The one write is
`save_phase_cache`, into the desk's own state directory, never under a repo.
`snapshot()` is the entry point.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Literal, TypeVar
from urllib.parse import quote, urlsplit

# The repo of a handoff that names none (a legacy one); `null` on the wire.
REPO_UNKNOWN = "unknown"

T = TypeVar("T")

# Resolved repo_root -> (slug, recorded_at) of the tree's binding (Decision 10, #35).
Bindings = Mapping[Path, tuple[str, str]]

TraceKind = Literal["worktree", "claim", "spec", "handoff", "presence"]

# Decision 7: the five signals, each citing the rule it reads (a closed set).
SIGNAL_RULES: dict[str, str] = {
    "removable": "SESSION-ISOLATION.md § Cleanup at session end",
    "base-unknown": "SESSION-ISOLATION.md § Cleanup at session end (no base to prove reachability)",
    "claim-no-agent": "SESSION-ISOLATION.md § Claims",
    "phase-fail": "wf.py status",
    "handoff-missing": "WORKFLOW.md § General rules (I5: a handoff per session; wf.py end)",
}

# The text of each pill (the wording of the approved mockup); the rule is its tooltip.
SIGNAL_LABELS: dict[str, str] = {
    "removable": "removable",
    "base-unknown": "base unknown",
    "claim-no-agent": "claim, no agent recorded",
    "phase-fail": "phase failing",
    "handoff-missing": "handoff missing",
}


class GitError(Exception):
    """message = "git <args>: exit <n>: <stderr tail>"."""


@dataclass(frozen=True)
class SourceError:
    source: str
    message: str


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None
    head: str


@dataclass(frozen=True)
class Repo:
    primary: Path
    identity: str
    base: str | None
    worktrees: tuple[Worktree, ...]
    origin: str | None = None   # the raw origin URL; read by pr_link, never serialized (Decision 17)
    base_note: str | None = None   # why `base` is None (#95); None when a base was found


@dataclass(frozen=True)
class Trace:
    kind: TraceKind
    repo: str
    slug: str
    path: str
    branch: str | None
    at: float
    text: str
    bound_at: str | None = None   # recorded_at of the binding of the trace's tree (#35)
    tracker: str | None = None    # spec traces: the front matter `tracker:` (#26)


@dataclass(frozen=True)
class Where:
    kind: Literal["worktree", "primary", "none"]
    path: str | None
    branch: str | None
    dirty: int | None
    ahead: int | None
    base: str | None
    base_note: str | None = None   # why `base` is None (#95), carried from the Repo


@dataclass(frozen=True)
class PhaseView:
    kind: Literal["derived", "delivered", "no-spec", "computing"]
    phase: int | None
    verdicts: tuple[dict, ...]
    refreshing: bool = False   # #111: the last known phase while its check runs again


COMPUTING = PhaseView("computing", None, ())   # a background job is deriving this phase (#65)


@dataclass(frozen=True)
class Row:
    id: str
    repo: str
    slug: str
    last_activity: float
    phase: PhaseView
    where: Where
    claim: dict | None
    agent: dict | None
    signals: tuple[dict, ...]
    traces: tuple[Trace, ...]
    error: str | None
    trees: tuple[Where, ...] = ()   # the row's other worktrees (Decision 12, #35)
    links: dict | None = None       # {pr, ticket, tracker}, built by group_rows (Decision 17, #26)


@dataclass(frozen=True)
class Snapshot:
    generated_at: str
    partial: bool
    errors: tuple[SourceError, ...]
    open: tuple[Row, ...]
    history: tuple[Row, ...]
    primaries: tuple[tuple[str, str], ...] = ()   # (repo identity, primary path); never serialized (#28)
    other: tuple[Row, ...] = ()                   # open worktree rows that are no feature (#93)


# (spec tree, slug) -> (tree fingerprint, workflow fingerprint, phase); in memory, and in the phase
# cache file when the server has one (#66, Decision 13 decides what may enter either).
# With jobs the cache is read and written only under PhaseJobs's lock.
PhaseCache = dict[tuple[Path, str], tuple[tuple | None, tuple | None, "PhaseView"]]


def snapshot(roots: tuple[Path, ...], workflow_dir: Path, now: datetime,
             presence_dir: Path | None = None, cache: PhaseCache | None = None,
             jira_url: str | None = None, store: Path | None = None,
             jobs: PhaseJobs | None = None) -> Snapshot:
    """One scan of every repo under `roots`. A repo that cannot be read becomes a
    SourceError (the snapshot is then `partial`); a failure of the scan itself
    (wf.py not loadable) raises."""
    # Covers the synchronous scan, so also a wf.derive run in this thread (it loads audit.py
    # lazily); the jobs run in pool threads, where DeskServer's process-wide flag covers them.
    with no_bytecode():
        wf = load_wf(workflow_dir)
        repos, found = discover_repos(roots)
        errors = list(found)
        # A root that cannot be listed stays a SourceError; a repo that cannot be read
        # (here or below) is also a row, so the failure is in the list, not only a banner.
        unreadable: list[Row] = [
            unreadable_repo_row(f"local:{e.source}", Path(e.source), None, e.message)
            for e in found if Path(e.source) not in roots]
        try:   # read before the scan: the bindings name each worktree's feature (Decision 10)
            present = wf.read_presence(presence_dir or wf.default_presence_dir())
            odd = next((r for r in present if not has_offset(r.recorded_at)), None)
            if odd is not None:   # an unorderable stamp is as malformed as an unreadable file
                raise ValueError(f"{presence_dir or wf.default_presence_dir()}: malformed presence "
                                 f"record (recorded_at {odd.recorded_at!r} is not an ISO-8601 "
                                 "instant with an offset)")
        except ValueError as error:   # a malformed record: no binding, no agent, the failure is listed
            source, _, tail = str(error).partition(": malformed presence record")
            errors.append(SourceError(source, "malformed presence record" + tail))
            present = ()
        bindings = tree_bindings(present)
        handoffs, handoff_errors = handoff_traces(workflow_dir)
        errors += handoff_errors
        all_traces: list[Trace] = list(handoffs)
        usable: list[Repo] = []
        bad_by_key: dict[tuple[str, str], tuple[Repo, list[str]]] = {}
        for repo in repos:
            try:
                found_traces, bad_specs = scan_repo(repo, wf, bindings)
            except (GitError, OSError) as error:
                errors.append(SourceError(str(repo.primary), str(error)))
                unreadable.append(unreadable_repo_row(repo.identity, repo.primary, repo.base,
                                                      str(error)))
            else:
                all_traces += found_traces
                errors += bad_specs
                usable.append(repo)
                for bad in bad_specs:
                    key = (repo.identity, Path(bad.source).stem)
                    bad_by_key.setdefault(key, (repo, []))[1].append(bad.message)
        workflow_fp = workflow_fingerprint(workflow_dir, store) if cache is not None else None
        rows, row_errors = group_rows(all_traces, tuple(usable), wf,
                                      cache, workflow_fp, jira_url, store, jobs)
    errors += row_errors
    rows = merge_handoff_errors(merge_bad_specs(rows, bad_by_key) + unreadable, handoff_errors)
    rows = [replace(r, signals=row_signals(r)) for r in attach_agents(rows, present)]
    newest_first = sorted(rows, key=lambda r: r.last_activity, reverse=True)
    return Snapshot(
        generated_at=now.isoformat(),
        partial=bool(errors),
        errors=tuple(errors),
        open=tuple(r for r in newest_first if is_open(r) and not is_other_worktree(r)),
        history=tuple(r for r in newest_first if not is_open(r)),
        primaries=tuple((r.identity, str(r.primary)) for r in repos),
        other=tuple(r for r in newest_first if is_other_worktree(r)),
    )


def error_row(repo: str, slug: str, path: Path, base: str | None, message: str) -> Row:
    """A row for a source that cannot be read: the error, no traces, no phase."""
    try:
        at = path.stat().st_mtime
    except OSError:
        at = 0.0
    return Row(
        id=hashlib.sha1(f"{repo}\0{slug}".encode()).hexdigest()[:12],
        repo=repo, slug=slug, last_activity=at, phase=PhaseView("no-spec", None, ()),
        where=Where("primary", str(path), None, None, None, base),
        claim=None, agent=None, signals=(), traces=(), error=message)


def unreadable_repo_row(repo: str, path: Path, base: str | None, message: str) -> Row:
    """The row for a repo that cannot be read. Two clones share one identity, so the
    path joins the id; it is never merged with the rows of readable specs."""
    row = error_row(repo, "?", path, base, message)
    return replace(row, id=hashlib.sha1(f"{repo}\0?\0{path}".encode()).hexdigest()[:12])


def merge_bad_specs(rows: list[Row], bad: dict[tuple[str, str], tuple[Repo, list[str]]]) -> list[Row]:
    """Each spec file that cannot be read lands on the row of its exact (repo, slug),
    creating that row when no other trace has it; its phase is then unknown."""
    by_key = {(r.repo, r.slug): r for r in rows}
    for (identity, slug), (repo, messages) in bad.items():
        text = "; ".join(messages)
        row = by_key.get((identity, slug))
        if row is None:
            by_key[(identity, slug)] = error_row(identity, slug, repo.primary, repo.base,
                                                 text)
        else:
            joined = "; ".join(filter(None, (row.error, text)))
            by_key[(identity, slug)] = replace(row, error=joined,
                                               phase=PhaseView("no-spec", None, ()))
    return list(by_key.values())


@contextmanager
def no_bytecode() -> Iterator[None]:
    """Importing by path must not write `__pycache__` next to the sources: the
    workflow dir may sit under a discovery root, and the app writes nothing there."""
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = before


_REPORT_STORE: ModuleType | None = None
REPORT_STORE_PATH = Path(__file__).resolve().parent.parent / "report_store.py"


def load_report_store() -> ModuleType:
    """`report_store.py` of this install (the parent of `app/`), loaded by path once, the way
    `load_wf` loads `wf.py`; the one door of the window to the report store."""
    global _REPORT_STORE
    if _REPORT_STORE is None:
        found = importlib.util.spec_from_file_location("report_store_desk", REPORT_STORE_PATH)
        module = importlib.util.module_from_spec(found)
        with no_bytecode():
            found.loader.exec_module(module)
        _REPORT_STORE = module
    return _REPORT_STORE


def load_wf(workflow_dir: Path) -> ModuleType:
    """`wf.py` of the workflow dir, loaded by path (the way wf._audit loads audit.py)."""
    found = importlib.util.spec_from_file_location("wf_desk", workflow_dir / "wf.py")
    module = importlib.util.module_from_spec(found)
    sys.modules["wf_desk"] = module  # dataclasses resolve their module through sys.modules
    try:
        with no_bytecode():
            found.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("wf_desk", None)
        raise
    return module


# --- discovery --------------------------------------------------------------

def discover_repos(roots: tuple[Path, ...]) -> tuple[tuple[Repo, ...], tuple[SourceError, ...]]:
    """Primary trees: direct children of a root whose `.git` is a directory (a
    linked worktree has a `.git` file and is found through its primary)."""
    repos: list[Repo] = []
    errors: list[SourceError] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            children = sorted(root.iterdir())
        except OSError as error:
            errors.append(SourceError(str(root), str(error)))
            continue
        for child in children:
            try:
                if child.is_symlink() or not (child / ".git").is_dir():
                    continue
            except OSError:
                continue  # a directory we may not enter is not a known repo
            if child.resolve() in seen:
                continue
            seen.add(child.resolve())
            try:
                repos.append(read_repo(child))
            except GitError as error:
                errors.append(SourceError(str(child), str(error)))
    return tuple(repos), tuple(errors)


def git_out(cwd: Path, *args: str) -> str:
    """stdout of `git <args>` run in `cwd`; GitError on a non-zero exit."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env["GIT_OPTIONAL_LOCKS"] = "0"                    # a read never refreshes the index
    env["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)   # a broken repo is not its parent's
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              errors="replace", env=env, check=False)
    except OSError as error:
        raise GitError(f"git {' '.join(args)}: {error}") from error
    if done.returncode:
        tail = " ".join(done.stderr.strip().splitlines()[-2:])
        raise GitError(f"git {' '.join(args)}: exit {done.returncode}: {tail}")
    return done.stdout


NO_BASE_NOTE = "no origin/develop, origin/HEAD, origin/main or origin/master"


def ref_exists(primary: Path, ref: str) -> bool:
    try:
        git_out(primary, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}")
    except GitError:
        return False
    return True


def resolve_base(primary: Path) -> tuple[str | None, str | None]:
    """The branch the work is merged into (#91): origin/develop, else the target of origin/HEAD
    (when that ref exists), else origin/main, else origin/master; local reads only. Returns
    (base, None), or (None, the reason) when none exists (#95)."""
    if ref_exists(primary, "origin/develop"):
        return "origin/develop", None
    try:
        head = git_out(primary, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    except GitError:
        head = ""
    if head and ref_exists(primary, head):
        return head, None
    for fallback in ("origin/main", "origin/master"):
        if ref_exists(primary, fallback):
            return fallback, None
    return None, NO_BASE_NOTE


def read_repo(primary: Path) -> Repo:
    """Identity from `origin`, base from `resolve_base`, linked worktrees from
    `git worktree list --porcelain`. Raises GitError when the worktree list fails."""
    listing = git_out(primary, "worktree", "list", "--porcelain")
    try:
        url: str | None = git_out(primary, "remote", "get-url", "origin").strip() or None
    except GitError:
        url = None
    base, base_note = resolve_base(primary)
    return Repo(primary, repo_identity(url, primary), base, linked_worktrees(listing), url, base_note)


def linked_worktrees(porcelain: str) -> tuple[Worktree, ...]:
    """The blocks of `worktree list --porcelain` after the first (the primary)."""
    found: list[Worktree] = []
    for block in porcelain.strip().split("\n\n")[1:]:
        fields = dict(line.partition(" ")[::2] for line in block.splitlines())
        if "worktree" not in fields:
            continue
        ref = fields.get("branch")
        branch = ref.removeprefix("refs/heads/") if ref else None
        found.append(Worktree(Path(fields["worktree"]), branch, fields.get("HEAD", "")))
    return tuple(found)


def repo_identity(url: str | None, primary: Path) -> str:
    """Case-folded `owner/name` of an origin URL, else `local:<primary>`."""
    if url:
        text = url.strip().rstrip("/").removesuffix(".git")
        if "://" in text:
            try:
                text = urlsplit(text).path
            except ValueError:  # e.g. an unbalanced `[` in the host
                return f"local:{primary}"
        elif ":" in text:
            text = text.split(":", 1)[1]
        parts = [p for p in text.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}".lower()
    return f"local:{primary}"


# --- traces -----------------------------------------------------------------

def worktree_slug(worktree: Path, primary: Path) -> str:
    """The directory name minus `<primary-dir>-`; the whole name without that prefix."""
    name = worktree.name
    return name.removeprefix(primary.name + "-") or name


def has_offset(stamp: str) -> bool:
    """True when `stamp` is an ISO-8601 instant with an offset (so stamps compare as instants)."""
    try:
        return datetime.fromisoformat(stamp).utcoffset() is not None
    except ValueError:
        return False


def newest(items: Iterable[T], stamp: Callable[[T], str]) -> T:
    """The item whose ISO-8601 `stamp` is the latest instant (`datetime.fromisoformat`, so
    offsets and DST changes order correctly, never the strings). On equal instants the first
    item in iteration order wins. Raises ValueError when `items` is empty, or when a stamp is
    not an instant with an offset (callers validate with `has_offset` first)."""
    return max(items, key=lambda item: datetime.fromisoformat(stamp(item)))


def spec_tree(trace: Trace) -> Path:
    """The tree a spec trace lives in: `<tree>/docs/specs/<feature>.md`."""
    return Path(trace.path).parents[2]


def tree_bindings(presence: tuple) -> Bindings:
    """Resolved repo_root -> (slug, recorded_at) of its newest record (Decision 10). A dead
    record binds too: the binding outlives its agent. Records are sorted once by instant; on
    equal instants the later record in `presence` wins. Every `recorded_at` must pass
    `has_offset` (`snapshot` checks once, before this)."""
    ordered = sorted(presence, key=lambda r: datetime.fromisoformat(r.recorded_at))
    return {Path(r.repo_root).resolve(): (r.slug, r.recorded_at) for r in ordered}


def feature_tree(members: list[Trace]) -> tuple[Trace | None, Trace | None]:
    """(chosen spec copy, the row's worktree trace), Decision 11. The copy: the bound one with
    the newest `bound_at`, else one inside a linked worktree (bound by branch), else the
    primary's. The worktree: the one in the copy's tree, else the bound one with the newest
    `bound_at`, else the first."""
    worktrees = [t for t in members if t.kind == "worktree"]
    specs = [t for t in members if t.kind == "spec"]
    bound_specs = [t for t in specs if t.bound_at]
    in_worktree = {t.path for t in worktrees}
    spec = (newest(bound_specs, lambda t: t.bound_at) if bound_specs
            else next((t for t in specs if str(spec_tree(t)) in in_worktree),
                      specs[0] if specs else None))
    bound_trees = [t for t in worktrees if t.bound_at]
    worktree = (next((t for t in worktrees if spec and t.path == str(spec_tree(spec))), None)
                or (newest(bound_trees, lambda t: t.bound_at) if bound_trees else None)
                or (worktrees[0] if worktrees else None))
    return spec, worktree


def repo_traces(repo: Repo, wf: ModuleType) -> list[Trace]:
    """Worktree, claim and spec traces of one repo (Decision 3 for the specs);
    a spec that cannot be read is left out (`scan_repo` also reports it)."""
    return scan_repo(repo, wf, {})[0]


def scan_repo(repo: Repo, wf: ModuleType, bindings: Bindings) -> tuple[list[Trace], list[SourceError]]:
    """`repo_traces` plus a SourceError naming each spec file that cannot be read. `bindings`
    (Decision 10) names the feature of each bound tree: its worktree trace takes that slug,
    and the tree also contributes that feature's spec whatever its `branch:`."""
    found: list[Trace] = []
    unreadable: list[SourceError] = []

    def specs_of(tree_path: Path) -> list[tuple[Path, dict[str, str]]]:
        listed: list[tuple[Path, dict[str, str]]] = []
        for file in sorted((tree_path / "docs" / "specs").glob("*.md")):
            try:
                front = spec_front(file, wf)
            except OSError as error:
                unreadable.append(SourceError(str(file), str(error)))
                continue
            if front.get("feature") == file.stem:
                listed.append((file, front))
        return listed

    # A detached worktree with no binding has no feature to name: its specs are not read.
    trees = [(repo.primary, None, True), *((w.path, w.branch, False) for w in repo.worktrees)]
    fronts = {path: specs_of(path) for path, branch, is_primary in trees
              if is_primary or branch or path.resolve() in bindings}
    for tree in repo.worktrees:
        # A worktree whose directory is gone has no commit to read; its row shows the git error.
        at = float(git_out(tree.path, "log", "-1", "--format=%ct", "HEAD").strip() or 0) \
            if tree.path.is_dir() else 0.0
        bound = bindings.get(tree.path.resolve())
        by_branch = next((f.stem for f, front in fronts.get(tree.path, ())
                          if tree.branch and front.get("branch") == tree.branch), None)
        slug = bound[0] if bound else by_branch or worktree_slug(tree.path, repo.primary)
        found.append(Trace("worktree", repo.identity, slug, str(tree.path), tree.branch, at, "",
                           bound[1] if bound else None))
    claims = repo.primary / ".git" / "agentic-sessions"
    if claims.is_dir():
        for file in sorted(claims.iterdir()):
            if file.is_file():
                found.append(Trace("claim", repo.identity, file.name, str(file), None,
                                   file.stat().st_mtime, file.read_text(errors="replace")))
    for tree_path, branch, is_primary in trees:
        bound = bindings.get(tree_path.resolve())
        for file, front in fronts.get(tree_path, ()):
            is_bound_copy = bool(bound) and file.stem == bound[0]
            if not (is_primary or is_bound_copy or (branch and front.get("branch") == branch)):
                continue
            found.append(Trace("spec", repo.identity, file.stem, str(file), front.get("branch"),
                               file.stat().st_mtime, "", bound[1] if is_bound_copy else None,
                               front.get("tracker")))
    return found, unreadable


def spec_front(file: Path, wf: ModuleType) -> dict[str, str]:
    try:
        return wf.parse_front_matter(file.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise OSError(f"{file}: not valid UTF-8 ({error.reason})") from error


# --- rows -------------------------------------------------------------------

def group_rows(traces: list[Trace], repos: tuple[Repo, ...], wf: ModuleType,
               cache: PhaseCache | None = None,
               workflow_fp: tuple | None = None,
               jira_url: str | None = None,
               store: Path | None = None,
               jobs: PhaseJobs | None = None) -> tuple[list[Row], list[SourceError]]:
    """One row per exact (repo, slug). A phase or git failure of one row is on
    that row (`error`) and in the returned SourceErrors; the others are intact."""
    groups: dict[tuple[str, str], list[Trace]] = {}
    for trace in traces:
        groups.setdefault((trace.repo, trace.slug), []).append(trace)
    by_identity: dict[str, Repo] = {}
    for repo in repos:
        by_identity.setdefault(repo.identity, repo)
    primary_where: dict[Path, Where | GitError] = {}
    used: set[tuple[Path, str]] = set()   # the cache keys of this scan (Decision 13)
    rows: list[Row] = []
    errors: list[SourceError] = []
    for (identity, slug), members in groups.items():
        repo = by_identity.get(identity)   # None: handoffs of a repo that is not on disk
        spec, worktree = feature_tree(members)
        claims = sorted((t for t in members if t.kind == "claim"), key=lambda t: t.at)
        problems: list[str] = []
        if spec is not None:
            used.add(cache_key(spec))
        try:
            if jobs is not None and cache is not None:   # #65: a miss is COMPUTING, a job derives it
                phase = jobs.phase(spec, wf, cache, workflow_fp, store)
            else:
                phase = cached_phase(spec, wf, cache, workflow_fp, store)
        except (OSError, ValueError, RuntimeError) as error:
            phase = PhaseView("no-spec", None, ())
            problems.append(f"phase: {error}")
        try:
            where = (Where("none", None, None, None, None, None)
                     if repo is None or all(t.kind == "handoff" for t in members)
                     else located(worktree, repo, primary_where))
        except GitError as error:
            where = (Where("worktree", worktree.path, worktree.branch, None, None, repo.base, repo.base_note)
                     if worktree else Where("primary", str(repo.primary), None, None, None, repo.base, repo.base_note))
            problems.append(str(error))
        if problems:
            errors.append(SourceError((worktree or spec or members[0]).path, "; ".join(problems)))
        others: list[Where] = []
        for other in (t for t in members if t.kind == "worktree" and t is not worktree and repo):
            try:
                others.append(row_where(other, repo))
            except GitError as error:   # the row keeps its own `where`
                problems.append(str(error))
                errors.append(SourceError(other.path, str(error)))
        rows.append(Row(
            id=hashlib.sha1(f"{identity}\0{slug}".encode()).hexdigest()[:12],
            repo=identity, slug=slug, last_activity=max(t.at for t in members),
            phase=phase, where=where,
            claim={"since": iso(claims[-1].at), "text": claims[-1].text} if claims else None,
            agent=None, signals=(), traces=tuple(members),
            error="; ".join(problems) or None, trees=tuple(others),
            links=row_links(spec, where, repo, jira_url)))
    if cache is not None:
        if jobs is not None:
            jobs.prune(cache, used)
        else:
            for stale in set(cache) - used:
                del cache[stale]
    return rows, errors


def row_links(spec: Trace | None, where: Where, repo: Repo | None,
              jira_url: str | None) -> dict:
    """{pr, ticket, tracker}: the PR page of the branch the work is on (the worktree's, else the
    spec's `branch:`), the ticket of the spec's `tracker:` (Decision 17)."""
    branch = where.branch if where.kind == "worktree" else spec.branch if spec else None
    tracker = spec.tracker if spec else None
    return {"pr": pr_link(repo.origin if repo else None, branch),
            "ticket": ticket_link(tracker, jira_url), "tracker": tracker}


def located(worktree: Trace | None, repo: Repo, cache: dict[Path, Where | GitError]) -> Where:
    """row_where, asking git once per primary tree however many rows live there."""
    if worktree is not None:
        return row_where(worktree, repo)
    if repo.primary not in cache:
        try:
            cache[repo.primary] = row_where(None, repo)
        except GitError as error:
            cache[repo.primary] = error
    answer = cache[repo.primary]
    if isinstance(answer, GitError):
        raise answer
    return answer


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat()


def row_phase(spec: Trace | None, wf: ModuleType, store: Path | None = None) -> PhaseView:
    """`wf.derive` on the tree that holds the chosen spec copy (Decision 11), with the store
    `wf.py next` would read (None: no evidence, so phase 6 has none)."""
    if spec is None:
        return PhaseView("no-spec", None, ())
    path = Path(spec.path)
    derived = wf.derive(spec_tree(spec), path.stem, store)
    verdicts = tuple({"phase": v.phase, "status": v.status, "problems": list(v.problems),
                      "reason": v.reason} for v in derived.verdicts)
    if derived.phase is None:
        return PhaseView("delivered", None, verdicts)
    return PhaseView("derived", derived.phase, verdicts)


def stat_entry(tree: Path, entry: str) -> tuple[str, int | None, int | None]:
    """A porcelain entry with the mtime and size of its path (None when it is gone)."""
    try:
        seen = (tree / entry[3:]).lstat()
    except OSError:
        return entry, None, None
    return entry, seen.st_mtime_ns, seen.st_size


def tree_fingerprint(tree: Path) -> tuple | None:
    """(HEAD, ((status entry, mtime_ns, size), ...)); None when git fails. Raises nothing."""
    try:
        head = git_out(tree, "rev-parse", "HEAD").strip()
        listed = git_out(tree, "status", "--porcelain", "-z", "--untracked-files=all")
    except GitError:
        return None
    tokens = listed.split("\0")
    entries: list[str] = []
    skip = False
    for token in tokens:
        if skip:   # the origin path of a rename or copy
            skip = False
        elif token:
            entries.append(token)
            skip = token[0] in "RC" or token[1:2] in ("R", "C")
    return head, tuple(stat_entry(tree, entry) for entry in entries)


def store_stamp(store: Path | None) -> tuple[int, int] | None:
    """(mtime_ns, size) of the store file; None when there is none."""
    try:
        seen = store.stat() if store is not None else None
    except OSError:
        return None
    return None if seen is None else (seen.st_mtime_ns, seen.st_size)


# The install paths `wf.derive` reads (a directory stands for every file under it); the one list the
# install fingerprint hashes and `test_derive_reads_only_the_listed_install_files` checks.
DERIVE_INPUTS: tuple[str, ...] = ("wf.py", "audit.py", "report_store.py", "guards")


def install_fingerprint(workflow_dir: Path) -> tuple[tuple[str, str | None], ...]:
    """One (path relative to the install, sha256 hex) per file of `DERIVE_INPUTS`, sorted by path;
    a listed file or directory that is missing or unreadable is (path, None). `__pycache__` is
    left out. No git, so an edit elsewhere in the install (handoffs, experiments, a new file)
    leaves it equal. Raises nothing."""
    files: list[str] = []
    for name in DERIVE_INPUTS:
        path = workflow_dir / name
        if path.is_dir():
            files.extend(found.relative_to(workflow_dir).as_posix() for found in path.rglob("*")
                         if found.is_file() and "__pycache__" not in found.parts)
        else:
            files.append(name)
    entries: list[tuple[str, str | None]] = []
    for rel in sorted(files):
        try:
            entries.append((rel, hashlib.sha256((workflow_dir / rel).read_bytes()).hexdigest()))
        except OSError:
            entries.append((rel, None))
    return tuple(entries)


def workflow_fingerprint(workflow_dir: Path, store: Path | None = None) -> tuple | None:
    """(`install_fingerprint`, the store's `store_stamp`): what `wf.derive` reads is the content of
    the listed install files and the store, nothing else (#70, #122). None only when `wf.py` itself
    cannot be read (an install that is no install)."""
    try:
        (workflow_dir / "wf.py").read_bytes()
    except OSError:
        return None
    return install_fingerprint(workflow_dir), store_stamp(store)


def cache_key(spec: Trace) -> tuple[Path, str]:
    """The phase-cache key of a chosen spec copy: (its tree, its slug)."""
    return spec_tree(spec), Path(spec.path).stem


def cached_phase(spec: Trace | None, wf: ModuleType, cache: PhaseCache | None,
                 workflow_fp: tuple | None, store: Path | None = None) -> PhaseView:
    """row_phase, reused while the spec tree and the workflow install are unchanged.
    Only a success with both fingerprints is stored (Decision 13)."""
    if spec is None or cache is None:
        return row_phase(spec, wf, store)
    key = cache_key(spec)
    fingerprint = tree_fingerprint(key[0])
    stored = cache.pop(key, None)
    if (stored is not None and fingerprint is not None and workflow_fp is not None
            and stored[:2] == (fingerprint, workflow_fp)):
        cache[key] = stored
        return stored[2]
    phase = row_phase(spec, wf, store)
    if fingerprint is not None and workflow_fp is not None:
        cache[key] = (fingerprint, workflow_fp, phase)
    return phase


CACHE_VERSION = 1   # `version` of the phase cache file (#66)


def to_tuples(value: object) -> object:
    """A JSON value with every list turned into a tuple, recursively (a fingerprint's form)."""
    if isinstance(value, list):
        return tuple(to_tuples(item) for item in value)
    return value


def file_entry(raw: object) -> tuple[tuple[Path, str], tuple[tuple, tuple, PhaseView]] | None:
    """One entry of the cache file as (key, value), or None when it is malformed."""
    if not isinstance(raw, dict):
        return None
    tree, slug, phase = raw.get("tree"), raw.get("slug"), raw.get("phase")
    fingerprint, workflow_fp = raw.get("fingerprint"), raw.get("workflow_fingerprint")
    if (not isinstance(tree, str) or not isinstance(slug, str) or fingerprint is None
            or workflow_fp is None or not isinstance(phase, dict)):
        return None
    number, verdicts = phase.get("phase"), phase.get("verdicts")
    if (phase.get("kind") not in ("derived", "delivered") or isinstance(number, bool)
            or not (number is None or isinstance(number, int)) or not isinstance(verdicts, list)
            or not all(isinstance(v, dict) and "phase" in v and "status" in v
                       and isinstance(v.get("problems"), list) and "reason" in v
                       for v in verdicts)):
        return None
    view = PhaseView(phase["kind"], number, tuple(verdicts))
    return (Path(tree), slug), (to_tuples(fingerprint), to_tuples(workflow_fp), view)


def load_phase_cache(path: Path) -> PhaseCache:
    """The phase cache saved in `path`; {} when the file is missing, unreadable, not JSON, of
    another version or has no entry list. A malformed entry is skipped, the others kept.
    Raises nothing."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):   # unreadable (or a directory), not UTF-8, not JSON
        return {}
    if (not isinstance(document, dict) or document.get("version") != CACHE_VERSION
            or not isinstance(document.get("entries"), list)):
        return {}
    loaded = (file_entry(raw) for raw in document["entries"])
    return dict(entry for entry in loaded if entry is not None)


def save_phase_cache(path: Path, cache: PhaseCache) -> None:
    """Write `cache` to `path` whole: to a temporary file in the same directory, then renamed
    over `path`, so a reader never sees half a file. Entries without both fingerprints are left
    out. On any exception the temporary file is removed and the exception raised."""
    entries = [{"tree": str(tree), "slug": slug, "fingerprint": fingerprint,
                "workflow_fingerprint": workflow_fp,
                "phase": {"kind": phase.kind, "phase": phase.phase,
                          "verdicts": list(phase.verdicts)}}
               for (tree, slug), (fingerprint, workflow_fp, phase) in cache.items()
               if fingerprint is not None and workflow_fp is not None]
    text = json.dumps({"version": CACHE_VERSION, "entries": entries})
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".phase-cache-", delete=False) as handle:
        try:
            handle.write(text)
            handle.flush()
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


class PhaseJobs:
    """Background derivation of the phases (#65, Decisions 5-6): one job per (tree, slug) in a
    thread pool. `phase` answers in the request's thread: the cached phase, COMPUTING while a
    job runs or starts, or the held error of a job that failed less than `hold_failed` seconds
    ago for the same fingerprints. One lock guards the running jobs, the held failures and every
    read and write of the phase cache that `phase`, a job or a prune makes."""

    def __init__(self, workers: int, hold_failed: float = 10.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phase-job")
        self._lock = threading.Condition()   # also what wait_idle waits on
        self._hold_failed = hold_failed
        self._clock = clock
        self._running: dict[tuple[Path, str], Future] = {}   # key -> its Future
        self._held: dict[tuple[Path, str], tuple[tuple, tuple, str, float]] = {}
        self._generation = 0   # +1 whenever the phase cache gains or loses an entry (#66)

    def phase(self, spec: Trace | None, wf: ModuleType, cache: PhaseCache,
              workflow_fp: tuple | None, store: Path | None = None) -> PhaseView:
        if spec is None:   # nothing to derive: the answer is immediate
            return row_phase(spec, wf, store)
        key = cache_key(spec)
        fingerprint = tree_fingerprint(key[0])
        if fingerprint is None or workflow_fp is None:   # Decision 13: never cached, so no job
            return row_phase(spec, wf, store)
        with self._lock:
            stored = cache.get(key)
            if stored is not None and stored[:2] == (fingerprint, workflow_fp):
                return stored[2]
            held = self._held.get(key)
            if (held is not None and held[:2] == (fingerprint, workflow_fp)
                    and self._clock() - held[3] < self._hold_failed):
                raise RuntimeError(held[2])
            if key not in self._running:
                self._running[key] = self._pool.submit(
                    self._derive, key, spec, wf, store, fingerprint, workflow_fp, cache)
            if stored is not None:   # #111: other fingerprints - the last known phase, refreshing
                return replace(stored[2], refreshing=True)
            return COMPUTING

    def _derive(self, key: tuple[Path, str], spec: Trace, wf: ModuleType, store: Path | None,
                fingerprint: tuple, workflow_fp: tuple, cache: PhaseCache) -> None:
        try:
            phase = row_phase(spec, wf, store)
            error = None
        except Exception as failure:  # noqa: BLE001 - any failure is held and shown on the row
            phase, error = None, str(failure)
        with self._lock:
            if error is None:
                cache[key] = (fingerprint, workflow_fp, phase)
                self._generation += 1
                self._held.pop(key, None)
            else:
                self._held[key] = (fingerprint, workflow_fp, error, self._clock())
            self._running.pop(key, None)
            self._lock.notify_all()

    def prune(self, cache: PhaseCache, used: set[tuple[Path, str]]) -> None:
        """Drop the cache entries and held failures that the last scan did not use."""
        with self._lock:
            for stale in set(cache) - used:
                del cache[stale]
                self._generation += 1
            for stale in set(self._held) - used:
                del self._held[stale]

    def cache_copy(self, cache: PhaseCache) -> tuple[int, PhaseCache]:
        """(generation, a copy of `cache`), taken together under the lock."""
        with self._lock:
            return self._generation, dict(cache)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no job runs; False when `timeout` seconds pass first."""
        with self._lock:
            return self._lock.wait_for(lambda: not self._running, timeout)

    def close(self) -> None:
        """Shut the pool down without waiting for the running jobs. A queued job that the
        shutdown cancelled never pops itself, so it leaves the running set here."""
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            for key in [k for k, future in self._running.items() if future.cancelled()]:
                del self._running[key]
            self._lock.notify_all()


def row_where(worktree: Trace | None, repo: Repo) -> Where:
    """Where the work is: the worktree, else the primary tree; dirty = porcelain
    lines, ahead = commits not reachable from the base (None without a base)."""
    if worktree is not None:
        tree, kind, branch = Path(worktree.path), "worktree", worktree.branch
    else:
        tree, kind = repo.primary, "primary"
        branch = git_out(tree, "branch", "--show-current").strip() or None
    dirty = len(git_out(tree, "status", "--porcelain").splitlines())
    ahead = None
    if repo.base is not None:
        ahead = int(git_out(tree, "rev-list", "--count", f"{repo.base}..HEAD").strip())
    return Where(kind, str(tree), branch, dirty, ahead, repo.base, repo.base_note)


def is_open(row: Row) -> bool:
    """A live agent (`row.agent`, set only for an alive presence record of the row's own trees),
    a linked worktree, a claim, or a spec whose derivation is not delivered or
    cannot be derived (a spec trace with a phase that is not derived or delivered),
    or a source that cannot be read (an error with no phase). A git error on a
    delivered row leaves it in history."""
    has_spec = any(t.kind == "spec" for t in row.traces)
    return (row.agent is not None
            or any(t.kind in ("worktree", "claim") for t in row.traces)
            or row.phase.kind in ("derived", "computing")
            or (has_spec and row.phase.kind == "no-spec")
            or (row.error is not None and row.phase.kind == "no-spec"))


TICKET_REF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]+-\d+(?![\d.])")


def is_feature_row(row: Row) -> bool:
    """A feature row (#93): a spec trace, a claim, a live agent, a handoff trace, or a ticket
    reference (`TICKET_REF`) in the slug or the branch. Field reads only: no git, no network."""
    return (any(t.kind in ("spec", "handoff") for t in row.traces)
            or row.claim is not None
            or row.agent is not None
            or TICKET_REF.search(row.slug) is not None
            or (row.where.branch is not None and TICKET_REF.search(row.where.branch) is not None))


def is_other_worktree(row: Row) -> bool:
    """An open row kept open by its worktree alone (#93). A row with an error stays an Open row:
    an unreadable repo or handoff is a fault the owner must see, not a leftover to fold away."""
    return is_open(row) and row.error is None and not is_feature_row(row)


# --- wire form --------------------------------------------------------------

def to_json(snapshot: Snapshot) -> dict:
    """The spec's Snapshot, in plain JSON types."""
    return {
        "generated_at": snapshot.generated_at,
        "partial": snapshot.partial,
        "errors": [{"source": e.source, "message": e.message} for e in snapshot.errors],
        "open": [row_json(r) for r in snapshot.open],
        "history": [row_json(r) for r in snapshot.history],
        "other_worktrees": [row_json(r) for r in snapshot.other],
    }


def trace_id(trace: Trace) -> str:
    """The wire id of a trace: what `traces[].id` carries and `POST /api/open` is asked with."""
    return hashlib.sha1(f"{trace.kind}\0{trace.path}".encode()).hexdigest()[:12]


JIRA_KEY = re.compile(r"[A-Z][A-Z0-9]+-[0-9]+")
SCP_LIKE = re.compile(r"[^/@:\s]+@(?P<host>[^/@:\s]+):(?P<path>.+)")


def ticket_link(tracker: str | None, jira_url: str | None) -> str | None:
    """An http(s) `tracker:` as it is; a Jira key under `jira_url`; else None (Decision 17)."""
    if tracker is None:
        return None
    if tracker.startswith(("http://", "https://")):
        return tracker
    if jira_url and jira_url.startswith(("http://", "https://")) and JIRA_KEY.fullmatch(tracker):
        return f"{jira_url.rstrip('/')}/browse/{tracker}"
    return None


def origin_host_path(origin: str) -> tuple[str, list[str]] | None:
    """(host, path segments) of an https/http or scp-like origin; the user, password and port of
    the URL are dropped here, so nothing built from the result can carry them."""
    scp = SCP_LIKE.fullmatch(origin) if "://" not in origin else None
    if scp:
        host, path = scp["host"], scp["path"]
    else:
        try:
            parts = urlsplit(origin)
            host, path = parts.hostname or "", parts.path
        except ValueError:
            return None
        if parts.scheme not in ("http", "https") or not host:
            return None
    path = path.strip("/").removesuffix(".git")
    return host.lower(), [p for p in path.split("/") if p]


def pr_link(origin: str | None, branch: str | None) -> str | None:
    """The page listing the PRs of `branch`: GitHub's search, or a Bitbucket Server repo's
    pull-requests filtered by the ref (Decision 17); None for any other host or shape."""
    if not origin or not branch:
        return None
    found = origin_host_path(origin)
    if found is None:
        return None
    host, segments = found
    if host == "github.com" and len(segments) == 2:
        head = quote(branch, safe="")
        return f"https://github.com/{segments[0]}/{segments[1]}/pulls?q=is%3Apr+head%3A{head}"
    if len(segments) >= 3 and segments[-3] == "scm":
        context = "".join(f"/{p}" for p in segments[:-3])
        ref = quote(f"refs/heads/{branch}", safe="")
        return (f"https://{host}{context}/projects/{segments[-2].upper()}/repos/{segments[-1]}"
                f"/pull-requests?at={ref}")
    return None


def row_json(row: Row) -> dict:
    return {
        "id": row.id, "repo": None if row.repo == REPO_UNKNOWN else row.repo, "slug": row.slug,
        "last_activity": iso(row.last_activity),
        "phase": {"kind": row.phase.kind, "phase": row.phase.phase,
                  "verdicts": [dict(v) for v in row.phase.verdicts],
                  "refreshing": row.phase.refreshing},
        "where": asdict(row.where),
        "claim": row.claim, "agent": row.agent, "signals": list(row.signals),
        "traces": [{"id": trace_id(t), "kind": t.kind, "path": t.path} for t in row.traces],
        "error": row.error,
        "links": row.links or {"pr": None, "ticket": None, "tracker": None},
    }



# --- handoffs, agents, signals, detail (#25) --------------------------------

HANDOFF_FILE = re.compile(r"handoff-(?P<slug>.+)-(?P<date>\d{8})[a-z]?\.md")
REPO_LINE = re.compile(r"^- \*\*Repo\*\*:(.*)$", re.MULTILINE)
OWNER_NAME = re.compile(r"[\w.-]+/[\w.-]+")


def handoff_repo(text: str) -> str | None:
    """The first owner/name token of the `- **Repo**:` line, case-folded."""
    line = REPO_LINE.search(text)
    token = OWNER_NAME.search(line.group(1)) if line else None
    return token.group(0).lower() if token else None


def handoff_traces(workflow_dir: Path) -> tuple[list[Trace], list[SourceError]]:
    """One trace per `handoffs/handoff-<slug>-<YYYYMMDD>[letter].md` (Decision 8). `at` is the
    date of the file name (a checkout gives every file today's mtime). A file that cannot be
    read, or whose date is impossible, is a SourceError AND a trace of repo REPO_UNKNOWN under
    its file-name slug (the row that shows the error, story 27); other files in the dir are
    ignored."""
    directory = workflow_dir / "handoffs"
    found: list[Trace] = []
    errors: list[SourceError] = []
    try:
        files = sorted(directory.glob("handoff-*.md"))
    except OSError as error:
        return [], [SourceError(str(directory), str(error))]
    for file in files:
        named = HANDOFF_FILE.fullmatch(file.name)
        if named is None:
            continue
        try:
            day = datetime.strptime(named["date"], "%Y%m%d")
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            errors.append(SourceError(str(file), f"unreadable handoff: {error}"))
            try:
                at = file.stat().st_mtime
            except OSError:
                at = 0.0
            found.append(Trace("handoff", REPO_UNKNOWN, named["slug"], str(file), None, at, ""))
            continue
        found.append(Trace("handoff", handoff_repo(text) or REPO_UNKNOWN, named["slug"], str(file),
                           None, day.timestamp(), ""))
    return found, errors


def merge_handoff_errors(rows: list[Row], errors: list[SourceError]) -> list[Row]:
    """Each unreadable handoff puts its SourceError message on the REPO_UNKNOWN row holding
    its trace (`handoff_traces` made it, with the file as `path` = the error's `source`); an
    error row is in Open (no phase to read)."""
    message_of = {e.source: e.message for e in errors}
    merged = []
    for row in rows:
        own = [message_of[t.path] for t in row.traces
               if row.repo == REPO_UNKNOWN and t.kind == "handoff" and t.path in message_of]
        merged.append(replace(row, error="; ".join(filter(None, (row.error, *own)))) if own else row)
    return merged


def row_trees(row: Row) -> set[str]:
    """The trees a record of this row may name: its worktree, the tree of each spec copy, the
    primary tree a claim lives under, and where the row says the work is."""
    trees = [row.where.path] if row.where.path else []
    for t in row.traces:
        path = Path(t.path)
        if t.kind == "worktree":
            trees.append(t.path)
        elif t.kind in ("spec", "claim"):   # <tree>/docs/specs/x.md, <primary>/.git/agentic-sessions/x
            trees.append(str(path.parents[2]))
    return {str(Path(tree).resolve()) for tree in trees}


def boot_time(proc_root: Path) -> float | None:
    """`btime` of <proc>/stat, None when it cannot be read."""
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def attach_agents(rows: list[Row], presence: tuple, proc_root: Path = Path("/proc")) -> list[Row]:
    """`Row.agent` from an alive record whose slug is the row's and whose repo_root is one of
    the row's trees; `since` = btime + start ticks / CLK_TCK (None when /proc/stat is
    unreadable). The newest record wins when several match. A process is active on one
    feature only (#51): of its alive records, only the newest counts."""
    by_time = sorted((p for p in presence if p.alive),
                     key=lambda p: datetime.fromisoformat(p.recorded_at))
    alive = list({(p.pid, p.start_ticks): p for p in by_time}.values())  # the newest per process wins
    if not alive:
        return rows
    btime = boot_time(proc_root)
    hertz = os.sysconf("SC_CLK_TCK")
    attached: list[Row] = []
    for row in rows:
        trees = row_trees(row)
        mine = [p for p in alive if p.slug == row.slug and str(Path(p.repo_root).resolve()) in trees]
        if not mine:
            attached.append(row)
            continue
        record = newest(mine, lambda p: p.recorded_at)
        started = btime + record.start_ticks / hertz if btime is not None else None
        trace = Trace("presence", row.repo, row.slug, str(proc_root / str(record.pid)), None,
                      started or 0.0, "")
        agent = {"pid": record.pid, "process": record.process,
                 "since": iso(started) if started is not None else None}
        attached.append(replace(row, agent=agent, traces=(*row.traces, trace)))
    return attached


def row_signals(row: Row) -> tuple[dict, ...]:
    """Decision 7 and 12. Field reads only; every name is a key of SIGNAL_RULES and SIGNAL_LABELS.
    `path` is the tree a signal is about (a worktree of the row), None when it is about the row."""
    found: list[tuple[str, str | None]] = []
    where = row.where
    if where.kind == "worktree":
        if removable(row, where):
            found.append(("removable", where.path))
        if where.base is None:
            found.append(("base-unknown", where.path))
    found += [("removable", other.path) for other in row.trees if removable(row, other)]
    if row.claim is not None and row.agent is None:
        found.append(("claim-no-agent", None))
    if any(v["status"] == "fail" for v in row.phase.verdicts) and row.agent is None:
        found.append(("phase-fail", None))   # #96: with a live agent the phase is just in progress
    if (row.phase.kind == "delivered" and not any(t.kind == "handoff" for t in row.traces)
            and row.agent is None):
        found.append(("handoff-missing", None))   # #94: with a live agent the handoff is not due yet
    return tuple({"name": n, "rule": SIGNAL_RULES[n], "label": SIGNAL_LABELS[n], "path": path}
                 for n, path in found)


def removable(row: Row, where: Where) -> bool:
    """A worktree of a row with no live agent and no claim, a clean tree and HEAD reachable from
    the base. The agent belongs to the row, so it guards every tree of the row (#92)."""
    if row.agent is not None:
        return False
    return (where.kind == "worktree" and row.claim is None and where.dirty == 0
            and where.ahead == 0 and where.base is not None)


def handoff_next(path: Path) -> str | None:
    """The body of the `## Next session` section (up to the next heading), trimmed; None when
    there is no such section or it is empty. Raises OSError when the file cannot be read."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise OSError(f"{path}: not valid UTF-8 ({error.reason})") from error
    body: list[str] = []
    inside = False
    for line in lines:
        if inside and re.match(r"#{1,2} ", line):
            break
        if inside:
            body.append(line)
        elif re.fullmatch(r"## Next session\s*", line):
            inside = True
    return "\n".join(body).strip() or None


def slug_repos(rows: Iterable[Row]) -> dict[str, frozenset[str]]:
    """Slug -> the real repos it is known in; REPO_UNKNOWN is not a repo (a slug known only
    through legacy handoffs maps to the empty set, so it is still a known slug)."""
    found: dict[str, frozenset[str]] = {}
    for row in rows:
        found[row.slug] = found.get(row.slug, frozenset()) | (
            frozenset() if row.repo == REPO_UNKNOWN else frozenset({row.repo}))
    return found


def _slug_owner(name: str, repos_of: Mapping[str, frozenset[str]]) -> str | None:
    """The longest known slug (a key of `repos_of`) that prefixes `name` followed by `-`, or None."""
    owners = [k for k in repos_of if name.startswith(k + "-")]
    return max(owners, key=len) if owners else None


def stored_reports(store: Path | None, slug: str, repo: str,
                   repos_of: Mapping[str, frozenset[str]]) -> list[tuple[str, tuple[str, ...]]]:
    """(file name, store key) of the store's evidence of this row, by name. A review report is the
    row's when its `repo` (case-folded) and `feature` are the row's; so is the brief of that repo
    and slug (`<slug>-brief.json`, key `("brief", repo, slug)`); a matrix or a manifest is
    attributed by name (Decision 8): the longest known slug that prefixes its name is `slug`,
    known in exactly one repo, the row's. A missing store is `[]` (it is never created)."""
    rs = load_report_store()
    conn = rs.open_existing(store)
    if conn is None:
        return []
    try:
        reviews = [(f"{report}.jsonl", ("review", repo, slug, report))
                   for report in rs.review_report_names(conn, repo, slug)]
        briefs = ([(f"{slug}-brief.json", ("brief", repo, slug))]
                  if rs.brief_text(conn, repo, slug) is not None else [])
        matrices = rs.matrix_names(conn)
        manifests = rs.manifest_names(conn)
    finally:
        conn.close()
    named = sorted([(f"{name}.jsonl", ("matrix", name)) for name in matrices]
                   + [(f"{name}.json", ("manifest", name)) for name in manifests])
    own = repos_of.get(slug) == frozenset({repo})
    return (reviews + briefs
            + [(name, key) for name, key in named if own and _slug_owner(key[1], repos_of) == slug])


def feature_detail(row: Row, stored: Sequence[tuple[str, tuple[str, ...]]] = ()) -> dict:
    """The row's wire form plus the Next session text of its newest handoff, the reports
    attributed to it (`name` only: the server adds the catalog ids) and the documents (#28).
    The reports are the store's (`stored`, #70). Raises OSError when a handoff vanished."""
    handoffs = [t for t in row.traces if t.kind == "handoff"]
    newest = max(handoffs, key=lambda t: (t.at, Path(t.path).name), default=None)
    return {**row_json(row),
            "handoff_next": handoff_next(Path(newest.path)) if newest else None,
            "reports": [{"name": name} for name, _ in stored], "documents": []}
