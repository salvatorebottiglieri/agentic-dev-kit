#!/usr/bin/env python3
"""audit.py — workflow invariant checks, transversal across projects.

Pure check functions (input data -> violations) + a CLI that gathers data
from the configured tracker via ``gh``. Run in any project:

    python ~/agentic-dev-kit/audit.py --repo owner/repo [--since YYYY-MM-DD]

Each check maps to an invariant in INVARIANTS.md; every check must be able
to fail (see tests/test_audit.py — mutation tests feed deliberately
violating inputs and assert the violation is detected).
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent
HANDOFFS_DIR = WORKFLOW_DIR / "handoffs"
ISSUE_REF_RE = re.compile(r"#\d+")


# ── Pure checks ──────────────────────────────────────────────────────


def check_pr_links_issue(prs: list[dict]) -> list[str]:
    """I4 — every merged PR links at least one issue/ticket."""
    violations = []
    for pr in prs:
        if not pr.get("merged"):
            continue
        body = pr.get("body") or ""
        if not ISSUE_REF_RE.search(body):
            violations.append(
                f"[I4] PR #{pr['number']} merged but links no issue in its body"
            )
    return violations


def check_ci_before_merge(prs: list[dict]) -> list[str]:
    """I1 — every merged PR has all CI checks successful (best-effort)."""
    violations = []
    for pr in prs:
        if not pr.get("merged"):
            continue
        rollup = pr.get("statusCheckRollup") or []
        if not rollup:
            continue  # no check data — cannot verify, not a violation
        for check in rollup:
            conclusion = (check.get("conclusion") or "").upper()
            if conclusion not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                name = check.get("name") or check.get("context") or "?"
                violations.append(
                    f"[I1] PR #{pr['number']} merged with CI check "
                    f"'{name}' = {conclusion}"
                )
    return violations


def check_skip_annotations(issues: list[dict]) -> list[str]:
    """I3 — closed non-trivial issues carry a spec section or a skip note."""
    # Marker set = the actual spec/ticket formats used across the workflow:
    # spec issues (Problem Statement/Solution), tickets (What to build/
    # Acceptance), architecture/invariants sections, program design.
    SPEC_MARKERS = (
        "## Problem Statement",
        "## Solution",
        "## What to build",
        "## Acceptance",
        "## System Architecture",
        "## Invariants",
        "## Program Design",
    )
    violations = []
    for issue in issues:
        if issue.get("state") != "CLOSED":
            continue
        # The clause (INVARIANTS.md § I3) accepts the annotation in the body
        # *or* in a comment, so the search text is body plus comment bodies.
        comments = issue.get("comments") or []
        text = "\n".join(
            [issue.get("body") or "", *((c.get("body") or "") for c in comments)]
        )
        if "Skipped" in text or any(m in text for m in SPEC_MARKERS):
            continue
        violations.append(
            f"[I3] issue #{issue['number']} closed without a spec section "
            "or 'Skipped — reason' annotation (review: is it trivial?)"
        )
    return violations


def _handoff_date(name: str) -> datetime | None:
    """Parse the YYYYMMDD stamp out of a handoff filename."""
    m = re.search(r"(\d{8})", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return None


def check_recent_handoff(handoffs: list[str], days: int = 7) -> list[str]:
    """I5 — a handoff exists in the canonical corpus for the recent window."""
    cutoff = datetime.now() - timedelta(days=days)
    recent = [
        name for name in handoffs
        if (date := _handoff_date(name)) and date >= cutoff
    ]
    if not recent:
        return [
            f"[I5] no handoff in {HANDOFFS_DIR} within the last {days} days"
        ]
    return []


#: Quality gates a repo can configure, and the config section that declares them.
GATE_DECLARATIONS = {
    "mypy": r"^\s*\[(tool\.)?mypy\]",
    "ruff": r"^\s*\[(tool\.)?ruff\]",
}

#: Words that name a follow-up when they *label* a line.
FOLLOWUP_MARKERS = (
    "follow-up", "followup", "advisory", "non bloccante", "non-blocking",
)

#: Bullet, number or bold marker, then the label at the start of the line.
ANCHORED_MARKER_RE = re.compile(
    r"^\s*(?:[-*+>]\s+|\d+[.)]\s+)?\**\s*(?:"
    + "|".join(re.escape(m) for m in FOLLOWUP_MARKERS)
    + r")\b",
    re.IGNORECASE,
)

#: A section heading that says "what follows are the pending items". Inside
#: such a section any marker counts, label or prose. Same asymmetry as the
#: lines: the unambiguous words count anywhere in the heading, the ambiguous
#: one only when the heading starts with it.
FOLLOWUP_SECTION_RE = re.compile(
    r"^#{1,4}\s*(?:.{0,40}?\b(?:next session|next steps?|follow-?ups?|"
    r"prossim[oa] session[ei]|da fare|to ?do)\b"
    r"|advisory\b)",
    re.IGNORECASE,
)

#: Any markdown heading — the boundary between one section's rules and another's.
HEADING_RE = re.compile(r"^#{1,6}\s")

#: Ways a handoff says "there are none" instead of citing a ticket.
NONE_MARKERS = ("none", "nessuno", "nessuna", "n/a")

#: A backticked command, or an open fence — the weak signal I10 settles for.
QUOTED_COMMAND_RE = re.compile(
    r"`[^`\n]*(?:pytest|mypy|ruff|uv |python|gh |git |npm|flutter|docker)[^`\n]*`"
    r"|^```"
)

#: The repo a handoff declares it concerns, which is what scopes I9 to a
#: project (see ``check_followups_tracked``). Canonical form:
#: ``- **Repo**: Example/a-data-pipeline``. Bullet, numbering,
#: bold label and label case are tolerated — a handoff is a prose document —
#: while the slug is taken literally. Prefer the canonical form when writing.
HANDOFF_REPO_RE = re.compile(
    r"^\s*(?:[-*+>]\s+|\d+[.)]\s+)?\**\s*repo\s*\**\s*:\s*\**\s*([\w.-]+/[\w.-]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _handoff_repo(text: str) -> str | None:
    """The repo a handoff declares, or None when it declares none."""
    match = HANDOFF_REPO_RE.search(text or "")
    return match.group(1) if match else None


def check_configured_gates_run_in_ci(config: dict) -> list[str]:
    """I8 — every quality tool the repo configures is executed by CI.

    I1 ("merged only on green CI") is vacuously satisfied by a CI that runs
    no gates at all: green because nothing is checked. This is the check
    that stops the vacuity.
    """
    ci_text = config.get("ci_text") or ""
    if not ci_text:
        return []  # no CI data — cannot verify, not a violation (cf. I1)
    violations = []
    for tool, where in sorted((config.get("configured") or {}).items()):
        if not re.search(rf"\b{re.escape(tool)}\b", ci_text):
            violations.append(
                f"[I8] {tool} is configured in {where} but no CI workflow runs it"
            )
    return violations


def check_followups_tracked(
    handoffs: list[dict], known_issues: set[int], repo: str
) -> list[str]:
    """I9 — a follow-up named in a handoff carries a resolving ticket ref.

    Scoped to the audited *repo*. The corpus of handoffs is shared by every
    project, while ``known_issues`` resolves on one tracker only: judging a
    handoff of another project against it reports a violation for refs that
    resolve perfectly well at home (2026-09-19, the third false-positive
    class of this check). A handoff therefore declares the repo it concerns
    (``repo`` field, filled from the ``Repo:`` line WORKFLOW.md requires) and
    only the handoffs declaring the audited repo are read. A handoff that
    declares none is *cannot verify* — not a violation — for the same reason
    an unreadable channel is: the check has no evidence, not counter-
    evidence. The cost is zero coverage for handoffs written before the
    convention, which is what the convention is for.

    A follow-up is *named* in one of two ways, and prose is neither:

    - a marker labels the line — bullet, number or bold marker, then the word
      (`- Advisory: …`), wherever that line sits;
    - any marker appears inside a section whose heading says the pending items
      are these (`## Next session`, `## Follow-up`, …).

    A sentence in Delivered work or Verification that merely contains the word
    ("Ponytail 3 advisory, all applied", "I9 stops reading a severity as a
    follow-up") is not a follow-up: that conflation produced two false
    violations on 2026-09-17, one per marker.
    """
    violations = []
    for handoff in handoffs:
        declared = (handoff.get("repo") or "").lower()
        if declared != repo.lower():
            continue
        name = handoff.get("name", "?")
        in_followup_section = False
        for lineno, line in enumerate((handoff.get("text") or "").splitlines(), 1):
            if HEADING_RE.match(line):
                in_followup_section = bool(FOLLOWUP_SECTION_RE.match(line))
                continue
            low = line.lower()
            named = ANCHORED_MARKER_RE.match(line) or (
                in_followup_section
                and any(marker in low for marker in FOLLOWUP_MARKERS)
            )
            if not named:
                continue
            refs = [int(n) for n in re.findall(r"#(\d+)", line)]
            if refs:
                for ref in refs:
                    if ref not in known_issues:
                        violations.append(
                            f"[I9] {name}:{lineno} cites follow-up #{ref}, "
                            "which does not resolve on the tracker"
                        )
            elif not any(marker in low for marker in NONE_MARKERS):
                violations.append(
                    f"[I9] {name}:{lineno} names a follow-up with no "
                    f"tracker reference: {line.strip()[:80]}"
                )
    return violations


def check_handoffs_quote_commands(handoffs: list[dict]) -> list[str]:
    """I10 (weak) — a handoff names the command behind a stated result.

    Weak by construction: it checks for the *presence* of a quoted command,
    not that the command is the one that produced the reported result. It
    still fails on a handoff that asserts an outcome with no command at all,
    which is the failure mode it exists for.
    """
    violations = []
    for handoff in handoffs:
        text = handoff.get("text") or ""
        if "## Verification" not in text and "## Verifica" not in text:
            continue  # no verification section — I10 does not apply
        if not QUOTED_COMMAND_RE.search(text):
            violations.append(
                f"[I10] {handoff.get('name', '?')} has a verification section "
                "but quotes no command"
            )
    return violations


# ── Data gathering (gh) ──────────────────────────────────────────────


def _gh(args: list[str]) -> dict:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr[:200]}")
    return json.loads(proc.stdout)


def gather_prs(repo: str, since: str) -> list[dict]:
    """Merged PRs since *since*, with body + CI rollup."""
    prs = _gh([
        "pr", "list", "--repo", repo, "--state", "merged",
        "--search", f"merged:>={since}",
        "--json", "number,mergedAt,body,headRefOid",
    ])
    for pr in prs:
        try:
            detail = _gh([
                "pr", "view", str(pr["number"]), "--repo", repo,
                "--json", "state,statusCheckRollup",
            ])
        except RuntimeError:
            # The rollup is unreadable (token without check-run access): the
            # PRs come from `--state merged`, so the state is known, and the
            # same jobs are readable on the Actions runs channel.
            pr["merged"] = True
            pr["statusCheckRollup"] = _rollup_from_actions(
                repo, pr.get("headRefOid")
            )
            continue
        pr["merged"] = detail["state"] == "MERGED"
        pr["statusCheckRollup"] = detail.get("statusCheckRollup") or []
    return prs


def _rollup_from_actions(repo: str, sha: str | None) -> list[dict]:
    """Fallback for the PR check rollup, read from the Actions runs.

    `--json statusCheckRollup` needs a token allowed to read check runs; the
    runs channel only needs `Actions: read`, so it keeps I1 verifiable with a
    token that cannot read the rollup. Same underlying jobs, one row per
    workflow instead of one row per check.

    Only `pull_request` runs are the PR gate (a `push` run on the default
    branch is not), only completed runs have a conclusion, and the newest run
    wins — `gh run list` returns newest first. An unreadable channel returns
    [] ("cannot verify"), which ``check_ci_before_merge`` treats as not a
    violation; the rollup stays the primary channel.
    """
    if not sha:
        return []
    try:
        runs = _gh([
            "run", "list", "--repo", repo, "--commit", sha, "--limit", "50",
            "--json", "name,status,conclusion,event",
        ])
    except RuntimeError:
        return []
    rollup: list[dict] = []
    seen: set[str] = set()
    for run in runs:
        name = run.get("name") or "?"
        if run.get("event") != "pull_request":
            continue
        if run.get("status") != "completed":
            continue
        if name in seen:
            continue
        seen.add(name)
        rollup.append({"name": name, "conclusion": run.get("conclusion")})
    return rollup


def gather_issues(repo: str, since: str) -> list[dict]:
    return _gh([
        "issue", "list", "--repo", repo, "--state", "closed",
        "--search", f"closed:>={since}",
        "--json", "number,state,body,title,comments",
    ])


def _gh_text(args: list[str]) -> str:
    """Run gh and return raw stdout, for payloads that are not JSON."""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr[:200]}")
    return proc.stdout


def gather_gate_config(repo: str) -> dict:
    """Which quality gates the repo declares, and the text of its CI workflows.

    Best-effort: an API failure degrades to "cannot verify" rather than a
    violation, matching I1's handling of an empty rollup.
    """
    try:
        tree = _gh(["api", f"repos/{repo}/git/trees/HEAD?recursive=1"])
    except RuntimeError:
        return {"configured": {}, "ci_text": ""}

    paths = [e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"]
    config_paths = [
        p for p in paths
        if Path(p).name in ("pyproject.toml", "ruff.toml", "mypy.ini", "setup.cfg")
    ]
    workflow_paths = [
        p for p in paths
        if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
    ]

    def _contents(path: str) -> str | None:
        try:
            raw = _gh_text(["api", f"repos/{repo}/contents/{path}", "--jq", ".content"])
        except RuntimeError:
            return None
        # The contents API base64-encodes; tolerate an already-decoded body.
        if re.fullmatch(r"[A-Za-z0-9+/=\s]*", raw or ""):
            try:
                return base64.b64decode(raw).decode("utf-8", "replace")
            except (ValueError, TypeError):
                return None
        return raw

    configured: dict[str, str] = {}
    for path in config_paths:
        text = _contents(path)
        if text is None:
            continue
        for tool, pattern in GATE_DECLARATIONS.items():
            if tool not in configured and re.search(pattern, text, re.MULTILINE):
                configured[tool] = path

    ci_parts = [t for t in (_contents(p) for p in workflow_paths) if t]
    return {"configured": configured, "ci_text": "\n".join(ci_parts)}


def gather_handoffs(days: int = 7) -> list[dict]:
    """Text of the handoffs in the recent window, for the text-level checks."""
    cutoff = datetime.now() - timedelta(days=days)
    handoffs = []
    for path in sorted(HANDOFFS_DIR.glob("handoff-*.md")):
        date = _handoff_date(path.name)
        if date is None or date < cutoff:
            continue
        text = path.read_text(encoding="utf-8")
        handoffs.append(
            {"name": path.name, "text": text, "repo": _handoff_repo(text)}
        )
    return handoffs


def gather_known_refs(repo: str) -> set[int]:
    """Every number on the tracker — issues *and* PRs — for follow-up refs.

    Handoffs cite both kinds: "advisory ... (#270)" pointed at a PR, not an
    issue, so resolving only against issues produced a false violation.
    """
    refs: set[int] = set()
    for args in (
        ["issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number"],
        ["pr", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number"],
    ):
        try:
            refs.update(item["number"] for item in _gh(args))
        except RuntimeError:
            continue
    return refs


def main() -> int:
    args = sys.argv[1:]
    repo = None
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    i = 0
    while i < len(args):
        if args[i] == "--repo" and i + 1 < len(args):
            repo = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        else:
            i += 1
    if not repo:
        print("usage: audit.py --repo owner/repo [--since YYYY-MM-DD]", file=sys.stderr)
        return 2

    violations: list[str] = []
    try:
        prs = gather_prs(repo, since)
        issues = gather_issues(repo, since)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    violations += check_pr_links_issue(prs)
    violations += check_ci_before_merge(prs)
    violations += check_skip_annotations(issues)

    handoffs = sorted(p.name for p in HANDOFFS_DIR.glob("handoff-*.md"))
    violations += check_recent_handoff(handoffs)

    try:
        gate_config = gather_gate_config(repo)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    violations += check_configured_gates_run_in_ci(gate_config)

    recent_handoffs = gather_handoffs()
    violations += check_followups_tracked(
        recent_handoffs, gather_known_refs(repo), repo
    )
    violations += check_handoffs_quote_commands(recent_handoffs)

    if violations:
        print(f"{len(violations)} violation(s):")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("ok — all workflow invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
