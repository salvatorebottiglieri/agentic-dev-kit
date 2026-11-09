"""Mutation tests for guards/check_rationale_required.py.

Anti-vacuity (D-002, D-024): a check that never fails on violating
input is decoration. Each test exercises a distinct code path; a
mutation that silences one of them makes the test fail.

The pure function `find_violation(staged)` is the heart of the check;
tests call it directly. The git-shell wrappers are tested in one
end-to-end smoke test against a tmpdir repo.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

GUARDS = Path(__file__).resolve().parent.parent / "guards"
spec = importlib.util.spec_from_file_location(
    "check_rationale_required", GUARDS / "check_rationale_required.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_no_rule_change_passes():
    """No phases/*.md or WORKFLOW.md staged: pass."""
    staged = ["README.md", "tests/test_x.py", "audit.py"]
    assert mod.find_violation(staged) is None


def test_phases_change_without_rationale_blocks():
    """phases/1-foo.md staged but RATIONALE.md not staged: blocked."""
    staged = ["phases/1-foo.md"]
    v = mod.find_violation(staged)
    assert v is not None
    assert "RATIONALE.md" in v
    assert "phases/1-foo.md" in v


def test_workflow_change_without_rationale_blocks():
    """WORKFLOW.md staged but RATIONALE.md not staged: blocked."""
    staged = ["WORKFLOW.md"]
    v = mod.find_violation(staged)
    assert v is not None
    assert "WORKFLOW.md" in v


def test_phases_change_with_rationale_passes():
    """phases/1-foo.md AND RATIONALE.md staged: pass."""
    staged = ["phases/1-foo.md", "RATIONALE.md"]
    assert mod.find_violation(staged) is None


def test_workflow_change_with_rationale_passes():
    """WORKFLOW.md AND RATIONALE.md staged: pass."""
    staged = ["WORKFLOW.md", "RATIONALE.md"]
    assert mod.find_violation(staged) is None


def test_both_phases_and_workflow_without_rationale_blocks():
    """Both rule-registry files staged, no RATIONALE.md: blocked, both named."""
    staged = ["phases/1-foo.md", "WORKFLOW.md", "audit.py"]
    v = mod.find_violation(staged)
    assert v is not None
    assert "phases/1-foo.md" in v
    assert "WORKFLOW.md" in v


def test_renamed_phase_without_rationale_blocks():
    """A renamed phases file (still a rule change) without RATIONALE.md blocks."""
    # The check uses --diff-filter=ACMRD; renames (R) and deletions (D)
    # are included. find_violation only sees filenames, so a renamed
    # file's new name still matches the regex.
    staged = ["phases/2-bar.md"]
    v = mod.find_violation(staged)
    assert v is not None


def test_deletion_of_phase_without_rationale_blocks():
    """Deleting a phase file (still a rule change) without RATIONALE.md
    blocks. Realistic case: 'I no longer need phase 3' needs a
    rationale entry (D-024, B-021: every rule change carries a
    rationale)."""
    # We can't easily express "deleted" in find_violation (it doesn't
    # know the diff-filter), but the spirit is: any path that matches
    # the rule-registry regex must require RATIONALE.md. This is the
    # same as test_phases_change_without_rationale_blocks; we keep
    # this test as documentation.
    staged = ["phases/3-deleted.md"]
    assert mod.find_violation(staged) is not None


def test_unrelated_change_passes():
    """A commit that only touches source code (not the rule registry) passes."""
    staged = ["src/foo.py", "tests/test_foo.py", "README.md"]
    assert mod.find_violation(staged) is None


def test_phases_subdir_does_not_block():
    """`phases/notes/foo.md` (a subdir) does not match the regex; a
    change to it is allowed without RATIONALE.md. Documents the
    boundary: only files DIRECTLY under phases/ count, not nested."""
    # Actually the regex `^phases/[^\s/]+\.md$` requires no slash in
    # the name, so `phases/notes/foo.md` (which has a slash) does not
    # match. Verify.
    staged = ["phases/notes/foo.md"]
    assert mod.find_violation(staged) is None


def test_rationale_alone_passes():
    """A commit that only stages RATIONALE.md passes (no rule change)."""
    staged = ["RATIONALE.md"]
    assert mod.find_violation(staged) is None


# ── End-to-end smoke: a real tmp git repo ───────────────────────────────


def test_end_to_end_blocks_and_passes(tmp_path):
    """Run the actual pre-commit-rationale shim against a real tmp
    git repo, with two scenarios: (a) commit without RATIONALE.md is
    blocked, (b) commit with RATIONALE.md is allowed."""
    shim = GUARDS / "hooks" / "pre-commit-rationale"
    assert shim.exists(), f"shim missing: {shim}"

    # scenario (a)
    repo_a = tmp_path / "a"
    repo_a.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_a, check=True)
    subprocess.run(["git", "config", "user.email", "x@x"], cwd=repo_a, check=True)
    subprocess.run(["git", "config", "user.name", "X"], cwd=repo_a, check=True)
    (repo_a / "phases").mkdir()
    (repo_a / "phases" / "1-foo.md").write_text("# foo\n", encoding="utf-8")
    subprocess.run(["git", "add", "phases/1-foo.md"], cwd=repo_a, check=True)
    r = subprocess.run(
        ["python3", str(shim)], cwd=repo_a, capture_output=True, text=True
    )
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "RATIONALE.md" in r.stderr

    # scenario (b): add RATIONALE.md to the staged set, retry
    (repo_a / "RATIONALE.md").write_text("# rationale\n", encoding="utf-8")
    subprocess.run(["git", "add", "RATIONALE.md"], cwd=repo_a, check=True)
    r = subprocess.run(
        ["python3", str(shim)], cwd=repo_a, capture_output=True, text=True
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
