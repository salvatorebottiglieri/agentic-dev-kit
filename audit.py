#!/usr/bin/env python3
"""audit.py — workflow invariant checks, transversal across projects.

Pure check functions (input data -> violations) + a CLI that gathers data
from the configured tracker via ``gh``. Run in any project:

    python ~/agentic-dev-kit/audit.py --repo owner/repo [--since YYYY-MM-DD] [--store PATH]

Each check maps to an invariant in INVARIANTS.md; every check must be able
to fail (see tests/test_audit.py — mutation tests feed deliberately
violating inputs and assert the violation is detected).
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

WORKFLOW_DIR = Path(__file__).resolve().parent
HANDOFFS_DIR = WORKFLOW_DIR / "handoffs"
ISSUE_REF_RE = re.compile(r"#\d+")
MODEL_RULE_SINCE = "2026-11-10"
MODEL_DECL_RE = re.compile(r"^Model:\s*(.+)$", re.M | re.I)
GH_WORKERS = 8
GH_ATTEMPTS = 3
GH_BACKOFF = 0.5


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


def check_model_declared(
    prs: list[dict], since: str = MODEL_RULE_SINCE
) -> list[str]:
    """D-051 — a merged PR after the rule's effective date declares its model.

    The body carries ``Model: docs/models/<feature>.md`` or
    ``Model: n/a — <reason>``. The checker proves the declaration exists and
    is shaped like a path or a reasoned skip; it cannot prove that the file is
    the right one — that is the Spec axis (h). PRs merged before the rule's
    effective date are out of scope, so the check does not fail history.
    """
    cutoff = max(since, MODEL_RULE_SINCE) if since else MODEL_RULE_SINCE
    violations: list[str] = []
    for pr in prs:
        if not pr.get("merged"):
            continue
        merged_at = str(pr.get("mergedAt") or "")[:10]
        if merged_at and merged_at < cutoff:
            continue
        body = pr.get("body") or ""
        match = MODEL_DECL_RE.search(body)
        if not match:
            violations.append(
                f"[D-051] PR #{pr.get('number', '?')} merged without a "
                f"`Model:` line (path or `n/a — reason`)"
            )
            continue
        value = match.group(1).strip().strip("`").strip()
        if not value:
            violations.append(
                f"[D-051] PR #{pr.get('number', '?')} has an empty `Model:` line"
            )
            continue
        if value.lower().startswith("n/a"):
            reason = re.sub(r"^n/a\s*[—:-]\s*", "", value, flags=re.I).strip()
            if not reason or reason.lower() == "n/a":
                violations.append(
                    f"[D-051] PR #{pr.get('number', '?')} says `Model: n/a` "
                    "without a reason"
                )
            continue
        path_match = re.search(r"docs/models/[^\s`]+\.md", value)
        if not path_match:
            violations.append(
                f"[D-051] PR #{pr.get('number', '?')} `Model:` does not point "
                f"to docs/models/*.md (got {value!r})"
            )
            continue
        declared = path_match.group(0)
        files = pr.get("files") or []
        if files:
            paths = {
                item.get("path") if isinstance(item, dict) else item
                for item in files
            }
            if declared not in paths:
                violations.append(
                    f"[D-051] PR #{pr.get('number', '?')} declares "
                    f"`Model: {declared}` but that file is not in the PR diff"
                )
    return violations


def check_ci_before_merge(prs: list[dict]) -> list[str]:
    """I1 — every merged PR has all CI checks successful (best-effort)."""
    violations = []
    for pr in prs:
        if not pr.get("merged"):
            continue
        rollup = pr.get("statusCheckRollup") or []
        if isinstance(rollup, Unread):
            continue  # unreadable — main lists it as unread, never as a pass
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


#: Marker set = the actual spec/ticket formats used across the workflow: spec issues
#: (Problem Statement/Solution), tickets (What to build/Acceptance), architecture/invariants
#: sections, program design. The one list the I3 audit and any caller's gate read.
SPEC_MARKERS: tuple[str, ...] = (
    "## Problem Statement",
    "## Solution",
    "## What to build",
    "## Acceptance",
    "## System Architecture",
    "## Invariants",
    "## Program Design",
)


def issue_has_spec(issue: dict) -> bool:
    """I3 — the issue carries a spec marker or a skip note, in its body or in a comment.
    The clause (INVARIANTS.md § I3) accepts the annotation in the body *or* in a comment,
    so the search text is body plus comment bodies."""
    comments = issue.get("comments") or []
    text = "\n".join([issue.get("body") or "", *((c.get("body") or "") for c in comments)])
    return "skipped" in text.lower() or any(m in text for m in SPEC_MARKERS)


def check_skip_annotations(issues: list[dict]) -> list[str]:
    """I3 — closed non-trivial issues carry a spec section or a skip note."""
    return [
        f"[I3] issue #{issue['number']} closed without a spec section "
        "or 'Skipped — reason' annotation (review: is it trivial?)"
        for issue in issues
        if issue.get("state") == "CLOSED" and not issue_has_spec(issue)
    ]


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
#: ``- **Repo**: example/example-repo``. Bullet, numbering,
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


def check_invariant_verification_exists() -> list[str]:
    """B-025 voce 4 — every invariant in `INVARIANTS.md` has a
    verification that can fail. ``Judgment`` alone is no longer enough
    (D-024 trigger): an invariant's verification is either a back-ticked
    script/function that exists in `audit.py` / `guards/`, or a tool
    reference alongside a judgment. The check parses `INVARIANTS.md` and
    asserts the form.
    """
    text = (WORKFLOW_DIR / "INVARIANTS.md").read_text(encoding="utf-8")
    audit_text = (WORKFLOW_DIR / "audit.py").read_text(encoding="utf-8")
    guards_text = ""
    guards_dir = WORKFLOW_DIR / "guards"
    if guards_dir.exists():
        guards_text = "\n".join(
            p.read_text(encoding="utf-8") for p in guards_dir.glob("*.py")
        )
    violations = []
    for m in re.finditer(r"^## (I\d+) — ", text, re.MULTILINE):
        inv_id = m.group(1)
        block_start = m.start()
        rest = text[block_start + 1:]
        next_match = re.search(r"^## ", rest, re.MULTILINE)
        block_end = block_start + 1 + next_match.start() if next_match else len(text)
        block = text[block_start:block_end]
        if "REMOVED" in block[:200]:
            continue
        ver_match = re.search(r"\*\*Verification\*\*:\s*([^\n]+)", block)
        if not ver_match or not ver_match.group(1).strip():
            violations.append(
                f"[B-025/4] {inv_id} has no **Verification** line"
            )
            continue
        ver = ver_match.group(1)
        ck = re.search(r"`audit\.py\s+(check_\w+)`", ver)
        if ck:
            func_name = ck.group(1)
            if f"def {func_name}(" not in audit_text:
                violations.append(
                    f"[B-025/4] {inv_id} Verification cites "
                    f"`audit.py {func_name}` but the function is missing"
                )
            continue
        if "judgment" in ver.lower() and "audit.py" not in ver and "guards/" not in ver:
            violations.append(
                f"[B-025/4] {inv_id} Verification is judgment-only — "
                f"D-024 requires a script/tool reference alongside"
            )
    return violations


def check_premises_falsified(reports: list[dict]) -> list[str]:
    """B-025 voce 2 — premises in a design must be falsified with
    evidence. Each finding (or design row) can carry a ``premise`` field
    of shape ``{claim, verdict, evidence}``. ``verdict=falsified`` is the
    only state that allows a premise to survive into implementation;
    ``untested`` is a violation (the design has not done its job);
    ``confirmed`` without an ``evidence`` pointer is a violation
    (the conclusion is asserted without where-it-came-from).
    """
    violations = []
    for r in reports:
        p = r.get("premise")
        if not p:
            continue
        verdict = p.get("verdict")
        evidence = p.get("evidence")
        item = r.get("item_id", "?")
        if verdict == "untested":
            violations.append(
                f"[B-025/2] premise item_id={item} verdict=untested — "
                f"the design did not falsify its own premise: "
                f"{p.get('claim','?')[:80]}"
            )
        elif verdict == "confirmed" and not evidence:
            violations.append(
                f"[B-025/2] premise item_id={item} verdict=confirmed "
                f"without evidence pointer: {p.get('claim','?')[:80]}"
            )
    return violations


def gather_session_claims(git_common_dir: Path | None = None) -> set[str]:
    """B-025 voce 12 — names of claim files in
    ``<git-common-dir>/agentic-sessions/`` (one per active session).
    Stale claims are treated as live per `SESSION-ISOLATION.md`; the
    caller filters them by the *handoff's* feature / repo.
    """
    if git_common_dir is None:
        git_common_dir = WORKFLOW_DIR / ".git"
    claims_dir = git_common_dir / "agentic-sessions"
    if not claims_dir.exists():
        return set()
    return {p.stem for p in claims_dir.iterdir() if p.is_file()}


def check_session_claim_present(
    handoffs: list[dict], claims: set[str]
) -> list[str]:
    """B-025 voce 12 — a session that ``claimed a primary tree``
    (per `SESSION-ISOLATION.md`) has a corresponding claim file. A
    missing claim on a session that said it claimed is a violation:
    the rest of the workflow trusts the claim to coordinate writers,
    and a phantom claim is worse than no claim.
    """
    claimed_re = re.compile(r"claimed (?:a )?primary tree", re.IGNORECASE)
    violations = []
    for h in handoffs:
        text = h.get("text", "")
        if not claimed_re.search(text):
            continue
        repo_match = re.search(r"\*\*Repo\*\*:\s*([\w./-]+)", text)
        feature = repo_match.group(1) if repo_match else h.get("name", "?")
        if feature not in claims:
            violations.append(
                f"[B-025/12] handoff {h.get('name','?')} says it claimed a "
                f"primary tree but no claim file exists for feature "
                f"'{feature}' in <git-common-dir>/agentic-sessions/"
            )
    return violations


def gather_worktrees(repo: Path | None = None) -> list[dict]:
    """B-025 voce 13 — snapshot of `git worktree list --porcelain`,
    parsed into ``{path, head, branch, locked}`` dicts. The first
    entry is the principal worktree; subsequent entries are the
    worktrees the workflow must account for at session end.
    """
    if repo is None:
        repo = WORKFLOW_DIR
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return []
    worktrees: list[dict] = []
    current: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):].strip()}
        elif line.startswith("HEAD "):
            current["head"] = line[5:].strip()
        elif line.startswith("branch "):
            current["branch"] = line[7:].strip()
        elif line == "locked":
            current["locked"] = True
    if current:
        worktrees.append(current)
    return worktrees


def check_worktrees_cleaned(worktrees: list[dict]) -> list[str]:
    """B-025 voce 13 — every non-principal worktree is documented as
    either retained-for-X or to-be-removed in the session handoff
    (per `SESSION-ISOLATION.md` § "Cleanup at session end"). The check
    reads the worktree list and emits one violation per non-principal
    worktree; the handoff writer (or the orchestrator) addresses it.
    Locked worktrees are reported as critical (they cannot be cleaned
    without manual unlock).
    """
    violations: list[str] = []
    if len(worktrees) <= 1:
        return violations
    principal = worktrees[0]["path"]
    for wt in worktrees[1:]:
        branch = wt.get("branch", "?")
        path = wt.get("path", "?")
        if wt.get("locked"):
            violations.append(
                f"[B-025/13] CRITICAL worktree {path} (branch {branch}) is "
                f"locked and cannot be removed automatically — the principal "
                f"worktree is {principal}; unlock or `git worktree remove --force`"
            )
        else:
            violations.append(
                f"[B-025/13] worktree {path} (branch {branch}) is still "
                f"present after the session — should be cleaned or its "
                f"retention documented in the handoff"
            )
    return violations


def check_rationale_entry_in_pr(prs: list[dict]) -> list[str]:
    """B-025 voce 14 — a PR that touches the workflow rule registry
    (``phases/*.md`` or the "Where each rule is enforced" table in
    `WORKFLOW.md`) must also touch `RATIONALE.md` in the same PR
    (per WORKFLOW.md General rules: "any change to a load-bearing rule
    ... adds or updates an entry in `RATIONALE.md`"). A rule change
    without a rationale entry is an orphan (D-024).
    """
    rationale_re = re.compile(r"\bRATIONALE\.md\b")
    rule_paths = (
        re.compile(r"^phases/[^\s/]+\.md$"),
        re.compile(r"^WORKFLOW\.md$"),
    )
    violations: list[str] = []
    for pr in prs:
        files = pr.get("files") or []
        touches_rule_registry = False
        for f in files:
            fname = f.get("path") if isinstance(f, dict) else f
            if not fname:
                continue
            for rx in rule_paths:
                if rx.match(fname):
                    touches_rule_registry = True
                    break
        if not touches_rule_registry:
            continue
        body = pr.get("body") or ""
        if rationale_re.search(body) or rationale_re.search(str(files)):
            continue
        violations.append(
            f"[B-025/14] PR #{pr.get('number','?')} touches the rule "
            f"registry ({[f.get('path') if isinstance(f, dict) else f for f in files]}) "
            f"but does not touch `RATIONALE.md` — a rule change without "
            f"a rationale entry is an orphan (D-024)"
        )
    return violations


def check_audit_in_handoff(handoffs: list[dict]) -> list[str]:
    """B-025 voce 15 — the most recent handoff names ``audit.py
    --repo <owner/repo>`` in its Verification section. The session-end
    gate requires the audit; a handoff that omits the audit command
    declares "verification" without evidence (I10 weak).
    """
    if not handoffs:
        return []
    latest = sorted(handoffs, key=lambda h: h.get("name", ""))[-1]
    text = latest.get("text", "")
    audit_re = re.compile(r"audit\.py\s+--repo\s+[\w./-]+")
    if audit_re.search(text):
        return []
    return [
        f"[B-025/15] latest handoff {latest.get('name','?')} does not "
        f"cite `audit.py --repo <owner/repo>` in its Verification — "
        f"the session-end gate command must be quoted (I10)"
    ]


def check_phase2_decision_stated(handoffs: list[dict]) -> list[str]:
    """B-025 voce 3 — every handoff surfaces a phase-2 decision
    (done / skipped — reason / not applicable). Phase 2 is conditional
    ("only when the feature touches UI"); the declaration is required
    either way so a future reader does not have to reconstruct the
    decision from prose. The literal string ``Phase 2:`` followed by a
    verdict is the mark; its absence is a violation (same shape as I3
    skip annotations).
    """
    decision_re = re.compile(
        r"Phase\s*2\s*:\s*(done|skipped|n/?a|not applicable|none)",
        re.IGNORECASE,
    )
    violations = []
    for h in handoffs:
        text = h.get("text", "")
        if decision_re.search(text):
            continue
        violations.append(
            f"[B-025/3] handoff {h.get('name','?')} has no phase-2 "
            f"decision ('Phase 2: done' / 'Phase 2: skipped — <reason>' / "
            f"'Phase 2: not applicable')"
        )
    return violations


def check_reviewer_evidence_verbatim(
    reports: list[dict], pr_diffs: dict[str, str | Unread]
) -> list[str]:
    """B-025 voce 8 — every ``quote`` for a code claim appears verbatim in
    the diff of its ``commit``. A quote that does not match is a
    hallucination or a stale reference; either way it fails the gate.
    Coverage claims (``claim=coverage``) carry no quote — the mutation
    matrix is the proof — and are skipped. Code claims missing ``quote``
    or ``commit`` are violations: the report does not enable verification.
    """
    violations = []
    for r in reports:
        claim = r.get("claim", "code")
        if claim == "coverage":
            continue
        quote = r.get("quote")
        commit = r.get("commit")
        item = r.get("item_id", "?")
        criterion = r.get("criterion", "?")
        if not quote or not commit:
            violations.append(
                f"[B-025/8] finding item_id={item} criterion={criterion} "
                f"claim=code: missing quote or commit (cannot verify)"
            )
            continue
        diff = pr_diffs.get(commit)
        if diff is None:
            violations.append(
                f"[B-025/8] finding item_id={item} commit={commit[:8]} "
                f"not in pr_diffs (cannot verify)"
            )
            continue
        if isinstance(diff, Unread):
            continue  # not judged, not found wrong — main lists it as unread
        if quote not in diff:
            violations.append(
                f"[B-025/8] finding item_id={item} criterion={criterion} "
                f"commit={commit[:8]}: quote does not appear verbatim in diff"
            )
    return violations


def _is_pass_like(verdict: object) -> bool:
    """#13: REVIEW-CRITERIA verdict vocabulary — ``pass``, ``pinned``, ``n/a ...``."""
    v = str(verdict or "")
    return v in ("pass", "pinned") or v.startswith("n/a")


def check_i6_zero_actionable(reports: list[dict]) -> list[str]:
    """B-025 voce 10 — the implement-loop ends only with zero actionable
    findings (I6). An open (default) ``severity=fatal`` row whose verdict is
    not pass-like (``pass``, ``pinned``, ``n/a ...``) is an unresolved
    blocker: the fix loop must continue. It is closed by another row of the
    same ``_feature``, ``item_id`` and ``criterion`` with status ``resolved``
    or ``fixed``. ``advisory`` rows never block.
    """
    def close_key(r: dict) -> tuple:
        return (r.get("_feature"), r.get("item_id"), r.get("criterion"))

    closed = {close_key(r) for r in reports if r.get("status") in ("resolved", "fixed")}
    violations = []
    for r in reports:
        if (r.get("status", "open") == "open"
                and r.get("severity") == "fatal"
                and not _is_pass_like(r.get("verdict"))
                and close_key(r) not in closed):
            violations.append(
                f"[I6/B-025/10] {r.get('verdict', '?')} finding "
                f"item_id={r.get('item_id','?')} "
                f"criterion={r.get('criterion','?')} reviewer={r.get('reviewer','?')} "
                f"is still open (fix loop must continue)"
            )
    return violations


def check_brief_coverage(
    reports: list[dict], brief_items: list[str]
) -> list[str]:
    """B-025 voce 9 — every item of the brief appears in the reviewer
    reports. ``Silence is not a verdict`` (criterion 7): in a free-form
    finding list, "conforms", "not applicable" and "never read" are the
    same output, and that is how a reviewer drops an item from its own
    brief. The orchestrator counts coverage — every brief item has at
    least one row in the JSONL, with ``verdict=pass`` / ``fail`` /
    ``n/a — motivo`` accepted.
    """
    violations = []
    covered = {r.get("item_id") for r in reports}
    for item in brief_items:
        if item not in covered:
            violations.append(
                f"[B-025/9] brief item '{item}' has no row in any reviewer "
                f"report (silence is not a verdict)"
            )
    return violations


def check_report_actor(reports: list[dict]) -> list[str]:
    """B-004/D-031 — every report row names the sub-agent that emitted it.

    The actor is the data D-031 needs to prove that a reviewer/prover did not
    write the commit it reviews. The isolation check itself comes later; a
    missing actor is already a report-format violation.
    """
    violations = []
    for r in reports:
        if not r.get("actor"):
            violations.append(
                f"[B-004] report {r.get('_feature', '?')}/{r.get('item_id', '?')} "
                "has no actor"
            )
    return violations


def _feature_axis(
    path: Path, features: frozenset[str] = frozenset()
) -> tuple[str, str]:
    """``phase-harness-spec-r2`` → (``phase-harness``, ``spec-r2``).

    *features* are the feature names declared by brief manifests: the longest
    one that the stem equals or prefixes (``feature + "-"``) wins and the rest
    is the axis (it may contain dashes). Without a match, split at the last
    dash (``example-5061-s44-review`` → ``example-5061-s44``, ``review``).
    """
    stem = path.stem
    feature = max(
        (f for f in features if stem == f or stem.startswith(f + "-")),
        key=len,
        default=None,
    )
    if feature is not None:
        return feature, stem[len(feature) + 1:]
    if "-" in stem:
        return tuple(stem.rsplit("-", 1))  # type: ignore[return-value]
    return stem, ""


def _store_module():
    """The sibling report_store.py of this install, loaded by path."""
    found = importlib.util.spec_from_file_location("audit_report_store", WORKFLOW_DIR / "report_store.py")
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def _read_store(store: Path | None, read, default):
    """``read(module, conn)`` on the store at *store*; *default* when there is no store.

    A path that is not a file is no store (``report_store.open_existing``): reading never creates one.
    With no path the module is not even loaded: audit.py travels without report_store.py."""
    if store is None:
        return default
    module = _store_module()
    try:
        conn = module.open_existing(store)
        if conn is None:
            return default
        try:
            return read(module, conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"report store {store}: {exc}") from exc


def _stored_axis(feature: str, report: str) -> str:
    """The report name minus ``<feature>-``: what ``_feature_axis`` gives the file of that name."""
    if report == feature:
        return ""
    return report.removeprefix(feature + "-")


def gather_reviewer_reports(repo: str, store: Path | None = None) -> list[dict]:
    """Read the reviewer reports of *repo* from the report store (B-004).

    The store is the only source (#70): no store, or a path that is not a file, gives no
    rows and is never created. A stored row takes its ``_feature`` from its ``feature``
    column. Rows of other repos, and old rows with no ``repo``, are not returned: the
    audit cannot know which project they belong to, and judging them against the audited
    repo would be the same false-positive class as I9. A row stored twice (one report
    imported from two folders, or under two report names) counts once.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for row, feature, report in _read_store(store, lambda m, c: m.review_records(c, repo), []):
        row = dict(row)
        row["_feature"] = feature
        row["_axis"] = _stored_axis(feature, report)
        key = json.dumps(row, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


def gather_brief_items(repo: str, store: Path | None = None) -> dict[str, list[str]]:
    """The briefs of *repo* in the report store: feature -> its numbered items.

    The orchestrator stores the numbered brief (``wf.py report brief``) before spawning the
    reviewers; ``check_brief_coverage`` needs it to know what "every item" means. No store
    gives no briefs (and none is created).
    """
    return _read_store(store, lambda m, c: m.brief_items(c, repo), {})


def gather_feature_dates(repo: str, store: Path | None = None) -> dict[str, str]:
    """feature -> the ``recorded_at`` of its most recent reviewer row or brief item (#102).

    No store gives no dates (and none is created)."""
    return _read_store(store, lambda m, c: m.feature_dates(c, repo), {})


def apply_window(reports: list[dict], briefs: dict[str, list[str]], dates: dict[str, str],
                 since: str) -> tuple[list[dict], dict[str, list[str]], dict[str, int]]:
    """Keep the features dated on or after *since*, whole; count the rows of the others (#102).

    A feature is inside when the first ten characters of its date are >= *since*, or when it has
    no date (it is never dropped silently). Returns the rows and briefs of the features inside, in
    their order, and ``left_out``: feature -> its number of rows left out (0 for a brief-only one).
    """
    def inside(feature: str) -> bool:
        return feature not in dates or dates[feature][:10] >= since

    left_out: dict[str, int] = {}
    for feature in [r.get("_feature") for r in reports] + list(briefs):
        if not inside(feature):
            left_out.setdefault(feature, 0)
    for r in reports:
        if r.get("_feature") in left_out:
            left_out[r["_feature"]] += 1
    return ([r for r in reports if inside(r.get("_feature"))],
            {f: items for f, items in briefs.items() if inside(f)}, left_out)


def window_note(left_out: dict[str, int]) -> list[str]:
    """The lines of the informative note about the features left out of the window (#102)."""
    if not left_out:
        return []
    return ([f"{sum(left_out.values())} reviewer rows older than the window were not checked"]
            + [f"  - {feature}: {rows} row(s)" for feature, rows in sorted(left_out.items())])


def gather_pr_diffs(repo: str, reports: list[dict]) -> dict[str, str | Unread]:
    """Fetch the patch of every commit cited by a report (B-004).

    ``check_reviewer_evidence_verbatim`` needs the diff of the report's
    ``commit``. The commit API is enough: the quote is matched against the
    patch, not against the PR body. Commits with no textual patch (binary or
    merge) produce an empty diff and the check reports the quote as missing.
    A commit that cannot be read is kept as an ``Unread``; nothing raises.
    """
    commits = sorted({r["commit"] for r in reports if r.get("commit")})
    calls = [["api", f"repos/{repo}/commits/{sha}"] for sha in commits]
    diffs: dict[str, str | Unread] = {}
    for sha, call, data in zip(commits, calls, _gh_many(calls)):
        if isinstance(data, Unread):
            diffs[sha] = data
        elif not isinstance(data, dict):
            diffs[sha] = Unread("gh " + " ".join(call), "unexpected API response")
        else:
            parts = [f.get("patch", "").rstrip("\n") for f in data.get("files", [])
                     if f.get("patch")]
            diffs[sha] = "\n".join(parts)
    return diffs


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


class Unread(NamedTuple):
    call: str
    reason: str


def _gh_many(calls: list[list[str]], sleep: Callable[[float], None] = time.sleep) -> list[dict | Unread]:
    """Read independent ``gh`` calls in a pool, in the order of *calls*.

    Each call is retried up to ``GH_ATTEMPTS`` times with a doubling backoff;
    one that keeps failing (any exception) comes back as an ``Unread``.
    """
    def read(args: list[str]) -> dict | Unread:
        n = 0
        while True:
            n += 1
            try:
                return _gh(args)
            except Exception as exc:  # noqa: BLE001 — nothing may leave the pool
                if n >= GH_ATTEMPTS:
                    return Unread("gh " + " ".join(args),
                                  f"{type(exc).__name__}: {exc}")
                sleep(GH_BACKOFF * 2 ** (n - 1))

    with ThreadPoolExecutor(max_workers=GH_WORKERS) as pool:
        return list(pool.map(read, calls))


def gather_prs(repo: str, since: str) -> list[dict]:
    """Merged PRs since *since*, with body + CI rollup."""
    prs = _gh([
        "pr", "list", "--repo", repo, "--state", "merged",
        "--search", f"merged:>={since}",
        "--json", "number,mergedAt,body,headRefOid,files",
    ])
    views = _gh_many([
        ["pr", "view", str(pr["number"]), "--repo", repo,
         "--json", "state,statusCheckRollup"]
        for pr in prs
    ])
    for pr, detail in zip(prs, views):
        if isinstance(detail, Unread):
            # The rollup is unreadable (token without check-run access): the
            # PRs come from `--state merged`, so the state is known, and the
            # same jobs are readable on the Actions runs channel.
            pr["merged"] = True
            sha = pr.get("headRefOid")
            pr["statusCheckRollup"] = (
                _rollup_from_actions(repo, sha) if sha else detail
            )
            continue
        pr["merged"] = detail["state"] == "MERGED"
        pr["statusCheckRollup"] = detail.get("statusCheckRollup") or []
    return prs


def _rollup_from_actions(repo: str, sha: str) -> list[dict] | Unread:
    """Fallback for the PR check rollup, read from the Actions runs.

    `--json statusCheckRollup` needs a token allowed to read check runs; the
    runs channel only needs `Actions: read`, so it keeps I1 verifiable with a
    token that cannot read the rollup. Same underlying jobs, one row per
    workflow instead of one row per check.

    Only `pull_request` runs are the PR gate (a `push` run on the default
    branch is not), only completed runs have a conclusion, and the newest run
    wins — `gh run list` returns newest first. An unreadable channel returns
    an ``Unread`` (never a pass: ``main`` lists it); the rollup stays the
    primary channel.
    """
    [runs] = _gh_many([[
        "run", "list", "--repo", repo, "--commit", sha, "--limit", "50",
        "--json", "name,status,conclusion,event",
    ]])
    if isinstance(runs, Unread):
        return runs
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
    store = None
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    i = 0
    while i < len(args):
        if args[i] == "--repo" and i + 1 < len(args):
            repo = args[i + 1]
            i += 2
        elif args[i] == "--store" and i + 1 < len(args):
            store = Path(args[i + 1])
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        else:
            i += 1
    if not repo:
        print("usage: audit.py --repo owner/repo [--since YYYY-MM-DD] [--store PATH]", file=sys.stderr)
        return 2

    if store is None:
        store = _store_module().default_store_path(os.environ, Path.home())
    violations: list[str] = []
    if not store.is_file():
        violations.append(f"[B-004] no report store at {store}")
    try:
        prs = gather_prs(repo, since)
        issues = gather_issues(repo, since)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    violations += check_pr_links_issue(prs)
    violations += check_model_declared(prs, since)
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

    try:
        reports = gather_reviewer_reports(repo, store=store)
        briefs = gather_brief_items(repo, store=store)
        dates = gather_feature_dates(repo, store=store)
        reports, briefs, left_out = apply_window(reports, briefs, dates, since)
        pr_diffs = gather_pr_diffs(repo, reports)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    violations += check_report_actor(reports)
    violations += check_reviewer_evidence_verbatim(reports, pr_diffs)
    violations += check_i6_zero_actionable(reports)
    for feature, items in briefs.items():
        feature_reports = [r for r in reports if r.get("_feature") == feature]
        violations += check_brief_coverage(feature_reports, items)
    for feature in sorted({r.get("_feature") for r in reports} - set(briefs)):
        violations.append(
            f"[B-004] feature {feature} has reports but no brief manifest "
            "(wf.py report brief)"
        )

    unread = [pr["statusCheckRollup"] for pr in prs
              if isinstance(pr.get("statusCheckRollup"), Unread)]
    unread += [d for d in pr_diffs.values() if isinstance(d, Unread)]
    if violations:
        print(f"{len(violations)} violation(s):")
        for v in violations:
            print(f"  - {v}")
    for line in window_note(left_out):
        print(line)
    if unread:
        print(f"{len(unread)} item(s) could not be read "
              "(not checked; this is not a pass):")
        for u in unread:
            print(f"  - {u.call}: {u.reason}")
    if violations or unread:
        return 1
    print("ok — all workflow invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
