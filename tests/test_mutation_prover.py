"""Mutation tests for guards/mutation_prover.py (B-026).

The agent chooses the mutants and writes them as patches; this script applies
them in clean worktrees, runs the targeted test, and emits the matrix. The
tests below use a real temporary git repo: the control must go red first, a
mutant the test catches is ``pinned``, a mutant it misses is ``blind-test``,
and a selector that collects nothing is ``no-test``.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "guards" / "mutation_prover.py"


def _load():
    spec = importlib.util.spec_from_file_location("mutation_prover_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


MP = _load()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "mod.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_empty.py").write_text(
        "# no tests here\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _break_add() -> str:
    return (
        "--- a/src/mod.py\n"
        "+++ b/src/mod.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a + b\n"
        "+    return a - b\n"
    )


def _comment_add() -> str:
    return (
        "--- a/src/mod.py\n"
        "+++ b/src/mod.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def add(a, b):\n"
        "+    # a comment the test does not see\n"
        "     return a + b\n"
    )


def _manifest(**kw) -> dict:
    base = {
        "feature": "f",
        "base_ref": "HEAD",
        "pythonpath": "src",
        "test_command": [sys.executable, "-m", "pytest", "-q", "{test}"],
        "control": {
            "id": "M0",
            "file": "src/mod.py",
            "patch": _break_add(),
            "invariant": "control",
            "test": "tests/test_mod.py::test_add",
        },
        "mutants": [
            {
                "id": "M1",
                "file": "src/mod.py",
                "patch": _break_add(),
                "invariant": "I-1",
                "test": "tests/test_mod.py::test_add",
            },
        ],
    }
    base.update(kw)
    return base


def _run(tmp_path: Path, manifest: dict, repo: Path) -> tuple[int, list[dict]]:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "matrix.jsonl"
    code = MP.run_manifest(path, repo, worktree_root=tmp_path / "wt", out=out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return code, rows


def test_classify_exit_codes() -> None:
    assert MP.classify(0) == "blind-test"
    assert MP.classify(1) == "pinned"
    assert MP.classify(5) == "no-test"
    assert MP.classify(2) == "error"


def test_validate_manifest_reports_missing_fields() -> None:
    problems = MP.validate_manifest({"mutants": []})
    assert any("control" in p for p in problems)
    assert any("test_command" in p for p in problems)


def test_control_red_then_pinned_and_blind(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(mutants=[
        {
            "id": "M1",
            "file": "src/mod.py",
            "patch": _break_add(),
            "invariant": "I-1",
            "test": "tests/test_mod.py::test_add",
        },
        {
            "id": "M2",
            "file": "src/mod.py",
            "patch": _comment_add(),
            "invariant": "I-2",
            "test": "tests/test_mod.py::test_add",
        },
    ])
    code, rows = _run(tmp_path, manifest, repo)
    assert code == 1
    assert [r["id"] for r in rows] == ["M0", "M1", "M2"]
    assert rows[0]["outcome"] == "red"
    assert rows[1]["outcome"] == "pinned"
    assert rows[2]["outcome"] == "blind-test"
    assert rows[2]["diff"] == _comment_add()
    worktrees = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    assert len(worktrees) == 1


def test_control_green_is_inconclusive_and_stops(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(control={
        "id": "M0",
        "file": "src/mod.py",
        "patch": _comment_add(),
        "invariant": "control",
        "test": "tests/test_mod.py::test_add",
    })
    code, rows = _run(tmp_path, manifest, repo)
    assert code == 1
    assert len(rows) == 1
    assert rows[0]["outcome"] == "inconclusive"


def test_control_no_tests_is_inconclusive(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(control={
        "id": "M0",
        "file": "src/mod.py",
        "patch": _break_add(),
        "invariant": "control",
        "test": "tests/test_empty.py",
    })
    code, rows = _run(tmp_path, manifest, repo)
    assert code == 1
    assert len(rows) == 1
    assert rows[0]["outcome"] == "inconclusive"


def test_no_tests_collected_is_no_test(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(control={
        "id": "M0",
        "file": "src/mod.py",
        "patch": _break_add(),
        "invariant": "control",
        "test": "tests/test_mod.py::test_add",
    }, mutants=[{
        "id": "M1",
        "file": "src/mod.py",
        "patch": _break_add(),
        "invariant": "I-1",
        "test": "tests/test_empty.py",
    }])
    code, rows = _run(tmp_path, manifest, repo)
    assert code == 1
    assert rows[1]["outcome"] == "no-test"


def test_main_exits_two_on_invalid_manifest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = tmp_path / "bad.json"
    path.write_text('{"mutants": []}\n', encoding="utf-8")
    assert MP.main([str(path), "--repo-root", str(repo)]) == 2
