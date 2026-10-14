"""Pre-commit gate: compute review facts into the commit.

Run by guards/hooks/pre-commit on every `git commit` once installed with
`install.sh --gate <repo>`; `gate.py final [<repo>]` verifies afterwards that
the committed report is current and the hook intact. Standard library only.
"""
import contextlib
import difflib
import glob
import os
import re
import subprocess
import sys
import tempfile

if sys.version_info < (3, 11):
    sys.exit("gate: blocked: Python >= 3.11 required (tomllib); found "
             + ".".join(map(str, sys.version_info[:3])))

import tomllib  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Mapping  # noqa: E402

REPORT_PATH = ".workflow/gate-report.md"
CONFIG_PATH = ".workflow/gate.toml"
INDEX = ":"          # rev value meaning "the index being committed" (not a revspec)

HOOKS_DIR = Path(__file__).resolve().parent / "hooks"
KIT_DIR = HOOKS_DIR.parent.parent     # the checkout containing guards/


class GateError(Exception):
    """A configured check cannot run. The commit is blocked; str(e) is the reason."""


@dataclass(frozen=True)
class SkipsConfig:
    command: tuple[str, ...]
    junit_option: str = "--junitxml={path}"


@dataclass(frozen=True)
class PinsConfig:
    artifact: str                       # matched as "<artifact>:<version>"
    base: str                           # any git revision: branch, tag or sha
    files: tuple[str, ...]              # globs relative to the repo top level; may leave the repo
    version_file: str = "pyproject.toml"   # read [project].version


@dataclass(frozen=True)
class GateConfig:
    skips: SkipsConfig | None
    pins: PinsConfig | None = None


@dataclass(frozen=True)
class SkippedTest:
    test_id: str
    reason: str


@dataclass(frozen=True)
class StalePin:
    path: str                           # as matched, relative to the repo top level
    line: int                           # 1-based
    version: str                        # the pinned version (!= new)


@dataclass(frozen=True)
class Pins:
    old: str                            # version at base
    new: str                            # version at rev
    stale: tuple[StalePin, ...]         # sorted by (path, line, version); empty when old == new
    artifact: str                       # for rendering "pins <artifact>:<version>"


@dataclass(frozen=True)
class Report:
    """The facts written to the gate report; each further check adds a sibling field."""
    skips: tuple[SkippedTest, ...] | None
    pins: Pins | None = None


def load_config(repo: Path) -> GateConfig:
    """Read .workflow/gate.toml. A missing file is an error: an installed gate
    without configuration checks nothing, which is inconclusive, not a pass."""
    path = repo / CONFIG_PATH
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GateError(f"{CONFIG_PATH} not found: the gate is installed but not configured")
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"{CONFIG_PATH}: cannot read: {e}")
    except tomllib.TOMLDecodeError as e:
        raise GateError(f"{CONFIG_PATH}: invalid TOML: {e}")
    skips = data.get("skips")
    pins = data.get("pins")
    return GateConfig(skips=None if skips is None else _parse_skips(skips),
                      pins=None if pins is None else _parse_pins(pins))


def _parse_skips(table: Any) -> SkipsConfig:
    if not isinstance(table, dict):
        raise GateError(f"{CONFIG_PATH}: [skips] must be a table")
    command = table.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(arg, str) and arg for arg in command)):
        raise GateError(f"{CONFIG_PATH}: skips.command must be a non-empty list of strings (argv)")
    junit_option = table.get("junit_option", SkipsConfig.junit_option)
    if not isinstance(junit_option, str):
        raise GateError(f"{CONFIG_PATH}: skips.junit_option must be a string")
    return SkipsConfig(command=tuple(command), junit_option=junit_option)


def _parse_pins(table: Any) -> PinsConfig:
    if not isinstance(table, dict):
        raise GateError(f"{CONFIG_PATH}: [pins] must be a table")
    for key in ("artifact", "base"):
        value = table.get(key)
        if not isinstance(value, str) or not value:
            raise GateError(f"{CONFIG_PATH}: pins.{key} must be a non-empty string")
    files = table.get("files")
    if (not isinstance(files, list) or not files
            or not all(isinstance(f, str) and f for f in files)):
        raise GateError(f"{CONFIG_PATH}: pins.files must be a non-empty list of globs")
    version_file = table.get("version_file", PinsConfig.version_file)
    if not isinstance(version_file, str) or not version_file:
        raise GateError(f"{CONFIG_PATH}: pins.version_file must be a non-empty string")
    return PinsConfig(artifact=table["artifact"], base=table["base"],
                      files=tuple(files), version_file=version_file)


def clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop every GIT_* variable git exports to hooks (GIT_INDEX_FILE, GIT_DIR, ...).

    A test suite that runs git would otherwise act on the commit's index.
    """
    return {k: v for k, v in env.items() if not k.startswith("GIT_")}


def run_skips(repo: Path, cfg: SkipsConfig) -> tuple[SkippedTest, ...]:
    """Run the test command with a JUnit report and return the skipped tests.

    A non-zero exit with a valid JUnit is not an error: failing tests are not
    this gate's job. The command's console output goes to stderr for the
    developer and is never parsed.
    """
    with tempfile.TemporaryDirectory(prefix="gate-") as tmp:
        junit = Path(tmp) / "junit.xml"
        # A junit_option without {path} writes no report and is blocked below.
        argv = [*cfg.command, cfg.junit_option.replace("{path}", str(junit))]
        try:
            subprocess.run(argv, cwd=repo, env=clean_env(os.environ),
                           stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
        except OSError as e:
            raise GateError(f"test command cannot start: {cfg.command[0]}: {e.strerror or e}")
        try:
            xml_text = junit.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise GateError(f"test command produced no JUnit report ({' '.join(argv)})")
        except (OSError, UnicodeDecodeError) as e:
            raise GateError(f"cannot read the JUnit report: {e}")
        return parse_junit(xml_text)


def parse_junit(xml_text: str) -> tuple[SkippedTest, ...]:
    """Skipped test cases from a JUnit report, sorted by test id."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise GateError(f"JUnit report is not parsable XML: {e}")
    if root.tag not in ("testsuites", "testsuite"):
        raise GateError(f"JUnit report has unexpected root <{root.tag}>")
    found = [
        SkippedTest(test_id=f"{case.get('classname', '')}::{case.get('name', '')}",
                    reason=skipped.get("message", ""))
        for case in root.iter("testcase")
        if (skipped := case.find("skipped")) is not None
    ]
    return tuple(sorted(found, key=lambda s: s.test_id))


def read_version(repo: Path, version_file: str, rev: str) -> str:
    """[project].version of ``version_file`` at git revision ``rev`` (INDEX = the index)."""
    # ":<path>" is git's revspec for <path> in the index (stage 0).
    spec = f":{version_file}" if rev == INDEX else f"{rev}:{version_file}"
    where = "the index" if rev == INDEX else rev
    try:
        text = _git(repo, "show", spec, what=f"read {version_file} at {where}")
    except UnicodeDecodeError:
        raise GateError(f"{version_file} at {where} is not UTF-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise GateError(f"{version_file} at {where}: invalid TOML: {e}")
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise GateError(f"{version_file} at {where}: no [project].version string")
    return version


def scan_pins(repo: Path, cfg: PinsConfig, new_version: str) -> tuple[StalePin, ...]:
    """Every "<artifact>:<version>" in the deploy files whose version differs from
    ``new_version``, sorted by (path, line, version). Files are read from the filesystem."""
    paths: set[str] = set()
    for file_glob in cfg.files:
        matched = glob.glob(file_glob, root_dir=repo, recursive=True)
        if not matched:
            raise GateError(f"pins.files: {file_glob!r} matches no file")
        paths.update(matched)
    # The lookbehind keeps "other-my-svc:" from matching "my-svc"; the ":"
    # right after the name keeps "my-svc-extra:" out.
    pin = re.compile(r"(?<![A-Za-z0-9._-])" + re.escape(cfg.artifact)
                     + r":v?(\d+(?:\.\d+)*[0-9A-Za-z.+-]*)")
    stale = []
    for path in paths:
        try:
            text = (repo / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise GateError(f"cannot read deploy file {path}: {e}")
        for number, line in enumerate(text.splitlines(), start=1):
            stale.extend(StalePin(path=path, line=number, version=m.group(1))
                         for m in pin.finditer(line) if m.group(1) != new_version)
    return tuple(sorted(stale, key=lambda p: (p.path, p.line, p.version)))


def check_pins(repo: Path, cfg: PinsConfig, rev: str) -> Pins:
    old = read_version(repo, cfg.version_file, cfg.base)
    new = read_version(repo, cfg.version_file, rev)
    stale = () if old == new else scan_pins(repo, cfg, new)
    return Pins(old=old, new=new, stale=stale, artifact=cfg.artifact)


def render_report(report: Report) -> str:
    """Deterministic markdown: no timestamps, skips already sorted by parse_junit."""
    lines = [
        "# Gate report",
        "",
        f"Generated by the pre-commit gate (guards/gate.py) from {CONFIG_PATH}.",
        "Do not edit: it is rewritten on every commit.",
        "",
        "## Skipped tests",
        "",
    ]
    if report.skips is None:
        lines.append("not configured")
    elif not report.skips:
        lines.append("none")
    else:
        for skip in report.skips:
            reason = " ".join(skip.reason.split())
            lines.append(f"- `{skip.test_id}` — {reason}" if reason else f"- `{skip.test_id}`")
    lines += ["", "## Stale pins", ""]
    pins = report.pins
    if pins is None:
        lines.append("not configured")
    elif pins.old == pins.new:
        lines.append(f"not applicable: version unchanged ({pins.new})")
    elif not pins.stale:
        lines.append(f"version {pins.old} → {pins.new}: none")
    else:
        lines.append(f"version {pins.old} → {pins.new}:")
        lines += [f"- `{p.path}:{p.line}` pins {pins.artifact}:{p.version}" for p in pins.stale]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str, what: str, strip: bool = True) -> str:
    """Run git in repo; a non-zero exit becomes GateError("cannot <what>: ...").

    The output is stripped unless ``strip`` is False (exact content, column layouts).
    The hook's environment is inherited: GIT_INDEX_FILE is the index being committed.
    """
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise GateError(f"cannot {what}: {result.stderr.strip()}")
    return result.stdout.strip() if strip else result.stdout


def write_and_stage(repo: Path, text: str) -> None:
    path = repo / REPORT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise GateError(f"cannot write {REPORT_PATH}: {e}")
    _git(repo, "add", "--", REPORT_PATH, what=f"stage {REPORT_PATH}")


def install(repo: Path) -> None:
    """Point the repo-local core.hooksPath at the kit's hooks.

    Idempotent. Refuses a repo subdirectory (the enclosing repo's config would
    change) and a hooksPath already set to something else (another hook
    manager would be silently replaced). Never touches global config.
    """
    top = _git(repo, "rev-parse", "--show-toplevel", what=f"find the work tree of {repo}")
    if Path(top).resolve() != repo.resolve():
        raise GateError(f"{repo} is not the top level of its repository ({top}); "
                        "pass the top level")
    current = _git(repo, "config", "--local", "--type=path", "--default", "", "--get",
                   "core.hooksPath", what=f"read core.hooksPath in {repo}")
    # A relative hooksPath is relative to the top level, as git reads it.
    if current and (Path(top) / current).resolve() == HOOKS_DIR:
        return
    if current:
        raise GateError(f"core.hooksPath is already set to {current!r} in {repo}; "
                        "unset it first (git config --local --unset core.hooksPath) "
                        "if you want the gate to replace it")
    hooks = repo / _git(repo, "rev-parse", "--git-path", "hooks", what="locate .git/hooks")
    active = sorted(h.name for h in hooks.glob("*") if h.is_file() and h.suffix != ".sample")
    _git(repo, "config", "--local", "core.hooksPath", str(HOOKS_DIR),
         what=f"set core.hooksPath in {repo}")
    if active:
        print(f"gate: warning: these hooks in {hooks} will stop running: {', '.join(active)}",
              file=sys.stderr)


def compute_report(repo: Path, cfg: GateConfig, rev: str) -> Report:
    """Run every configured check; ``rev`` is the git revision whose versioned
    files are read (INDEX = the index being committed)."""
    skips = run_skips(repo, cfg.skips) if cfg.skips else None
    pins = check_pins(repo, cfg.pins, rev) if cfg.pins else None
    return Report(skips=skips, pins=pins)


def pre_commit(repo: Path) -> int:
    """0 = report written and staged; 1 = a configured check cannot run (blocked)."""
    try:
        cfg = load_config(repo)
        write_and_stage(repo, render_report(compute_report(repo, cfg, rev=INDEX)))
    except GateError as e:
        print(f"gate: blocked: {e}", file=sys.stderr)
        return 1
    return 0


@dataclass(frozen=True)
class Problem:
    message: str                     # one line
    detail: str = ""                 # e.g. the unified diff of a stale report


def _changed_paths(repo: Path, *args: str, what: str) -> list[str]:
    """Paths listed by `git status --porcelain <args>` (renames as "old -> new")."""
    out = _git(repo, "status", "--porcelain", *args, what=what, strip=False)
    # Each line is "XY <path>": two status columns and a space.
    return [line[3:] for line in out.splitlines()]


def final_problems(repo: Path) -> tuple[Problem, ...]:
    """Everything that makes the committed state unverified, in reporting order.

    A check that cannot run is a Problem (inconclusive = fail), never a traceback.
    """
    try:
        repo = Path(_git(repo, "rev-parse", "--show-toplevel", what=f"find the work tree of {repo}"))
    except GateError:
        return (Problem(f"not a git repository: {repo}"),)
    found = []
    try:
        changed = _changed_paths(repo, "--untracked-files=no", what="read git status")
    except GateError as e:
        found.append(Problem(f"configured check cannot run: {e}"))
    else:
        if changed:
            found.append(Problem(f"uncommitted tracked changes: {', '.join(changed)}"))
    try:
        committed = _git(repo, "show", f"HEAD:{REPORT_PATH}", what="read the committed report",
                         strip=False)
    except GateError:
        committed = None
        found.append(Problem(f"no report committed: HEAD has no {REPORT_PATH}"))
    # The test command's console output is kept only to explain a failure.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as console:
        try:
            with contextlib.redirect_stderr(console):
                recomputed = render_report(compute_report(repo, load_config(repo), rev="HEAD"))
        except GateError as e:
            console.seek(0)
            found.append(Problem(f"configured check cannot run: {e}", console.read().strip()))
        else:
            if committed is not None and committed != recomputed:
                diff = difflib.unified_diff(committed.splitlines(keepends=True),
                                            recomputed.splitlines(keepends=True),
                                            fromfile="committed", tofile="recomputed")
                found.append(Problem(f"report stale: the committed {REPORT_PATH} differs from "
                                     "the recompute at HEAD", "".join(diff).rstrip("\n")))
    return (*found, *hooks_problems(repo))


def hooks_problems(repo: Path) -> tuple[Problem, ...]:
    """The repo-local core.hooksPath must resolve to HOOKS_DIR and the kit's
    guards/ must be unchanged in its git checkout."""
    found = []
    try:
        top = _git(repo, "rev-parse", "--show-toplevel", what=f"find the work tree of {repo}")
        current = _git(repo, "config", "--local", "--type=path", "--default", "", "--get",
                       "core.hooksPath", what=f"read core.hooksPath in {repo}")
    except GateError as e:
        found.append(Problem(f"hook path not pointing at the gate: {e}"))
    else:
        # A relative hooksPath is relative to the top level, as git reads it.
        if not current or (Path(top) / current).resolve() != HOOKS_DIR:
            found.append(Problem("hook path not pointing at the gate: core.hooksPath is "
                                 f"{repr(current) if current else 'unset'}, expected {HOOKS_DIR}"))
    try:
        kit_top = _git(KIT_DIR, "rev-parse", "--show-toplevel", what="find the kit checkout")
    except GateError:
        kit_top = None
    # A kit copy inside some other repo is not a checkout of the kit.
    if kit_top is None or Path(kit_top).resolve() != KIT_DIR:
        found.append(Problem(f"cannot verify gate integrity: the kit {KIT_DIR} is not a git checkout"))
        return tuple(found)
    try:
        modified = _changed_paths(KIT_DIR, "--", "guards", what="read git status of the kit")
    except GateError as e:
        found.append(Problem(f"cannot verify gate integrity: {e}"))
    else:
        if modified:
            found.append(Problem(f"gate files modified in the kit {KIT_DIR}: {', '.join(modified)}"))
    return tuple(found)


def final(repo: Path) -> int:
    """0 = the committed report is present and current and the hook intact; 1 otherwise."""
    found = final_problems(repo)
    for problem in found:
        print(f"gate: final: {problem.message}", file=sys.stderr)
        if problem.detail:
            print("\n".join(f"  {line}" for line in problem.detail.splitlines()), file=sys.stderr)
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["pre-commit"]:
        # githooks(5): git runs pre-commit from the top of the work tree.
        return pre_commit(Path.cwd())
    if len(args) == 2 and args[0] == "install":
        try:
            install(Path(args[1]))
        except GateError as e:
            print(f"gate: install failed: {e}", file=sys.stderr)
            return 1
        print(f"gate: installed in {args[1]} (core.hooksPath = {HOOKS_DIR})")
        return 0
    if args[:1] == ["final"] and len(args) <= 2:
        return final(Path(args[1]) if len(args) == 2 else Path.cwd())
    print("usage: gate.py pre-commit | gate.py install <repo> | gate.py final [<repo>]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
