#!/usr/bin/env python3
"""mutation_prover — execute the mutants the agent chose (B-026).

The split is deliberate: the **agent** decides which plausible-wrong
implementations are worth trying and writes them as patches in a manifest;
this **script** applies each patch in a clean worktree, runs the targeted
test, and emits the matrix. The mechanical part no longer depends on the
LLM remembering to revert a mutant or on an editable venv pointing at the
session tree.

    python3 guards/mutation_prover.py <manifest.json> [--repo-root .]
        [--worktree-root DIR] [--out reports/<feature>-mutation.jsonl]

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

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
            outcome = "red" if proc.returncode == 1 else (
                "inconclusive" if proc.returncode == 0 else "error"
            )
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


def run_manifest(
    manifest_path: Path, repo_root: Path, worktree_root: Path | None = None,
    out: Path | None = None,
) -> int:
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

    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

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


def main(argv: list[str]) -> int:
    positional: list[str] = []
    repo_root = Path(".")
    worktree_root: Path | None = None
    out: Path | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg == "--repo-root" and i + 1 < len(argv):
            repo_root = Path(argv[i + 1]); i += 2; continue
        if arg == "--worktree-root" and i + 1 < len(argv):
            worktree_root = Path(argv[i + 1]); i += 2; continue
        if arg == "--out" and i + 1 < len(argv):
            out = Path(argv[i + 1]); i += 2; continue
        if arg.startswith("--repo-root="):
            repo_root = Path(arg.split("=", 1)[1]); i += 1; continue
        if arg.startswith("--worktree-root="):
            worktree_root = Path(arg.split("=", 1)[1]); i += 1; continue
        if arg.startswith("--out="):
            out = Path(arg.split("=", 1)[1]); i += 1; continue
        positional.append(arg); i += 1
    if not positional:
        print("usage: mutation_prover.py <manifest.json> [--repo-root .] "
              "[--worktree-root DIR] [--out FILE]", file=sys.stderr)
        return 2
    return run_manifest(Path(positional[0]), repo_root, worktree_root, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
