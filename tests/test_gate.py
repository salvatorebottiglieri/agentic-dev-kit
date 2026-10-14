"""Tests for guards/gate.py — the pre-commit gate.

Most tests drive a real `git commit` in a temporary repository whose
core.hooksPath points at the kit's guards/hooks, so the hook shim, the CLI
and the checks are exercised together. Git is isolated: every git process
gets GIT_CONFIG_GLOBAL under tmp_path and GIT_CONFIG_NOSYSTEM=1.
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "guards"))

import gate  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def git_env(tmp_path, monkeypatch) -> dict[str, str]:
    """Process env with git isolated from the user's global/system config."""
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return dict(os.environ)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def repo(tmp_path, git_env) -> Path:
    """A fresh repo with the gate installed and a local identity."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "--local", "user.name", "Test")
    git(path, "config", "--local", "user.email", "test@example.com")
    gate.install(path)
    return path


def write_config(repo: Path, body: str) -> None:
    cfg = repo / ".workflow" / "gate.toml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(textwrap.dedent(body))


def runner_config(repo: Path, script: str) -> None:
    """Configure [skips] to run a fake test runner that receives the JUnit path."""
    (repo / "runner.py").write_text(textwrap.dedent(script))
    write_config(repo, f"""\
        [skips]
        command = ["{sys.executable}", "runner.py"]
        junit_option = "{{path}}"
        """)


def junit_runner(xml: str, console: str = "", exit_code: int = 0) -> str:
    """Source of a fake runner writing ``xml`` to argv[1] and printing ``console``."""
    return f"""\
        import sys
        open(sys.argv[1], "w").write({xml!r})
        print({console!r})
        sys.exit({exit_code})
        """


def commit(repo: Path, message: str = "change") -> subprocess.CompletedProcess:
    (repo / "file.txt").write_text(message)
    git(repo, "add", "file.txt")
    return git(repo, "commit", "-q", "-m", message, check=False)


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout


def committed_report(repo: Path) -> str:
    return git(repo, "show", f"HEAD:{gate.REPORT_PATH}").stdout


def section(report: str, title: str) -> str:
    """The body of ``## <title>`` up to the next section, stripped."""
    body = report.split(f"## {title}\n", 1)[1]
    return body.split("\n## ", 1)[0].strip()


# ── pre-commit, end to end ───────────────────────────────────────────


def test_commit_with_one_passing_and_one_skipped_pytest_test_carries_the_skip_in_its_tree(repo):
    (repo / "test_sample.py").write_text(textwrap.dedent("""\
        import pytest

        def test_passes():
            assert True

        @pytest.mark.skip(reason="needs network")
        def test_offline():
            pass
        """))
    write_config(repo, f"""\
        [skips]
        command = ["{sys.executable}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_sample.py"]
        """)

    result = commit(repo)

    assert result.returncode == 0, result.stderr
    report = committed_report(repo)
    assert "`test_sample::test_offline`" in report
    assert "needs network" in report
    assert "test_passes" not in report


TWO_SKIPS_UNSORTED = """<testsuites><testsuite name="s">
<testcase classname="pkg.zeta" name="test_late"><skipped message="slow"/></testcase>
<testcase classname="pkg.alpha" name="test_ok"/>
<testcase classname="pkg.alpha" name="test_early"><skipped message="flaky on CI"/></testcase>
</testsuite></testsuites>"""


def test_skips_are_read_from_the_junit_file_not_from_console_output(repo):
    runner_config(repo, junit_runner(
        TWO_SKIPS_UNSORTED,
        console="SKIPPED pkg.ghost::test_console_only - printed, not in JUnit",
    ))

    assert commit(repo).returncode == 0
    report = committed_report(repo)
    assert "`pkg.zeta::test_late` — slow" in report
    assert "`pkg.alpha::test_early` — flaky on CI" in report
    assert "ghost" not in report
    assert report.index("pkg.alpha::test_early") < report.index("pkg.zeta::test_late")


def test_report_says_none_when_no_test_is_skipped(repo):
    runner_config(repo, junit_runner(
        '<testsuite><testcase classname="pkg.alpha" name="test_ok"/></testsuite>'))

    assert commit(repo).returncode == 0
    assert section(committed_report(repo), "Skipped tests") == "none"


def test_report_says_not_configured_when_the_skips_section_is_absent(repo):
    write_config(repo, "# no checks configured yet\n")

    assert commit(repo).returncode == 0
    report = committed_report(repo)
    assert section(report, "Skipped tests") == "not configured"
    assert section(report, "Stale pins") == "not configured"


def test_failing_tests_with_a_valid_junit_do_not_block_the_commit(repo):
    runner_config(repo, junit_runner(
        '<testsuite><testcase classname="pkg.alpha" name="test_broken">'
        '<failure message="assert 1 == 2"/></testcase>'
        '<testcase classname="pkg.alpha" name="test_later"><skipped message="todo"/></testcase>'
        '</testsuite>',
        exit_code=1,
    ))

    result = commit(repo)

    assert result.returncode == 0, result.stderr
    assert "`pkg.alpha::test_later` — todo" in committed_report(repo)


def test_test_command_runs_without_the_git_variables_the_hook_inherits(repo):
    # git exports GIT_INDEX_FILE (and friends) to hooks; a test suite that
    # runs git would otherwise act on the commit's index. The runner records
    # every GIT_* variable it sees as a skip reason.
    runner_config(repo, """\
        import os, sys
        leaked = " ".join(sorted(k for k in os.environ if k.startswith("GIT_")))
        open(sys.argv[1], "w").write(
            '<testsuite><testcase classname="env" name="probe">'
            f'<skipped message="leaked: {leaked}"/></testcase></testsuite>')
        """)

    assert commit(repo).returncode == 0
    assert "`env::probe` — leaked:\n" in committed_report(repo)


def test_clean_env_drops_every_git_variable_and_keeps_the_rest():
    env = {
        "GIT_DIR": "/r/.git",
        "GIT_INDEX_FILE": "/r/.git/index",
        "GIT_CONFIG_GLOBAL": "/x",
        "GITHUB_TOKEN": "kept",
        "PATH": "/usr/bin",
    }

    assert gate.clean_env(env) == {"GITHUB_TOKEN": "kept", "PATH": "/usr/bin"}


# ── blocking: the configured check cannot run ────────────────────────


def assert_blocked(repo: Path, result: subprocess.CompletedProcess, reason: str,
                   head_before: str | None = None) -> None:
    """Blocked with ``reason`` and no commit made: HEAD is still ``head_before``
    (or still unborn when None)."""
    assert result.returncode != 0
    assert "Traceback" not in result.stderr  # a reason, not a crash
    blocked = [line for line in result.stderr.splitlines() if line.startswith("gate: blocked:")]
    assert blocked and reason in blocked[0], result.stderr
    if head_before is None:
        assert git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0
    else:
        assert head(repo) == head_before


def test_commit_is_blocked_when_the_test_command_cannot_start(repo):
    write_config(repo, """\
        [skips]
        command = ["no-such-test-runner-xyz", "-q"]
        """)

    assert_blocked(repo, commit(repo), "no-such-test-runner-xyz")


def test_commit_is_blocked_when_the_test_command_writes_no_junit(repo):
    runner_config(repo, "print('SKIPPED pkg::test_x - console only')\n")

    assert_blocked(repo, commit(repo), "no JUnit report")


def test_commit_is_blocked_when_the_junit_report_is_not_parsable(repo):
    runner_config(repo, junit_runner("<testsuite><testcase"))

    assert_blocked(repo, commit(repo), "JUnit")


def test_commit_is_blocked_when_the_junit_report_has_a_non_junit_root(repo):
    runner_config(repo, junit_runner("<html/>"))

    assert_blocked(repo, commit(repo), "unexpected root <html>")


def test_commit_is_blocked_when_the_junit_report_is_not_valid_utf8(repo):
    runner_config(repo, """\
        import sys
        open(sys.argv[1], "wb").write(b"\\xff")
        """)

    assert_blocked(repo, commit(repo), "cannot read the JUnit report")


def test_commit_is_blocked_when_the_junit_path_is_a_directory(repo):
    runner_config(repo, """\
        import os, sys
        os.mkdir(sys.argv[1])
        """)

    assert_blocked(repo, commit(repo), "cannot read the JUnit report")


def test_commit_is_blocked_when_the_report_cannot_be_written(repo):
    # A directory where the report file should go makes the write fail.
    runner_config(repo, junit_runner(TWO_SKIPS_UNSORTED))
    (repo / gate.REPORT_PATH).mkdir()

    assert_blocked(repo, commit(repo), f"cannot write {gate.REPORT_PATH}")


def test_commit_is_blocked_when_the_report_cannot_be_staged(repo):
    # git add refuses an ignored path, so the report cannot enter the commit.
    runner_config(repo, junit_runner(TWO_SKIPS_UNSORTED))
    (repo / ".gitignore").write_text(".workflow/\n")

    assert_blocked(repo, commit(repo), f"cannot stage {gate.REPORT_PATH}")


def test_commit_is_blocked_when_the_config_is_not_valid_utf8(repo):
    cfg = repo / gate.CONFIG_PATH
    cfg.parent.mkdir()
    cfg.write_bytes(b"[skips]\ncommand = [\"\xff\xfe\"]\n")

    assert_blocked(repo, commit(repo), gate.CONFIG_PATH)


def test_commit_is_blocked_when_the_config_path_is_a_directory(repo):
    (repo / gate.CONFIG_PATH).mkdir(parents=True)

    assert_blocked(repo, commit(repo), gate.CONFIG_PATH)


def test_commit_is_blocked_when_the_config_file_is_missing(repo):
    assert_blocked(repo, commit(repo), gate.CONFIG_PATH)


def test_commit_is_blocked_when_the_config_is_not_valid_toml(repo):
    write_config(repo, "[skips\ncommand = 1\n")

    assert_blocked(repo, commit(repo), "invalid TOML")


@pytest.mark.parametrize("body, reason", [
    ('[skips]\ncommand = "pytest -q"\n', "skips.command"),
    ('[skips]\ncommand = []\n', "skips.command"),
    ('[skips]\ncommand = ["pytest", 1]\n', "skips.command"),
    ('[skips]\ncommand = ["pytest", ""]\n', "skips.command"),
    ('[skips]\ncommand = ["pytest"]\njunit_option = 3\n', "skips.junit_option"),
    ('skips = "yes"\n', "[skips]"),
])
def test_commit_is_blocked_when_the_config_has_wrong_types(repo, body, reason):
    write_config(repo, body)

    assert_blocked(repo, commit(repo), reason)


def test_gate_on_python_older_than_3_11_exits_with_a_one_line_reason(tmp_path):
    probe = (
        "import runpy, sys\n"
        "sys.version_info = (3, 10, 0)\n"
        f"runpy.run_path({str(KIT / 'guards' / 'gate.py')!r}, run_name='__main__')\n"
    )

    result = subprocess.run([sys.executable, "-c", probe, "pre-commit"],
                            cwd=tmp_path, capture_output=True, text=True)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("gate: blocked:") and "3.11" in result.stderr


# ── stale pins of the artifact in deploy files ───────────────────────


PINS_CONFIG = """\
    [pins]
    artifact = "my-svc"
    base = "{base}"
    files = {files}
    """


def pins_config(files: str = '["../infra/deploy-*.sh"]', base: str = "main") -> str:
    return PINS_CONFIG.format(files=files, base=base)


def write_pyproject(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "my-svc"\nversion = "{version}"\n')


@pytest.fixture
def infra(tmp_path) -> Path:
    """A deploy-scripts directory outside the repo, reached as ../infra."""
    path = tmp_path / "infra"
    path.mkdir()
    return path


@pytest.fixture
def pins_repo(repo) -> Path:
    """Base commit on main at version 0.0.7, then a feature branch with [pins]
    scanning ../infra/deploy-*.sh."""
    write_pyproject(repo, "0.0.7")
    write_config(repo, "# no checks at the base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    write_config(repo, pins_config())
    return repo


def write_stale_pin(infra: Path) -> None:
    """../infra/deploy-dev.sh pinning my-svc:0.0.3, older than any version used here."""
    (infra / "deploy-dev.sh").write_text("my-svc:0.0.3\n")


def bump(repo: Path, version: str) -> subprocess.CompletedProcess:
    write_pyproject(repo, version)
    git(repo, "add", "pyproject.toml")
    return commit(repo, f"bump to {version}")


def test_version_bump_lists_a_deploy_pin_older_than_the_previous_version(pins_repo, infra):
    (infra / "deploy-dev.sh").write_text("#!/bin/sh\nIMAGE=registry/my-svc:0.0.3\n")

    result = bump(pins_repo, "0.0.8")

    assert result.returncode == 0, result.stderr
    assert section(committed_report(pins_repo), "Stale pins") == (
        "version 0.0.7 → 0.0.8:\n"
        "- `../infra/deploy-dev.sh:2` pins my-svc:0.0.3")


def test_pins_on_the_new_version_and_names_containing_the_artifact_are_not_listed(pins_repo, infra):
    (infra / "deploy-dev.sh").write_text(
        "run my-svc:0.0.8\n"
        "run my-svc:v0.0.8\n"
        "run other-my-svc:0.0.3\n"
        "run my-svc-extra:0.0.3\n"
        "run my-svc:0.0.7\n")

    assert bump(pins_repo, "0.0.8").returncode == 0
    assert section(committed_report(pins_repo), "Stale pins") == (
        "version 0.0.7 → 0.0.8:\n"
        "- `../infra/deploy-dev.sh:5` pins my-svc:0.0.7")


def test_later_commit_not_touching_the_version_still_lists_the_stale_pins(pins_repo, infra):
    write_stale_pin(infra)
    assert bump(pins_repo, "0.0.8").returncode == 0

    result = commit(pins_repo, "unrelated change")

    assert result.returncode == 0, result.stderr
    assert git(pins_repo, "diff", "--quiet", "HEAD~", "HEAD", "--", "pyproject.toml",
               check=False).returncode == 0
    assert section(committed_report(pins_repo), "Stale pins") == (
        "version 0.0.7 → 0.0.8:\n"
        "- `../infra/deploy-dev.sh:1` pins my-svc:0.0.3")


def test_version_equal_to_the_base_says_not_applicable(pins_repo, infra):
    write_stale_pin(infra)

    assert commit(pins_repo).returncode == 0
    assert section(committed_report(pins_repo), "Stale pins") == (
        "not applicable: version unchanged (0.0.7)")


def test_new_version_is_read_from_the_index_not_the_working_tree(pins_repo, infra):
    (infra / "deploy-dev.sh").write_text("my-svc:0.0.9\n")
    write_pyproject(pins_repo, "0.0.8")
    git(pins_repo, "add", "pyproject.toml")
    write_pyproject(pins_repo, "0.0.9")  # edited after staging: not committed

    assert commit(pins_repo).returncode == 0
    assert section(committed_report(pins_repo), "Stale pins") == (
        "version 0.0.7 → 0.0.8:\n"
        "- `../infra/deploy-dev.sh:1` pins my-svc:0.0.9")


def test_stale_pins_are_sorted_by_file_and_line_and_the_report_is_deterministic(pins_repo, infra):
    write_config(pins_repo, pins_config(
        files='["../infra/z-*.sh", "../infra/a-*.sh", "../infra/a-dev.sh"]'))
    (infra / "z-prod.sh").write_text("my-svc:0.0.1\n")
    (infra / "a-dev.sh").write_text("x\nmy-svc:0.0.2\nmy-svc:0.0.6\n")

    assert bump(pins_repo, "0.0.8").returncode == 0
    first = committed_report(pins_repo)
    assert commit(pins_repo, "again").returncode == 0

    assert committed_report(pins_repo) == first
    assert section(first, "Stale pins") == (
        "version 0.0.7 → 0.0.8:\n"
        "- `../infra/a-dev.sh:2` pins my-svc:0.0.2\n"
        "- `../infra/a-dev.sh:3` pins my-svc:0.0.6\n"
        "- `../infra/z-prod.sh:1` pins my-svc:0.0.1")


def test_version_bump_with_every_pin_on_the_new_version_says_none(pins_repo):
    write_config(pins_repo, pins_config(files='["deploy/*.yml"]'))
    (pins_repo / "deploy").mkdir()
    (pins_repo / "deploy" / "compose.yml").write_text("image: my-svc:0.0.8\n")

    assert bump(pins_repo, "0.0.8").returncode == 0
    report = committed_report(pins_repo)
    assert section(report, "Stale pins") == "version 0.0.7 → 0.0.8: none"
    assert section(report, "Skipped tests") == "not configured"


def test_commit_is_blocked_when_the_base_revision_cannot_be_resolved(pins_repo, infra):
    write_stale_pin(infra)
    write_config(pins_repo, pins_config(base="no-such-base"))
    head_before = head(pins_repo)

    assert_blocked(pins_repo, bump(pins_repo, "0.0.8"), "no-such-base", head_before)


def test_commit_is_blocked_when_the_version_file_is_missing_at_the_base(pins_repo, infra):
    write_stale_pin(infra)
    write_config(pins_repo, pins_config() + 'version_file = "svc/pyproject.toml"\n')
    (pins_repo / "svc").mkdir()
    (pins_repo / "svc" / "pyproject.toml").write_text('[project]\nversion = "0.0.8"\n')
    git(pins_repo, "add", "svc/pyproject.toml")
    head_before = head(pins_repo)

    assert_blocked(pins_repo, commit(pins_repo), "svc/pyproject.toml", head_before)


@pytest.mark.parametrize("pyproject, reason", [
    ("[project\nversion = '0.0.8'\n", "invalid TOML"),
    ("[project]\nname = 'my-svc'\n", "[project].version"),
    ("[project]\nversion = 8\n", "[project].version"),
])
def test_commit_is_blocked_when_the_new_version_cannot_be_read(pins_repo, infra, pyproject, reason):
    write_stale_pin(infra)
    (pins_repo / "pyproject.toml").write_text(pyproject)
    git(pins_repo, "add", "pyproject.toml")
    head_before = head(pins_repo)

    assert_blocked(pins_repo, commit(pins_repo), reason, head_before)


def test_commit_is_blocked_when_a_deploy_glob_matches_no_file(pins_repo, infra):
    write_config(pins_repo, pins_config(
        files='["../infra/deploy-*.sh", "../infra/missing-*.sh"]'))
    write_stale_pin(infra)
    head_before = head(pins_repo)

    assert_blocked(pins_repo, bump(pins_repo, "0.0.8"), "../infra/missing-*.sh", head_before)


def test_commit_is_blocked_when_a_deploy_file_cannot_be_read(pins_repo, infra):
    (infra / "deploy-dir.sh").mkdir()
    head_before = head(pins_repo)

    assert_blocked(pins_repo, bump(pins_repo, "0.0.8"), "deploy-dir.sh", head_before)


def test_commit_is_blocked_when_a_deploy_file_is_not_utf8(pins_repo, infra):
    (infra / "deploy-dev.sh").write_bytes(b"my-svc:0.0.3\n# \xff\n")
    head_before = head(pins_repo)

    result = bump(pins_repo, "0.0.8")

    assert_blocked(pins_repo, result,
                   "cannot read deploy file ../infra/deploy-dev.sh", head_before)
    assert "'utf-8' codec can't decode" in result.stderr


def test_deploy_globs_are_resolved_from_the_repo_top_not_the_process_cwd(
        pins_repo, infra, tmp_path, monkeypatch):
    write_stale_pin(infra)
    # Deeper than the repo, so "../infra" from here names a directory that does not exist.
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    cfg = gate.PinsConfig(artifact="my-svc", base="main", files=("../infra/deploy-*.sh",))

    stale = gate.scan_pins(pins_repo, cfg, "0.0.8")

    assert stale == (gate.StalePin(path="../infra/deploy-dev.sh", line=1, version="0.0.3"),)


def test_commit_is_blocked_when_the_staged_version_file_is_not_utf8(pins_repo, infra):
    write_stale_pin(infra)
    (pins_repo / "pyproject.toml").write_bytes(b'[project]\nversion = "0.0.8"\n# \xff\n')
    git(pins_repo, "add", "pyproject.toml")
    head_before = head(pins_repo)

    assert_blocked(pins_repo, commit(pins_repo), "pyproject.toml at the index is not UTF-8",
                   head_before)


def move_base_to_pyproject(repo: Path, pyproject: str) -> None:
    """Point main at a commit whose pyproject.toml is ``pyproject``, then stage a
    valid 0.0.8 so only the base side is unreadable."""
    (repo / "pyproject.toml").write_text(pyproject)
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-q", "--no-verify", "-m", "unreadable base")
    git(repo, "branch", "-f", "main", "HEAD")
    write_pyproject(repo, "0.0.8")
    git(repo, "add", "pyproject.toml")


def test_commit_is_blocked_when_the_version_file_at_the_base_is_not_valid_toml(pins_repo, infra):
    write_stale_pin(infra)
    move_base_to_pyproject(pins_repo, "[project\nversion = '0.0.7'\n")
    head_before = head(pins_repo)

    assert_blocked(pins_repo, commit(pins_repo), "pyproject.toml at main: invalid TOML",
                   head_before)


def test_commit_is_blocked_when_the_version_file_at_the_base_has_no_project_version(
        pins_repo, infra):
    write_stale_pin(infra)
    move_base_to_pyproject(pins_repo, "[project]\nname = 'my-svc'\n")
    head_before = head(pins_repo)

    assert_blocked(pins_repo, commit(pins_repo),
                   "pyproject.toml at main: no [project].version string", head_before)


def test_unchanged_version_with_a_deploy_glob_matching_no_file_commits(pins_repo):
    write_config(pins_repo, pins_config(files='["../infra/missing-*.sh"]'))

    result = commit(pins_repo)

    assert result.returncode == 0, result.stderr
    assert section(committed_report(pins_repo), "Stale pins") == (
        "not applicable: version unchanged (0.0.7)")


@pytest.mark.parametrize("body, reason", [
    ('pins = "yes"\n', "[pins]"),
    ('[pins]\nbase = "main"\nfiles = ["a"]\n', "pins.artifact"),
    ('[pins]\nartifact = ""\nbase = "main"\nfiles = ["a"]\n', "pins.artifact"),
    ('[pins]\nartifact = "a"\nfiles = ["a"]\n', "pins.base"),
    ('[pins]\nartifact = "a"\nbase = 1\nfiles = ["a"]\n', "pins.base"),
    ('[pins]\nartifact = "a"\nbase = "main"\nfiles = "a"\n', "pins.files"),
    ('[pins]\nartifact = "a"\nbase = "main"\nfiles = []\n', "pins.files"),
    ('[pins]\nartifact = "a"\nbase = "main"\nfiles = [""]\n', "pins.files"),
    ('[pins]\nartifact = "a"\nbase = "main"\nfiles = ["a"]\nversion_file = 1\n', "pins.version_file"),
    ('[pins]\nartifact = "a"\nbase = "main"\nfiles = ["a"]\nversion_file = ""\n', "pins.version_file"),
])
def test_commit_is_blocked_when_the_pins_config_has_wrong_types(repo, body, reason):
    write_config(repo, body)

    assert_blocked(repo, commit(repo), reason)


# ── the report lands in the same commit ──────────────────────────────


def test_report_is_staged_into_the_commit_even_when_git_commits_through_a_temporary_index(repo):
    # `git commit <path>` builds the commit from a temporary index
    # (GIT_INDEX_FILE): staging into .git/index would miss the commit.
    runner_config(repo, junit_runner(TWO_SKIPS_UNSORTED))
    (repo / "file.txt").write_text("v1")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "first")
    (repo / "file.txt").write_text("v2")
    (repo / "runner.py").write_text(textwrap.dedent(junit_runner(
        '<testsuite><testcase classname="pkg.new" name="test_only"><skipped message="second"/></testcase></testsuite>')))
    git(repo, "add", "runner.py")

    result = git(repo, "commit", "-q", "-m", "second", "file.txt", "runner.py", check=False)

    assert result.returncode == 0, result.stderr
    assert "`pkg.new::test_only` — second" in committed_report(repo)


# ── install ──────────────────────────────────────────────────────────


@pytest.fixture
def bare_repo(tmp_path, git_env) -> Path:
    """An initialised repo without the gate."""
    target = tmp_path / "target"
    target.mkdir()
    git(target, "init", "-q")
    return target


def test_install_points_repo_local_hooks_path_at_the_kit_and_is_idempotent(bare_repo, git_env):
    global_cfg = Path(git_env["GIT_CONFIG_GLOBAL"])
    global_before = global_cfg.read_bytes()

    gate.install(bare_repo)
    first = git(bare_repo, "config", "--local", "--get-all", "core.hooksPath").stdout
    gate.install(bare_repo)
    second = git(bare_repo, "config", "--local", "--get-all", "core.hooksPath").stdout

    assert first == second == f"{gate.HOOKS_DIR}\n"
    assert global_cfg.read_bytes() == global_before


def test_install_over_an_empty_hooks_path_replaces_it_with_a_single_kit_value(bare_repo):
    git(bare_repo, "config", "--local", "core.hooksPath", "")

    gate.install(bare_repo)

    values = git(bare_repo, "config", "--local", "--get-all", "core.hooksPath").stdout
    assert values.splitlines() == [str(gate.HOOKS_DIR)]


def test_install_refuses_and_leaves_a_foreign_hooks_path_unchanged(bare_repo):
    git(bare_repo, "config", "--local", "core.hooksPath", ".husky")

    with pytest.raises(gate.GateError, match=r"\.husky.*unset"):
        gate.install(bare_repo)

    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == ".husky"


@pytest.mark.parametrize("spelling", ["trailing-dot", "symlink"])
def test_install_accepts_another_spelling_of_the_kit_hooks_path_as_already_installed(
        bare_repo, tmp_path, spelling):
    if spelling == "symlink":
        same_dir = tmp_path / "kit-hooks-link"
        same_dir.symlink_to(gate.HOOKS_DIR)
    else:
        same_dir = f"{gate.HOOKS_DIR}/."
    git(bare_repo, "config", "--local", "core.hooksPath", str(same_dir))

    gate.install(bare_repo)

    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == str(same_dir)


def test_install_resolves_a_relative_hooks_path_against_the_repo_top_level_not_the_cwd(
        bare_repo, tmp_path, monkeypatch):
    relative = os.path.relpath(gate.HOOKS_DIR, bare_repo.resolve())
    git(bare_repo, "config", "--local", "core.hooksPath", relative)
    # Deeper than the repo: the ".." chain climbs to / and would saturate at
    # the same depth, hiding a cwd-relative resolution.
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    gate.install(bare_repo)

    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == relative


def test_install_expands_a_home_relative_hooks_path_and_treats_it_as_already_installed(
        bare_repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "kit-hooks").symlink_to(gate.HOOKS_DIR)
    monkeypatch.setenv("HOME", str(home))
    git(bare_repo, "config", "--local", "core.hooksPath", "~/kit-hooks")

    gate.install(bare_repo)

    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == "~/kit-hooks"


def test_install_does_not_warn_about_stopped_hooks_when_setting_the_hooks_path_fails(bare_repo, capsys):
    (bare_repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\n")
    # An existing lock file makes `git config` refuse to write.
    (bare_repo / ".git" / "config.lock").write_text("")

    with pytest.raises(gate.GateError, match="set core.hooksPath"):
        gate.install(bare_repo)

    assert "stop running" not in capsys.readouterr().err


def test_install_warns_about_hooks_in_git_hooks_that_will_stop_running(bare_repo, capsys):
    hook = bare_repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\n")

    gate.install(bare_repo)

    err = capsys.readouterr().err
    assert "pre-push" in err and "stop running" in err
    assert ".sample" not in err
    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == str(gate.HOOKS_DIR)


def test_install_into_a_subdirectory_of_a_repo_fails_and_leaves_its_config_alone(bare_repo):
    sub = bare_repo / "sub"
    sub.mkdir()

    with pytest.raises(gate.GateError, match="top level"):
        gate.install(sub)

    assert git(bare_repo, "config", "--local", "core.hooksPath", check=False).returncode == 1


@pytest.fixture
def kit_env(tmp_path, git_env) -> dict[str, str]:
    """Env for running install.sh with HOME and the skills dir under tmp_path."""
    return {**git_env, "HOME": str(tmp_path / "home"),
            "AGENT_SKILLS_DIR": str(tmp_path / "skills")}


def test_install_sh_gate_flag_installs_the_hook_without_touching_skills(tmp_path, kit_env, bare_repo):
    result = subprocess.run(["bash", str(KIT / "install.sh"), "--gate", str(bare_repo)],
                            env=kit_env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert git(bare_repo, "config", "--local", "core.hooksPath").stdout.strip() == str(gate.HOOKS_DIR)
    assert not (tmp_path / "skills").exists()


@pytest.mark.parametrize("extra", [[], ["a", "b"]])
def test_install_sh_gate_flag_with_a_wrong_argument_count_prints_usage_and_exits_2(kit_env, extra):
    result = subprocess.run(["bash", str(KIT / "install.sh"), "--gate", *extra],
                            env=kit_env, capture_output=True, text=True)

    assert result.returncode == 2
    assert "usage:" in result.stderr and "install <repo>" in result.stderr


def test_install_into_a_directory_that_is_not_a_repo_fails_with_a_reason(tmp_path, git_env, capsys):
    assert gate.main(["install", str(tmp_path / "nowhere")]) == 1
    assert "gate: install failed:" in capsys.readouterr().err


# ── final mode: recompute, staleness, hook tampering ─────────────────
#
# These tests run a *copy* of the kit's guards/ under tmp_path, so the copy's
# HOOKS_DIR and its git checkout are what `final` verifies: the real kit's
# working tree (dirty while it is being developed) never leaks into a verdict.


FINAL = "gate: final: "


def copy_kit(dest: Path, *, checkout: bool) -> Path:
    """A copy of the kit's guards/ at ``dest``; committed in its own repo when ``checkout``."""
    shutil.copytree(KIT / "guards", dest / "guards",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / "README.md").write_text("kit copy\n")
    if checkout:
        git(dest, "init", "-q")
        git(dest, "add", "-A")
        git(dest, "-c", "user.name=Kit", "-c", "user.email=kit@example.com",
            "commit", "-q", "-m", "kit")
    return dest


@pytest.fixture
def kit(tmp_path, git_env) -> Path:
    """A kit copy that is a clean git checkout."""
    return copy_kit(tmp_path / "kit", checkout=True)


def run_gate(kit: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(kit / "guards" / "gate.py"), *args],
                          cwd=cwd, capture_output=True, text=True)


def problems(result: subprocess.CompletedProcess) -> list[str]:
    """The one-line problems `final` printed, without the prefix."""
    return [line[len(FINAL):] for line in result.stderr.splitlines() if line.startswith(FINAL)]


def make_repo(path: Path, kit: Path) -> Path:
    """A repo with the copied gate installed and a fake runner skipping nothing,
    committed through the gate: clean, current and intact."""
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "--local", "user.name", "Test")
    git(path, "config", "--local", "user.email", "test@example.com")
    installed = run_gate(kit, "install", str(path))
    assert installed.returncode == 0, installed.stderr
    runner_config(path, junit_runner(
        '<testsuite><testcase classname="pkg.alpha" name="test_ok"/></testsuite>'))
    (path / "file.txt").write_text("v1")
    git(path, "add", "-A")
    committed = git(path, "commit", "-q", "-m", "first", check=False)
    assert committed.returncode == 0, committed.stderr
    return path


@pytest.fixture
def clean(tmp_path, kit) -> Path:
    return make_repo(tmp_path / "clean", kit)


def run_final(kit: Path, repo: Path) -> subprocess.CompletedProcess:
    return run_gate(kit, "final", str(repo))


def test_final_on_a_clean_current_intact_repo_exits_0_and_prints_nothing(kit, clean):
    result = run_final(kit, clean)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize("change, reported", [
    ("unstaged edit", True),
    ("staged edit", True),
    ("untracked file", False),
])
def test_final_reports_uncommitted_tracked_changes_but_ignores_untracked_files(
        kit, clean, change, reported):
    if change == "untracked file":
        (clean / "PR-DESCRIPTION.md").write_text("why\n")
    else:
        (clean / "file.txt").write_text("v2")
        if change == "staged edit":
            git(clean, "add", "file.txt")

    result = run_final(kit, clean)

    found = [p for p in problems(result) if p.startswith("uncommitted tracked changes")]
    assert bool(found) == reported, result.stderr
    assert result.returncode == (1 if reported else 0)
    if reported:
        assert "file.txt" in found[0]


def test_final_says_no_report_committed_when_the_branch_was_committed_without_the_gate(kit, clean):
    assert run_final(kit, clean).returncode == 0          # twin: committed through the gate
    git(clean, "rm", "-q", gate.REPORT_PATH)
    git(clean, "commit", "-q", "--no-verify", "-m", "drop the report")

    result = run_final(kit, clean)

    assert result.returncode == 1
    assert problems(result) == [f"no report committed: HEAD has no {gate.REPORT_PATH}"]


def test_final_says_report_stale_with_a_diff_showing_a_skip_added_without_the_hook(kit, clean):
    assert run_final(kit, clean).returncode == 0          # twin: the committed report is current
    (clean / "runner.py").write_text(textwrap.dedent(junit_runner(
        '<testsuite><testcase classname="pkg.alpha" name="test_ok"/>'
        '<testcase classname="pkg.new" name="test_hidden"><skipped message="later"/></testcase>'
        '</testsuite>')))
    git(clean, "add", "runner.py")
    git(clean, "commit", "-q", "--no-verify", "-m", "skip a test quietly")

    result = run_final(kit, clean)

    assert result.returncode == 1
    assert len(problems(result)) == 1 and problems(result)[0].startswith("report stale")
    detail = result.stderr.split("\n", 1)[1]
    assert "--- committed" in detail and "+++ recomputed" in detail
    assert "+- `pkg.new::test_hidden` — later" in detail
    assert "-none" in detail
    assert all(line.startswith("  ") for line in detail.splitlines())


def test_final_reports_a_configured_check_that_cannot_run_without_a_traceback(kit, clean):
    write_config(clean, """\
        [skips]
        command = ["no-such-test-runner-xyz", "-q"]
        """)
    git(clean, "add", "-A")
    git(clean, "commit", "-q", "--no-verify", "-m", "broken runner")

    result = run_final(kit, clean)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert problems(result) == [
        "configured check cannot run: test command cannot start: no-such-test-runner-xyz: "
        "No such file or directory"]


@pytest.mark.parametrize("hooks_path, reported", [
    (None, True),
    (".husky", True),
    ("relative", False),
    ("trailing-dot", False),
])
def test_final_reports_a_hook_path_not_pointing_at_the_gate_and_accepts_other_spellings(
        kit, clean, hooks_path, reported):
    hooks = kit / "guards" / "hooks"
    if hooks_path is None:
        git(clean, "config", "--local", "--unset", "core.hooksPath")
    else:
        value = {"relative": os.path.relpath(hooks, clean),
                 "trailing-dot": f"{hooks}/."}.get(hooks_path, hooks_path)
        git(clean, "config", "--local", "core.hooksPath", value)

    result = run_final(kit, clean)

    found = [p for p in problems(result) if p.startswith("hook path not pointing at the gate")]
    assert bool(found) == reported, result.stderr
    assert result.returncode == (1 if reported else 0)


@pytest.mark.parametrize("tamper, reported", [
    ("edit gate.py", True),
    ("add a hook", True),
    ("edit outside guards", False),
])
def test_final_reports_gate_files_modified_in_the_kit_checkout(kit, clean, tamper, reported):
    if tamper == "edit gate.py":
        with open(kit / "guards" / "gate.py", "a") as f:
            f.write("# tampered\n")
    elif tamper == "add a hook":
        (kit / "guards" / "hooks" / "pre-push").write_text("#!/bin/sh\n")
    else:
        (kit / "README.md").write_text("edited\n")

    result = run_final(kit, clean)

    found = [p for p in problems(result) if p.startswith("gate files modified in the kit")]
    assert bool(found) == reported, result.stderr
    assert result.returncode == (1 if reported else 0)


def test_final_cannot_verify_gate_integrity_when_the_kit_is_not_a_git_checkout(
        tmp_path, git_env):
    plain_kit = copy_kit(tmp_path / "plain-kit", checkout=False)
    repo = make_repo(tmp_path / "plain-repo", plain_kit)

    result = run_final(plain_kit, repo)

    assert result.returncode == 1
    assert problems(result) == [
        f"cannot verify gate integrity: the kit {plain_kit} is not a git checkout"]


def test_final_with_a_repo_argument_from_another_cwd_matches_final_from_the_repo_top(
        kit, clean, tmp_path):
    (clean / "runner.py").write_text(textwrap.dedent(junit_runner(
        '<testsuite><testcase classname="pkg.new" name="test_x"><skipped/></testcase></testsuite>')))
    git(clean, "commit", "-q", "--no-verify", "-am", "stale")
    elsewhere = tmp_path / "elsewhere" / "deeper"
    elsewhere.mkdir(parents=True)

    from_top = run_gate(kit, "final", cwd=clean)
    from_elsewhere = run_gate(kit, "final", str(clean), cwd=elsewhere)

    assert from_top.returncode == from_elsewhere.returncode == 1
    assert from_top.stderr == from_elsewhere.stderr
    assert problems(from_top) and problems(from_top)[0].startswith("report stale")


def test_final_reports_several_problems_in_the_specified_order(kit, tmp_path):
    repo = tmp_path / "many"
    repo.mkdir()
    git(repo, "init", "-q")
    write_config(repo, '[skips]\ncommand = ["no-such-test-runner-xyz"]\n')
    (repo / "file.txt").write_text("v1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com",
        "commit", "-q", "--no-verify", "-m", "no gate")
    (repo / "file.txt").write_text("v2")
    with open(kit / "guards" / "gate.py", "a") as f:
        f.write("# tampered\n")

    result = run_final(kit, repo)

    assert result.returncode == 1
    assert [p.split(":", 1)[0] for p in problems(result)] == [
        "uncommitted tracked changes",
        "no report committed",
        "configured check cannot run",
        "hook path not pointing at the gate",
        "gate files modified in the kit " + str(kit),
    ]


def test_final_recomputes_from_the_head_version_while_a_staged_bump_is_an_uncommitted_change(
        tmp_path, kit, infra):
    repo = make_repo(tmp_path / "repo", kit)
    write_pyproject(repo, "0.0.7")
    write_config(repo, "# no checks at the base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "base on main")
    git(repo, "checkout", "-q", "-b", "feature")
    write_config(repo, pins_config())
    write_stale_pin(infra)
    git(repo, "add", "-A")
    assert git(repo, "commit", "-q", "-m", "pins", check=False).returncode == 0
    assert run_final(kit, repo).returncode == 0
    write_pyproject(repo, "0.0.8")
    git(repo, "add", "pyproject.toml")

    result = run_final(kit, repo)

    assert result.returncode == 1
    assert problems(result) == ["uncommitted tracked changes: pyproject.toml"]


def test_final_shows_the_test_command_stdout_and_stderr_below_a_check_that_cannot_run(kit, clean):
    (clean / "runner.py").write_text(textwrap.dedent("""\
        import sys
        print("marker-on-stdout")
        print("marker-on-stderr", file=sys.stderr)
        """))
    git(clean, "commit", "-q", "--no-verify", "-am", "runner writes no junit")

    result = run_final(kit, clean)

    assert result.returncode == 1
    assert len(problems(result)) == 1
    assert problems(result)[0].startswith(
        "configured check cannot run: test command produced no JUnit report")
    lines = result.stderr.splitlines()
    assert "  marker-on-stdout" in lines and "  marker-on-stderr" in lines
    assert "marker-on-stderr" not in lines            # not leaked unindented to the terminal


def test_final_with_a_runner_writing_to_stderr_on_a_clean_repo_exits_0_and_prints_nothing(kit, clean):
    (clean / "runner.py").write_text(textwrap.dedent("""\
        import sys
        open(sys.argv[1], "w").write('<testsuite><testcase classname="pkg.alpha" name="test_ok"/></testsuite>')
        print("noise-on-stderr", file=sys.stderr)
        """))
    git(clean, "add", "runner.py")
    assert git(clean, "commit", "-q", "-m", "noisy runner", check=False).returncode == 0

    result = run_final(kit, clean)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


def test_final_says_report_stale_when_the_committed_report_only_has_an_extra_trailing_blank_line(
        kit, clean):
    report = clean / gate.REPORT_PATH
    report.write_text(report.read_text() + "\n")
    git(clean, "commit", "-q", "--no-verify", "-am", "pad the report")

    result = run_final(kit, clean)

    assert result.returncode == 1
    assert len(problems(result)) == 1 and problems(result)[0].startswith("report stale")


def test_final_on_a_directory_that_is_not_a_repo_says_not_a_git_repository(kit, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    result = run_final(kit, plain)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert problems(result) == [f"not a git repository: {plain}"]


def test_final_cannot_verify_gate_integrity_when_the_kit_copy_sits_inside_another_repo(
        tmp_path, git_env):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    nested_kit = copy_kit(outer / "kit", checkout=False)
    repo = make_repo(tmp_path / "nested-repo", nested_kit)

    result = run_final(nested_kit, repo)

    assert result.returncode == 1
    assert problems(result) == [
        f"cannot verify gate integrity: the kit {nested_kit} is not a git checkout"]


@pytest.mark.parametrize("args", [["final", "a", "b"], ["finale"]])
def test_gate_with_bad_arguments_prints_a_usage_naming_final_and_exits_2(args, capsys):
    assert gate.main(args) == 2
    assert "final [<repo>]" in capsys.readouterr().err
