#!/usr/bin/env python3
"""mutation_prover — execute the mutants the agent chose (B-026).

The split is deliberate: the **agent** decides which plausible-wrong
implementations are worth trying and writes them as patches in a manifest;
this **script** applies each patch in a clean worktree, runs the targeted
test, and emits the matrix. The mechanical part no longer depends on the
LLM remembering to revert a mutant or on an editable venv pointing at the
session tree.

    python3 guards/mutation_prover.py <manifest.json> --name NAME [--repo-root .]
        [--worktree-root DIR] [--store PATH]
    python3 guards/mutation_prover.py --manifest-name NAME --name NAME
        [--store PATH] [--repo-root .]

The matrix is stored in the report store under ``--name`` (required: the matrix exists
nowhere else; without it the prover exits 2 with this usage before anything runs). A name
is written once; the matrix is stored even when it is not all pinned, and each row keeps
the last 30 lines of its output. ``--manifest-name`` reads the manifest from the store
instead of a file; give exactly one of the two. The store is ``--store``, else
``$AGENT_REPORT_STORE``, else the XDG default. A ``--name`` already stored is refused
(exit 2) before any mutant runs.

The prover needs ``report_store.py`` at the root of this guard's install (the parent of
``guards/``). The public kit does not ship it until #20 exports it, so there the prover
exits 2.

Manifest:

    {
      "feature": "<name>",
      "base_ref": "HEAD",
      "pythonpath": "src",
      "test_command": ["python", "-m", "pytest", "-q", "{test}"],
      "control": {
        "id": "M0", "file": "src/x.py", "patch": "--- a/...",
        "invariant": "control", "test": "tests/test_x.py::test_y"
      },
      "mutants": [
        {"id": "M1", "file": "src/x.py", "patch": "--- a/...",
         "invariant": "I-impl-1", "test": "tests/test_x.py::test_y",
         "note": "normalise instead of pass through"}
      ]
    }

The control runs **first**. If it stays green, the matrix is
``inconclusive`` and the script exits 1: a test that cannot go red proves
nothing, so the other mutants are not worth running. Mutant outcomes:
``pinned`` (the test went red), ``blind-test`` (the test stayed green),
``no-test`` (pytest collected nothing), ``error`` (patch or command failed).

Exit 0 only when the control is red and every mutant is ``pinned``;
exit 1 on a non-pinned outcome; exit 2 on a bad manifest or setup.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _validate_mutant(m: object, where: str) -> list[str]:
    if not isinstance(m, dict):
        return [f"{where} must be an object"]
    problems = []
    for field in ("id", "file", "patch", "test"):
        value = m.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{where}.{field} is required")
    patch = m.get("patch") or ""
    if isinstance(patch, str) and patch and ("---" not in patch or "+++" not in patch):
        problems.append(f"{where}.patch must be a unified diff (---/+++)")
    return problems


def validate_manifest(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    problems: list[str] = []
    command = data.get("test_command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(c, str) and c for c in command)):
        problems.append("test_command must be a non-empty list of strings")
    control = data.get("control")
    if not isinstance(control, dict):
        problems.append("control is required")
    else:
        problems += _validate_mutant(control, "control")
    mutants = data.get("mutants")
    if not isinstance(mutants, list) or not mutants:
        problems.append("mutants must be a non-empty list")
    else:
        for i, mutant in enumerate(mutants):
            problems += _validate_mutant(mutant, f"mutants[{i}]")
    ids = [m.get("id") for m in [control] + (mutants if isinstance(mutants, list) else [])
           if isinstance(m, dict)]
    if len(ids) != len(set(ids)):
        problems.append("mutant ids must be unique")
    return problems


def _load_store():
    """The report_store module at the root of this guard's install (loaded only for --name/--manifest-name)."""
    path = Path(__file__).resolve().parent.parent / "report_store.py"
    found = importlib.util.spec_from_file_location("mutation_prover_report_store", path)
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def classify(returncode: int) -> str:
    """Map a test run to the matrix outcome. Pytest exit codes are stable:
    0 = all passed, 1 = tests failed, 5 = no tests collected."""
    if returncode == 0:
        return "blind-test"
    if returncode == 1:
        return "pinned"
    if returncode == 5:
        return "no-test"
    return "error"


def _tail(text: str, limit: int = 2000) -> str:
    return (text or "")[-limit:]


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _worktree_add(repo: Path, worktree: Path, base_ref: str) -> tuple[bool, str]:
    if worktree.exists():
        shutil.rmtree(worktree)
    proc = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), base_ref],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0, proc.stderr


def _worktree_remove(repo: Path, worktree: Path) -> None:
    proc = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 and worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"], cwd=repo, capture_output=True, text=True,
        check=False,
    )


def _test_command(manifest: dict, mutant: dict) -> list[str]:
    command = list(manifest["test_command"])
    command = [sys.executable if c == "python" else c for c in command]
    if any("{test}" in c for c in command):
        return [c.replace("{test}", mutant["test"]) for c in command]
    return command + [mutant["test"]]


def _run_one(
    manifest: dict, mutant: dict, kind: str, repo: Path, worktree_root: Path,
    base_ref: str,
) -> dict:
    worktree = worktree_root / f"wt-{mutant['id']}"
    row = {
        "id": mutant["id"],
        "kind": kind,
        "invariant": mutant.get("invariant", ""),
        "test": mutant["test"],
        "diff": mutant["patch"],
        "note": mutant.get("note", ""),
    }
    ok, error = _worktree_add(repo, worktree, base_ref)
    if not ok:
        row.update({"outcome": "error", "error": f"git worktree add failed: {error}"})
        return row
    try:
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"], cwd=worktree,
            input=mutant["patch"], text=True, capture_output=True, check=False,
        )
        if applied.returncode != 0:
            row.update({
                "outcome": "error",
                "error": f"git apply failed: {applied.stderr}",
            })
            return row
        env = os.environ.copy()
        pythonpath = manifest.get("pythonpath")
        if not pythonpath and (worktree / "src").is_dir():
            pythonpath = "src"
        if pythonpath:
            path = str((worktree / str(pythonpath)).resolve())
            env["PYTHONPATH"] = path + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        command = _test_command(manifest, mutant)
        proc = _run(command, worktree, env)
        outcome = classify(proc.returncode)
        if kind == "control":
            if proc.returncode == 1:
                outcome = "red"
            elif proc.returncode in (0, 5):
                outcome = "inconclusive"
            else:
                outcome = "error"
        row.update({
            "outcome": outcome,
            "exit_code": proc.returncode,
            "command": command,
            "stdout": _tail(proc.stdout),
            "stderr": _tail(proc.stderr),
        })
        return row
    finally:
        _worktree_remove(repo, worktree)


def _store_matrix(store: Path | None, name: str, rows: list[dict]) -> int:
    """Append the matrix to the store: 0, or 2 with a reason on stderr."""
    try:
        module = _load_store()
    except OSError as exc:
        print(f"mutation_prover: cannot store matrix {name}: {exc}", file=sys.stderr)
        return 2
    if store is None:
        print(f"mutation_prover: cannot store matrix {name}: no store path given", file=sys.stderr)
        return 2
    try:
        conn = module.open_store(store)
        try:
            module.add_matrix(conn, name, rows, datetime.now())
        finally:
            conn.close()
    except module.BatchRejected as exc:
        reasons = exc.reasons
    except (OSError, sqlite3.Error) as exc:
        reasons = [str(exc)]
    else:
        print(f"mutation_prover: matrix {name} stored", file=sys.stderr)
        return 0
    for reason in reasons:
        print(f"mutation_prover: cannot store matrix {name}: {reason}", file=sys.stderr)
    return 2


def run_manifest(
    manifest_path: Path | None, repo_root: Path, worktree_root: Path | None = None,
    store: Path | None = None, *, name: str, manifest: dict | None = None,
) -> int:
    if manifest is not None:
        data = manifest
    else:
        manifest_path = Path(manifest_path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"mutation_prover: cannot read manifest {manifest_path}: {exc}",
                  file=sys.stderr)
            return 2
    problems = validate_manifest(data)
    if problems:
        print("mutation_prover: invalid manifest:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    repo = Path(repo_root).resolve()
    if not (repo / ".git").exists():
        print(f"mutation_prover: {repo} is not a git repository", file=sys.stderr)
        return 2
    base_ref = data.get("base_ref", "HEAD")
    own_root = worktree_root is None
    root = Path(worktree_root) if worktree_root is not None else Path(
        tempfile.mkdtemp(prefix="mutprove-")
    )
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    try:
        control = _run_one(data, data["control"], "control", repo, root, base_ref)
        rows.append(control)
        if control["outcome"] == "red":
            for mutant in data["mutants"]:
                rows.append(_run_one(manifest=data, mutant=mutant, kind="mutant",
                                     repo=repo, worktree_root=root, base_ref=base_ref))
    finally:
        if own_root:
            shutil.rmtree(root, ignore_errors=True)

    if _store_matrix(store, name, rows) != 0:
        return 2

    control_ok = rows[0]["outcome"] == "red"
    mutants_ok = all(row["outcome"] == "pinned" for row in rows[1:])
    if not control_ok:
        print("mutation_prover: control did not go red — matrix inconclusive",
              file=sys.stderr)
        return 1
    if not mutants_ok:
        print("mutation_prover: non-pinned mutant(s): " + ", ".join(
            f"{row['id']}={row['outcome']}" for row in rows[1:]
            if row["outcome"] != "pinned"
        ), file=sys.stderr)
        return 1
    print(f"mutation_prover: {len(rows) - 1} mutant(s) pinned", file=sys.stderr)
    return 0


def _usage() -> int:
    print("usage: mutation_prover.py (<manifest.json> | --manifest-name NAME) --name NAME "
          "[--repo-root .] [--worktree-root DIR] [--store PATH]", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    positional: list[str] = []
    value_flags = {"--repo-root", "--worktree-root", "--store", "--name", "--manifest-name"}
    given: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg in value_flags and i + 1 < len(argv):
            given[arg] = argv[i + 1]; i += 2; continue
        flag, _, value = arg.partition("=")
        if flag in value_flags and "=" in arg:
            given[flag] = value; i += 1; continue
        if arg.startswith("--"):
            return _usage()
        positional.append(arg); i += 1
    repo_root = Path(given.get("--repo-root", "."))
    worktree_root = Path(given["--worktree-root"]) if "--worktree-root" in given else None
    name, manifest_name = given.get("--name"), given.get("--manifest-name")
    store = Path(given["--store"]) if "--store" in given else None
    if bool(positional) == (manifest_name is not None) or name is None:
        return _usage()
    manifest = None
    try:
        module = _load_store()
        store = store or module.default_store_path(os.environ, Path.home())
        conn = module.open_existing(store)
        try:
            if conn is not None and module.matrix_rows(conn, name) is not None:
                print(f"mutation_prover: matrix {name} is already stored; nothing was run",
                      file=sys.stderr)
                return 2
            if manifest_name is not None:
                manifest = None if conn is None else module.manifest(conn, manifest_name)
        finally:
            if conn is not None:
                conn.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"mutation_prover: cannot use the report store: {exc}", file=sys.stderr)
        return 2
    if manifest_name is not None and manifest is None:
        print(f"mutation_prover: no manifest {manifest_name} in the store {store}", file=sys.stderr)
        return 2
    return run_manifest(Path(positional[0]) if positional else None, repo_root, worktree_root,
                        store, name=name, manifest=manifest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
