"""check_rationale_required: pre-commit gate that makes a rule change
without RATIONALE.md entry **impossible to commit**.

This is the **structure-level** enforcement of B-025 voce 14: the
previous level was `audit.py check_rationale_entry_in_pr` (post-hoc
script, runs on PRs). The hook here runs *before* the commit lands,
so a rule change without a rationale entry is **rejected by git**,
not flagged after the fact.

Install: `install.sh --gate <repo>` sets `core.hooksPath` to
`guards/hooks/`, and the hook shim `pre-commit` chains this script
before `guards/gate.py`.

Behavior:
- Stages are read with `git diff --cached --name-only --diff-filter=ACMRD`
  (Added/Copied/Modified/Renamed/Deleted — every change to a rule-registry
  file triggers the gate, including deletions: removing a rule is
  a change).
- A change to `phases/<name>.md` always requires a `RATIONALE.md`
  update in the same commit. Phases are rule files by definition.
- A change to `WORKFLOW.md` requires a `RATIONALE.md` update: WORKFLOW.md
  *contains* the rule registry (the "Where each rule is enforced"
  table). The check accepts either as the rationale trigger.
- `--no-verify` (a documented escape hatch) skips the hook; the
  `audit.py check_rationale_entry_in_pr` still catches the violation
  on the next PR.

Anti-vacuity (D-002, D-024): `tests/test_rationale_required.py`
mutation-tests the gate (deleting the check, swapping the path
regex, etc.) — see `tests/`.

Stdlib only: subprocess, re, sys. No third-party deps.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Files that count as "rule registry". A change to any of them
# requires a RATIONALE.md update in the same commit.
RULE_REGISTRY_PATHS = (
    re.compile(r"^phases/[^\s/]+\.md$"),
    re.compile(r"^WORKFLOW\.md$"),
)
RATIONALE_RE = re.compile(r"^RATIONALE\.md$")


def staged_files(repo_root: Path) -> list[str]:
    """Return the list of files staged for the next commit, in
    `--name-only` form. Includes Added, Copied, Modified, Renamed
    and Deleted (a deletion of a rule file is also a change)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRD"],
        cwd=repo_root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        # outside a git repo: nothing to check
        return []
    return [f for f in result.stdout.splitlines() if f]


def find_violation(staged: list[str]) -> str | None:
    """If the staged set touches the rule registry but not
    RATIONALE.md, return a human-readable violation string. Otherwise
    return None. Pure: easy to mutation-test."""
    touches_registry = any(
        rx.match(f) for f in staged for rx in RULE_REGISTRY_PATHS
    )
    if not touches_registry:
        return None
    has_rationale = any(RATIONALE_RE.match(f) for f in staged)
    if has_rationale:
        return None
    touched = [f for f in staged if any(rx.match(f) for rx in RULE_REGISTRY_PATHS)]
    return (
        "rule-registry change without a RATIONALE.md entry: "
        f"touched {touched!r} but RATIONALE.md is not in the staged set. "
        "Either stage RATIONALE.md or use `git commit --no-verify` "
        "(and accept the audit.py check_rationale_entry_in_pr will "
        "flag the PR on GitHub)."
    )


def main() -> int:
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        ).stdout.strip() or "."
    )
    staged = staged_files(Path(repo_root))
    violation = find_violation(staged)
    if violation is None:
        return 0
    print(f"check_rationale_required: {violation}", file=sys.stderr)
    return 2  # 2 = blocked, distinguishable from script error (1)


if __name__ == "__main__":
    sys.exit(main())
