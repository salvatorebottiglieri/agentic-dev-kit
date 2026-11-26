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
import os
import shutil
import sqlite3
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


def _report_store_module():
    spec = importlib.util.spec_from_file_location("rs_for_prover_tests", TOOL.parent.parent / "report_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, manifest: dict, repo: Path) -> tuple[int, list[dict]]:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    store = tmp_path / "run-store" / "r.sqlite"
    code = MP.run_manifest(path, repo, worktree_root=tmp_path / "wt", store=store, name="run")
    rs = _report_store_module()
    conn = rs.open_store(store)
    try:
        rows = rs.matrix_rows(conn, "run") or []
    finally:
        conn.close()
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
    store = tmp_path / "s" / "r.sqlite"
    # witness: a `--name` and a store are given, so the exit 2 is the manifest's, not the usage's
    assert MP.main([str(path), "--repo-root", str(repo), "--name", "x", "--store", str(store)]) == 2
    assert not store.exists()


# --- #55: the prover and the store -------------------------------------------

ROOT = TOOL.parent.parent


def _store_rows(store: Path, name: str) -> list[tuple]:
    if not store.is_file():
        return []
    with sqlite3.connect(store) as c:
        return c.execute("SELECT mutant_id, kind, outcome, output_tail FROM mutation_result"
                         " WHERE name = ? ORDER BY seq", (name,)).fetchall()


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _main(tmp_path: Path, repo: Path, *args: str) -> int:
    return MP.main([*args, "--repo-root", str(repo), "--worktree-root", str(tmp_path / "wt")])


def test_the_prover_stores_its_matrix_under_a_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = tmp_path / "s" / "r.sqlite"
    manifest = _manifest()
    path = _write_manifest(tmp_path, manifest)
    code = _main(tmp_path, repo, str(path), "--name", "m1", "--store", str(store))
    assert code == 0
    rows = _store_rows(store, "m1")
    assert [(i, k, o) for i, k, o, _ in rows] == [("M0", "control", "red"), ("M1", "mutant", "pinned")]
    # witness: a mutant that stays green — stored as blind-test with exit 1, the matrix kept
    # anyway (evidence); distinguishes "store only an all-pinned matrix" from "store what ran"
    blind = _manifest(mutants=[{"id": "M1", "file": "src/mod.py", "patch": _comment_add(),
                                "invariant": "I-1", "test": "tests/test_mod.py::test_add"}])
    code = _main(tmp_path, repo, str(_write_manifest(tmp_path, blind)), "--name", "m2", "--store", str(store))
    assert code == 1
    assert [(i, o) for i, _, o, _ in _store_rows(store, "m2")] == [("M0", "red"), ("M1", "blind-test")]
    # a name is written once: the same name again is exit 2, the rows unchanged
    assert _main(tmp_path, repo, str(path), "--name", "m1", "--store", str(store)) == 2
    assert len(_store_rows(store, "m1")) == 2


def test_the_prover_reads_its_manifest_from_the_store(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = tmp_path / "s" / "r.sqlite"
    spec = importlib.util.spec_from_file_location("rs_under_test", ROOT / "report_store.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    conn = rs.open_store(store)
    with conn:
        conn.execute("INSERT INTO mutation_manifest VALUES ('m1', 't', ?)", (json.dumps(_manifest()),))
    conn.close()
    code = _main(tmp_path, repo, "--manifest-name", "m1", "--name", "out1", "--store", str(store))
    assert code == 0
    assert [(i, o) for i, _, o, _ in _store_rows(store, "out1")] == [("M0", "red"), ("M1", "pinned")]
    # witness: both a file and a stored name, neither, and a name that is not stored — each is
    # exit 2; distinguishes "exactly one source" from "the file wins" / "the store wins"
    path = _write_manifest(tmp_path, _manifest())
    assert _main(tmp_path, repo, str(path), "--manifest-name", "m1", "--store", str(store)) == 2
    assert _main(tmp_path, repo, "--name", "x", "--store", str(store)) == 2
    assert _main(tmp_path, repo, "--manifest-name", "nope", "--store", str(store)) == 2


def test_a_stored_matrix_keeps_the_last_30_lines_of_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = tmp_path / "s" / "r.sqlite"
    printer = "import sys\nfor i in range(1, 46):\n    print(f'line {i}')\nsys.exit(1)\n"
    manifest = _manifest(test_command=[sys.executable, "-c", printer])
    _main(tmp_path, repo, str(_write_manifest(tmp_path, manifest)), "--name", "m1", "--store", str(store))
    rows = _store_rows(store, "m1")
    assert [i for i, *_ in rows] == ["M0", "M1"]
    # witness: 45 numbered lines on stdout — the tail is exactly lines 16-45; distinguishes
    # "last 30 lines" from "everything" (line 1 present) and from "first 30" (line 45 absent)
    assert rows[1][3].splitlines() == [f"line {i}" for i in range(16, 46)]


def test_the_prover_without_the_report_store_module_exits_two(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    kit = tmp_path / "kit"
    (kit / "guards").mkdir(parents=True)
    shutil.copy(TOOL, kit / "guards" / "mutation_prover.py")
    path = _write_manifest(tmp_path, _manifest())
    env = {k: v for k, v in os.environ.items() if k != "AGENT_REPORT_STORE"}
    # witness: report_store.py absent next to the copy (the public kit until #20) — `--name` says
    # why it cannot work and nothing is created; distinguishes "load the store only for --name"
    # from a silent success without a store
    named = subprocess.run(
        [sys.executable, str(kit / "guards" / "mutation_prover.py"), str(path), "--repo-root", str(repo),
         "--worktree-root", str(tmp_path / "wt"), "--name", "m1", "--store", str(tmp_path / "s.sqlite")],
        capture_output=True, text=True, check=False, env=env)
    assert named.returncode == 2 and "report_store" in named.stderr, named.stderr
    assert not (tmp_path / "s.sqlite").exists()


def test_the_prover_refuses_to_run_without_a_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_manifest(tmp_path, _manifest())
    wt = tmp_path / "wt"

    def prove(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TOOL), *args, "--repo-root", str(repo),
                               "--worktree-root", str(wt)], capture_output=True, text=True, check=False)

    def worktrees() -> list[str]:
        listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True,
                                text=True, check=True).stdout
        return [ln for ln in listed.splitlines() if ln.startswith("worktree ")]

    before = worktrees()
    # witness: a valid manifest that would run to a pinned matrix — without `--name` it must exit 2
    # with the usage and run nothing: no worktree root, no `git worktree add`, no matrix on stdout
    bare = prove(str(path))
    assert bare.returncode == 2, (bare.stdout, bare.stderr)
    assert "usage: mutation_prover.py" in bare.stderr and "--name NAME" in bare.stderr
    assert bare.stdout == "" and not wt.exists() and worktrees() == before
    # `--out` is gone: an unknown flag is exit 2 with the usage too, even beside a good `--name`
    store = tmp_path / "s" / "r.sqlite"
    out = tmp_path / "f.jsonl"
    old = prove(str(path), "--out", str(out), "--name", "m1", "--store", str(store))
    assert old.returncode == 2 and "usage: mutation_prover.py" in old.stderr, (old.stdout, old.stderr)
    assert not wt.exists() and not out.exists() and not store.exists() and worktrees() == before


def test_the_prover_refuses_a_stored_name_before_running_anything(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = tmp_path / "s" / "r.sqlite"
    assert _main(tmp_path, repo, str(_write_manifest(tmp_path, _manifest())),
                 "--name", "m1", "--store", str(store)) == 0
    marker = tmp_path / "ran.marker"
    probe = _manifest(test_command=[sys.executable, "-c",
                                    f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')"])
    code = _main(tmp_path, repo, str(_write_manifest(tmp_path, probe)), "--name", "m1", "--store", str(store))
    assert code == 2
    assert not marker.exists()
    assert not (tmp_path / "wt" / "M0").exists()
