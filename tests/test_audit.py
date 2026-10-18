"""Mutation tests for audit.py — every check must be able to fail.

Anti-vacuity rule (INVARIANTS.md): a check that never fails on violating
input is worse than no check. Each test feeds a deliberately violating
input and asserts the violation is detected; a green twin asserts the
compliant input passes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit
from audit import (
    check_audit_in_handoff,
    check_brief_coverage,
    check_ci_before_merge,
    check_configured_gates_run_in_ci,
    check_followups_tracked,
    check_handoffs_quote_commands,
    check_i6_zero_actionable,
    check_invariant_verification_exists,
    check_phase2_decision_stated,
    check_pr_links_issue,
    check_premises_falsified,
    check_rationale_entry_in_pr,
    check_recent_handoff,
    check_reviewer_evidence_verbatim,
    check_session_claim_present,
    check_skip_annotations,
    check_worktrees_cleaned,
    gather_prs,
)

# Real commit from BranchToast/branchtoast-news-pipeline, where the fallback
# was needed (a fine-grained PAT without check-run access).
SHA = "0887aaea7ae21185fee524a59facc500e43465f3"

#: I9 is scoped per project: only handoffs declaring this repo are read.
REPO = "BranchToast/branchtoast-news-pipeline"


def _handoff(text: str, name: str = "h.md", repo: str | None = REPO) -> dict:
    """A handoff fixture. ``repo=None`` writes one without the ``Repo:`` field."""
    return {"name": name, "text": text, "repo": repo}


# ── I4: PR links issue ───────────────────────────────────────────────


def test_pr_links_issue_compliant():
    prs = [{"number": 1, "merged": True, "body": "Fixes #12 — add quota gate"}]
    assert check_pr_links_issue(prs) == []


def test_pr_links_issue_detects_missing_link():
    prs = [{"number": 2, "merged": True, "body": "Add quota gate"}]
    violations = check_pr_links_issue(prs)
    assert any("#2" in v for v in violations)


def test_pr_links_issue_ignores_open_prs():
    prs = [{"number": 3, "merged": False, "body": "wip"}]
    assert check_pr_links_issue(prs) == []


# ── I1: CI green before merge ────────────────────────────────────────


def test_ci_before_merge_compliant():
    prs = [{
        "number": 4, "merged": True,
        "statusCheckRollup": [{"name": "test", "conclusion": "SUCCESS"}],
    }]
    assert check_ci_before_merge(prs) == []


def test_ci_before_merge_detects_failure():
    prs = [{
        "number": 5, "merged": True,
        "statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}],
    }]
    violations = check_ci_before_merge(prs)
    assert any("#5" in v and "FAILURE" in v for v in violations)


def test_ci_before_merge_empty_rollup_not_a_violation():
    prs = [{"number": 6, "merged": True, "statusCheckRollup": []}]
    assert check_ci_before_merge(prs) == []


# ── I3: skip annotations ─────────────────────────────────────────────


def test_skip_annotations_compliant_with_spec():
    issues = [{
        "number": 7, "state": "CLOSED",
        "body": "## System Architecture\nmermaid",
    }]
    assert check_skip_annotations(issues) == []


def test_skip_annotations_compliant_with_skip_note():
    issues = [{
        "number": 8, "state": "CLOSED",
        "body": "Skipped — API-only, no product-review",
    }]
    assert check_skip_annotations(issues) == []


def test_skip_annotations_detects_missing_both():
    issues = [{"number": 9, "state": "CLOSED", "body": "fix typo in docs"}]
    violations = check_skip_annotations(issues)
    assert any("#9" in v for v in violations)


def test_skip_annotations_accepts_skip_note_in_comment():
    """The clause allows the annotation in a comment, not only the body."""
    issues = [{
        "number": 10, "state": "CLOSED", "body": "fix typo in docs",
        "comments": [{"body": "Skipped — API-only, no product-review"}],
    }]
    assert check_skip_annotations(issues) == []


def test_skip_annotations_detects_missing_both_with_empty_comments():
    """An empty comment list must not make the check vacuously green."""
    issues = [{
        "number": 11, "state": "CLOSED", "body": "fix typo in docs",
        "comments": [],
    }]
    violations = check_skip_annotations(issues)
    assert any("#11" in v for v in violations)


# ── I5: recent handoff ───────────────────────────────────────────────


def test_recent_handoff_compliant():
    today = __import__("datetime").date.today().strftime("%Y%m%d")
    assert check_recent_handoff([f"handoff-x-{today}.md"], days=7) == []


def test_recent_handoff_detects_stale_corpus():
    violations = check_recent_handoff(["handoff-x-20200101.md"], days=7)
    assert violations and "no handoff" in violations[0]


# ── I8: configured gates run in CI ───────────────────────────────────


def test_configured_gates_run_in_ci_compliant():
    config = {
        "configured": {
            "mypy": "backend/pyproject.toml",
            "ruff": "backend/pyproject.toml",
        },
        "ci_text": (
            "steps:\n"
            "  - run: uv run mypy src\n"
            "  - run: uv run ruff check src\n"
        ),
    }
    assert check_configured_gates_run_in_ci(config) == []


def test_configured_gates_run_in_ci_detects_unenforced_gate():
    """The exact drift that motivated I8: strict configured, CI runs no gate."""
    config = {
        "configured": {"mypy": "backend/pyproject.toml"},
        "ci_text": "steps:\n  - run: uv run pytest tests/unit/\n",
    }
    violations = check_configured_gates_run_in_ci(config)
    assert violations
    assert "mypy" in violations[0]
    assert "backend/pyproject.toml" in violations[0]


def test_configured_gates_run_in_ci_without_ci_data_is_not_a_violation():
    config = {"configured": {"mypy": "x/pyproject.toml"}, "ci_text": ""}
    assert check_configured_gates_run_in_ci(config) == []


# ── I9: follow-ups tracked ───────────────────────────────────────────


def test_followups_tracked_compliant():
    handoffs = [
        _handoff("## Next session\n- Advisory non bloccanti: currency symbol (#270)\n")
    ]
    assert check_followups_tracked(handoffs, {270}, REPO) == []


def test_followups_tracked_detects_unresolving_ref():
    handoffs = [_handoff("Advisory: the helper is duplicated (#99999)\n")]
    violations = check_followups_tracked(handoffs, {270}, REPO)
    assert violations
    assert "#99999" in violations[0]
    assert "h.md:1" in violations[0]


def test_followups_tracked_detects_missing_ref():
    handoffs = [_handoff("## Next session\n- advisory: extract the shared helper\n")]
    violations = check_followups_tracked(handoffs, set(), REPO)
    assert violations and "no tracker reference" in violations[0]


def test_followups_tracked_accepts_explicit_none():
    handoffs = [_handoff("Advisory: none.\n")]
    assert check_followups_tracked(handoffs, set(), REPO) == []


def test_followups_tracked_ignores_markers_in_prose_outside_followup_sections():
    """The two observed false positives (2026-09-17), one per marker word: a
    *delivered* step or a review count is not a follow-up."""
    handoffs = [_handoff(
        "## Delivered work\n"
        "3. **Verifica visiva**: nessun difetto, solo un'osservazione "
        "advisory sull'indicatore della tab attiva.\n"
        "2. **I9 smette di leggere un severity come follow-up** "
        "(`9af5a30`, rationale D-015).\n"
        "Round 1: 0 fatal, 7 advisory. Round 2: 0 fatal, 5 advisory.\n"
    )]
    assert check_followups_tracked(handoffs, set(), REPO) == []


def test_followups_tracked_still_catches_an_anchored_advisory():
    """Non-vacuity of the anchored half: a line that *labels* itself is still a
    named follow-up and still needs a ticket, section or no section."""
    handoffs = [_handoff("## Next session\n- Advisory: estrarre l'helper condiviso\n")]
    violations = check_followups_tracked(handoffs, set(), REPO)
    assert violations and "no tracker reference" in violations[0]


def test_followups_tracked_catches_an_unbulleted_advisory_heading():
    handoffs = [_handoff("**Advisory** da aprire\n")]
    violations = check_followups_tracked(handoffs, set(), REPO)
    assert violations and "no tracker reference" in violations[0]


def test_followups_tracked_catches_prose_inside_a_followup_section():
    """The section half: inside `## Next session` any mention counts, so a
    pending item cannot hide behind an unlabelled sentence."""
    handoffs = [_handoff("## Next session\nResta un follow-up da aprire dopo il merge.\n")]
    violations = check_followups_tracked(handoffs, set(), REPO)
    assert violations and "no tracker reference" in violations[0]


def test_followups_tracked_section_scope_ends_at_the_next_heading():
    """A mention after the section ends is prose again — the rule is scoped to
    the section, not to the rest of the document."""
    handoffs = [_handoff(
        "## Next session\n- niente da aprire\n\n"
        "## Ground truth\nIl follow-up advisory e' stato chiuso.\n"
    )]
    assert check_followups_tracked(handoffs, set(), REPO) == []


# ── I9 scope: the handoff declares its repo ───────────────────────────


def test_handoff_repo_reads_the_declared_field():
    """The accepted forms of the field, and — just as important — a heading
    that mentions the repo without declaring it (which parses to None)."""
    assert audit._handoff_repo("- **Repo**: BranchToast/x\n") == "BranchToast/x"
    assert audit._handoff_repo("Repo: owner/name\n") == "owner/name"
    assert audit._handoff_repo("**repo:** owner/name (audited)\n") == "owner/name"
    assert audit._handoff_repo("## Repo state\nBranchToast/branchtoast-x\n") is None


def test_followups_tracked_ignores_handoffs_of_another_repo():
    """The third false-positive class (2026-09-19): the corpus of handoffs is
    shared by every project, the tracker is not. The domus_vault handoff
    citing its own #319 resolves at home and must not be judged here."""
    handoffs = [_handoff(
        "Closes #316 #317 #318 #319\n- Advisory: legacy note, already tracked\n",
        name="handoff-diario-mockup-fidelity-20260917.md",
        repo="salvatorebottiglieri/domus_vault",
    )]
    assert check_followups_tracked(handoffs, {270}, REPO) == []


def test_followups_tracked_still_bites_for_the_audited_repo():
    """Non-vacuity of the filter: skipping other projects must not skip ours.
    One handoff per repo, one unresolvable ref — exactly one violation."""
    handoffs = [
        _handoff("Advisory: follow-up already tracked (#900)\n",
                 name="theirs.md", repo="other/project"),
        _handoff("Advisory: follow-up to open (#901)\n",
                 name="ours.md", repo=REPO),
    ]
    violations = check_followups_tracked(handoffs, {900}, REPO)
    assert len(violations) == 1
    assert "#901" in violations[0] and "ours.md" in violations[0]


def test_followups_tracked_skips_a_handoff_declaring_no_repo():
    """Cannot verify, not counter-evidence: without the field the refs would be
    resolved against the wrong tracker, the very defect the field removes."""
    handoffs = [_handoff("Advisory: follow-up to open (#99999)\n", repo=None)]
    assert check_followups_tracked(handoffs, {270}, REPO) == []


def test_followups_tracked_matches_the_repo_slug_case_insensitively():
    """GitHub slugs are case-insensitive, so the field comparison is too."""
    handoffs = [_handoff("Advisory: follow-up to open (#99999)\n",
                         repo="branchtoast/BRANCHTOAST-NEWS-PIPELINE")]
    violations = check_followups_tracked(handoffs, {270}, REPO)
    assert violations and "#99999" in violations[0]


# ── I10: handoffs quote the command (weak check) ─────────────────────


def test_handoffs_quote_commands_compliant():
    handoffs = [{
        "name": "h.md",
        "text": "## Verification\n\n`pytest tests/unit/` -> 1049 passed\n",
    }]
    assert check_handoffs_quote_commands(handoffs) == []


def test_handoffs_quote_commands_detects_outcome_without_command():
    handoffs = [{
        "name": "h.md",
        "text": "## Verification\n\nAll tests green, no new errors.\n",
    }]
    violations = check_handoffs_quote_commands(handoffs)
    assert violations and "quotes no command" in violations[0]


def test_handoffs_quote_commands_skips_without_verification_section():
    handoffs = [{"name": "h.md", "text": "## Delivered work\n\nAll tests green.\n"}]
    assert check_handoffs_quote_commands(handoffs) == []


# ── I1: rollup fallback on the Actions runs channel ──────────────────
#
# `gh pr view --json statusCheckRollup` needs a token allowed to read check
# runs; without it `gather_prs` aborted the whole audit. The fallback reads
# the same jobs from `gh run list`. These tests pin it, and (anti-vacuity
# rule) fail if the fallback is removed or miswired.


def _run(name, conclusion, *, event="pull_request", status="completed"):
    return {"name": name, "conclusion": conclusion,
            "event": event, "status": status}


def _gh_stub(monkeypatch, *, rollup=None, rollup_error=False, runs=None,
             runs_error=False, head_ref=SHA):
    """Patch ``audit._gh``; returns the argv list of every call made."""
    calls: list[list[str]] = []

    def fake(args):
        calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            return [{"number": 12, "mergedAt": "2026-09-19T00:00:00Z",
                     "body": "Fixes #1", "headRefOid": head_ref}]
        if args[:2] == ["pr", "view"]:
            if rollup_error:
                raise RuntimeError(
                    "gh pr view failed: Resource not accessible by personal "
                    "access token"
                )
            return {"state": "MERGED", "statusCheckRollup": rollup or []}
        if args[:2] == ["run", "list"]:
            if runs_error:
                raise RuntimeError("gh run list failed")
            return runs or []
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(audit, "_gh", fake)
    return calls


def test_gather_prs_falls_back_to_actions_runs_when_rollup_unreadable(monkeypatch):
    _gh_stub(monkeypatch, rollup_error=True, runs=[_run("CI", "success")])
    prs = gather_prs("owner/repo", "2026-09-01")
    assert len(prs) == 1
    assert prs[0]["merged"] is True
    assert prs[0]["statusCheckRollup"] == [
        {"name": "CI", "conclusion": "success"}
    ]
    assert check_ci_before_merge(prs) == []


def test_gather_prs_fallback_detects_a_failing_run(monkeypatch):
    _gh_stub(monkeypatch, rollup_error=True, runs=[_run("CI", "failure")])
    violations = check_ci_before_merge(gather_prs("owner/repo", "2026-09-01"))
    assert violations
    assert "CI" in violations[0] and "FAILURE" in violations[0]


def test_gather_prs_fallback_ignores_non_pull_request_runs(monkeypatch):
    """A failing `push` run on main is not the PR gate."""
    _gh_stub(monkeypatch, rollup_error=True,
             runs=[_run("Image (GHCR)", "failure", event="push")])
    prs = gather_prs("owner/repo", "2026-09-01")
    assert prs[0]["statusCheckRollup"] == []
    assert check_ci_before_merge(prs) == []


def test_gather_prs_fallback_keeps_newest_completed_run_per_workflow(monkeypatch):
    """`gh run list` is newest-first; an older attempt must not win."""
    _gh_stub(monkeypatch, rollup_error=True, runs=[
        _run("CI", "success"),
        _run("CI", "failure"),
        _run("CI", None, status="in_progress"),
    ])
    prs = gather_prs("owner/repo", "2026-09-01")
    assert [c["conclusion"] for c in prs[0]["statusCheckRollup"]] == ["success"]


def test_gather_prs_fallback_without_runs_is_not_a_violation(monkeypatch):
    _gh_stub(monkeypatch, rollup_error=True, runs=[])
    assert check_ci_before_merge(gather_prs("owner/repo", "2026-09-01")) == []


def test_gather_prs_fallback_without_head_sha_skips_the_runs_channel(monkeypatch):
    calls = _gh_stub(monkeypatch, rollup_error=True, head_ref=None)
    prs = gather_prs("owner/repo", "2026-09-01")
    assert prs[0]["statusCheckRollup"] == []
    assert not any(c[:2] == ["run", "list"] for c in calls)


def test_gather_prs_fallback_survives_a_run_list_failure(monkeypatch):
    """Both channels unreadable: 'cannot verify', never an exception."""
    _gh_stub(monkeypatch, rollup_error=True, runs_error=True)
    prs = gather_prs("owner/repo", "2026-09-01")
    assert prs[0]["statusCheckRollup"] == []
    assert check_ci_before_merge(prs) == []


def test_gather_prs_prefers_the_readable_rollup(monkeypatch):
    calls = _gh_stub(monkeypatch,
                     rollup=[{"name": "CI", "conclusion": "SUCCESS"}],
                     runs=[_run("CI", "failure")])
    prs = gather_prs("owner/repo", "2026-09-01")
    assert prs[0]["statusCheckRollup"] == [
        {"name": "CI", "conclusion": "SUCCESS"}
    ]
    assert not any(c[:2] == ["run", "list"] for c in calls)


# ── B-025 voce 8: reviewer evidence verbatim ──────────────────────────


def test_reviewer_evidence_verbatim_compliant():
    """A code claim with a quote that appears in the diff passes."""
    diff = "+foo()\n-bar()\n"
    reports = [{
        "criterion": "4", "item_id": "ADR-0013",
        "verdict": "fail", "severity": "fatal",
        "claim": "code", "quote": "foo()", "commit": "abc1234",
        "reviewer": "standards",
    }]
    pr_diffs = {"abc1234": diff}
    assert check_reviewer_evidence_verbatim(reports, pr_diffs) == []


def test_reviewer_evidence_verbatim_detects_quote_not_in_diff():
    """A quote that does not appear verbatim in the diff is a violation
    (hallucination or stale reference)."""
    diff = "+foo()\n-bar()\n"
    reports = [{
        "criterion": "4", "item_id": "ADR-0013",
        "verdict": "fail", "severity": "fatal",
        "claim": "code", "quote": "qux()", "commit": "abc1234",
        "reviewer": "standards",
    }]
    pr_diffs = {"abc1234": diff}
    violations = check_reviewer_evidence_verbatim(reports, pr_diffs)
    assert any("ADR-0013" in v and "abc1234" in v for v in violations)


def test_reviewer_evidence_verbatim_detects_missing_quote_for_code_claim():
    """A code claim without a quote or commit is unverifiable = violation."""
    reports = [{
        "criterion": "4", "item_id": "AC-2",
        "verdict": "fail", "severity": "fatal",
        "claim": "code",  # no quote, no commit
        "reviewer": "spec",
    }]
    violations = check_reviewer_evidence_verbatim(reports, {})
    assert any("AC-2" in v and "missing" in v for v in violations)


def test_reviewer_evidence_verbatim_detects_commit_not_in_pr_diffs():
    """A commit not present in pr_diffs cannot be verified = violation."""
    reports = [{
        "criterion": "4", "item_id": "AC-2",
        "verdict": "fail", "severity": "fatal",
        "claim": "code", "quote": "foo()", "commit": "deadbeef",
        "reviewer": "spec",
    }]
    violations = check_reviewer_evidence_verbatim(reports, {})
    assert any("deadbeef" in v for v in violations)


def test_reviewer_evidence_verbatim_skips_coverage_claim():
    """A coverage claim has no quote (the mutation matrix is the proof)."""
    reports = [{
        "criterion": "4b", "item_id": "I-impl-line-12",
        "verdict": "pinned", "severity": "fatal",
        "claim": "coverage",
        "commit": "abc1234",  # no quote
        "reviewer": "mutation",
    }]
    assert check_reviewer_evidence_verbatim(reports, {}) == []


# ── B-025 voce 9: brief coverage ─────────────────────────────────────


def test_brief_coverage_compliant():
    """Every brief item has at least one row in the reports."""
    reports = [
        {"criterion": "1", "item_id": "ADR-0013", "verdict": "pass",
         "severity": "advisory", "reviewer": "standards"},
        {"criterion": "1", "item_id": "AC-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec"},
        {"criterion": "1", "item_id": "AC-2", "verdict": "n/a — out of scope",
         "severity": "advisory", "reviewer": "spec"},
    ]
    brief = ["ADR-0013", "AC-1", "AC-2"]
    assert check_brief_coverage(reports, brief) == []


def test_brief_coverage_detects_missing_item():
    """An item in the brief without a row in any report is a violation."""
    reports = [
        {"criterion": "1", "item_id": "ADR-0013", "verdict": "pass",
         "severity": "advisory", "reviewer": "standards"},
        # AC-1 missing — reviewer never answered it
    ]
    brief = ["ADR-0013", "AC-1", "AC-2"]
    violations = check_brief_coverage(reports, brief)
    assert any("AC-1" in v for v in violations), violations
    assert any("AC-2" in v for v in violations), violations
    assert not any("ADR-0013" in v for v in violations), violations


def test_brief_coverage_detects_fully_silent_reviewer():
    """If the reviewer emits no rows at all, every brief item is missing."""
    violations = check_brief_coverage([], ["ADR-0013", "AC-1", "AC-2"])
    assert len(violations) == 3


# ── B-025 voce 10: I6 zero actionable findings ──────────────────────


def test_i6_zero_actionable_compliant():
    """No open fatal actionable findings: the fix loop can exit."""
    reports = [
        {"criterion": "1", "item_id": "AC-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec", "status": "open"},
        {"criterion": "1", "item_id": "AC-2", "verdict": "fail",
         "severity": "fatal", "reviewer": "spec", "status": "resolved"},
        {"criterion": "1", "item_id": "AC-3", "verdict": "n/a",
         "severity": "advisory", "reviewer": "spec", "status": "open"},
    ]
    assert check_i6_zero_actionable(reports) == []


def test_i6_zero_actionable_detects_open_fatal():
    """An open fatal actionable finding blocks the loop exit (I6)."""
    reports = [
        {"criterion": "1", "item_id": "AC-1", "verdict": "actionable",
         "severity": "fatal", "reviewer": "spec", "status": "open"},
    ]
    violations = check_i6_zero_actionable(reports)
    assert any("AC-1" in v and "actionable" in v for v in violations), violations


def test_i6_zero_actionable_closed_finding_passes():
    """A fatal actionable finding with status=resolved is closed."""
    reports = [
        {"criterion": "1", "item_id": "AC-1", "verdict": "actionable",
         "severity": "fatal", "reviewer": "spec", "status": "resolved"},
    ]
    assert check_i6_zero_actionable(reports) == []


def test_i6_zero_actionable_advisory_never_blocks():
    """Advisory findings ride along in the handoff, not in I6."""
    reports = [
        {"criterion": "1", "item_id": "AC-1", "verdict": "actionable",
         "severity": "advisory", "reviewer": "ponytail", "status": "open"},
    ]
    assert check_i6_zero_actionable(reports) == []


# ── B-025 voce 2: premises falsified ──────────────────────────────


def test_premises_falsified_compliant():
    """A premise with verdict=falsified + evidence passes."""
    reports = [
        {"criterion": "1", "item_id": "P-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec",
         "premise": {"claim": "user count grows linearly",
                     "verdict": "falsified",
                     "evidence": "spec.md:42 (PoC: 10k users, flat latency)"}},
    ]
    assert check_premises_falsified(reports) == []


def test_premises_falsified_detects_untested():
    """A premise with verdict=untested is a violation: design did not
    falsify its own claim."""
    reports = [
        {"criterion": "1", "item_id": "P-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec",
         "premise": {"claim": "user count grows linearly",
                     "verdict": "untested"}},
    ]
    violations = check_premises_falsified(reports)
    assert any("P-1" in v and "untested" in v for v in violations), violations


def test_premises_falsified_detects_confirmed_without_evidence():
    """A premise claimed confirmed without an evidence pointer is unbacked."""
    reports = [
        {"criterion": "1", "item_id": "P-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec",
         "premise": {"claim": "system invariant I4 holds",
                     "verdict": "confirmed"}},
    ]
    violations = check_premises_falsified(reports)
    assert any("P-1" in v and "evidence" in v for v in violations), violations


def test_premises_falsified_skips_reports_without_premise():
    """Reports without a premise field are not subject to this check."""
    reports = [
        {"criterion": "1", "item_id": "AC-1", "verdict": "pass",
         "severity": "advisory", "reviewer": "spec"},
    ]
    assert check_premises_falsified(reports) == []


# ── B-025 voce 3: phase-2 decision stated ───────────────────────────


def test_phase2_decision_stated_compliant_done():
    """A UI handoff with 'Phase 2: done' passes."""
    handoffs = [{
        "name": "h-ui.md",
        "text": "UI changes: rewrote the modal. Phase 2: done.\n",
    }]
    assert check_phase2_decision_stated(handoffs) == []


def test_phase2_decision_stated_compliant_skipped():
    """A UI handoff with 'Phase 2: skipped — API-only' passes."""
    handoffs = [{
        "name": "h-ui.md",
        "text": "UI changes: header bar refactor. "
                "Phase 2: skipped — header-only, no flows changed.\n",
    }]
    assert check_phase2_decision_stated(handoffs) == []


def test_phase2_decision_stated_detects_ui_without_decision():
    """A handoff with no phase-2 decision is a violation, even when the
    session did not touch UI ('Phase 2: n/a' is the explicit no-UI form)."""
    handoffs = [{
        "name": "h-ui.md",
        "text": "Touched the UI but forgot to say anything about phase 2.\n",
    }]
    violations = check_phase2_decision_stated(handoffs)
    assert any("h-ui.md" in v for v in violations), violations


def test_phase2_decision_stated_skips_non_ui_handoffs():
    """Non-UI handoffs that explicitly declare 'Phase 2: n/a' pass."""
    handoffs = [{
        "name": "h-backend.md",
        "text": "Backend refactor, no UI changes.\n"
                "Phase 2: n/a — backend only.\n",
    }]
    assert check_phase2_decision_stated(handoffs) == []


# ── B-025 voce 4: invariant verification exists ──────────────────


def test_invariant_verification_exists_compliant():
    """INVARIANTS.md is in the expected form: every invariant has a
    verification line citing a real `audit.py check_*` function."""
    violations = check_invariant_verification_exists()
    assert violations == [], violations


def test_invariant_verification_detects_missing_function(tmp_path, monkeypatch):
    """If INVARIANTS.md cites a function that doesn't exist in audit.py,
    the check detects it (anti-vacuity)."""
    # Simulate a stale reference: rewrite audit.py to drop the function
    monkeypatch.setattr(audit, "WORKFLOW_DIR", tmp_path)
    inv = tmp_path / "INVARIANTS.md"
    inv.write_text(
        "## I99 — test invariant\n\n- **Verification**: `audit.py check_ghost_func`\n",
        encoding="utf-8",
    )
    (tmp_path / "audit.py").write_text("# empty\n", encoding="utf-8")
    (tmp_path / "guards").mkdir()
    (tmp_path / "guards" / "x.py").write_text("", encoding="utf-8")
    violations = check_invariant_verification_exists()
    assert any("I99" in v and "check_ghost_func" in v for v in violations), violations


# ── B-025 voce 12: session claim present ─────────────────────────────


def test_session_claim_present_compliant_when_claimed_with_file():
    """A handoff that said it claimed and the claim file exists passes."""
    handoffs = [{
        "name": "h.md",
        "text": "- **Repo**: owner/foo\n\nSession claimed a primary tree.\n",
    }]
    assert check_session_claim_present(handoffs, {"owner/foo"}) == []


def test_session_claim_present_detects_missing_claim_file():
    """A handoff that said it claimed but no claim file exists is a violation."""
    handoffs = [{
        "name": "h.md",
        "text": "- **Repo**: owner/foo\n\nSession claimed a primary tree.\n",
    }]
    violations = check_session_claim_present(handoffs, set())
    assert any("h.md" in v and "owner/foo" in v for v in violations), violations


def test_session_claim_present_skips_handoff_that_did_not_claim():
    """A handoff that did not claim a primary tree is not subject to the check."""
    handoffs = [{
        "name": "h.md",
        "text": "No tree claim, just a normal session.\n",
    }]
    assert check_session_claim_present(handoffs, set()) == []


# ── B-025 voce 13: worktrees cleaned ────────────────────────────────


def test_worktrees_cleaned_compliant_no_extras():
    """A single (principal) worktree is fine: nothing to clean."""
    worktrees = [{"path": "/repo", "head": "abc", "branch": "refs/heads/master"}]
    assert check_worktrees_cleaned(worktrees) == []


def test_worktrees_cleaned_detects_extras():
    """A non-principal worktree triggers a violation (must be cleaned
    or its retention documented in the handoff)."""
    worktrees = [
        {"path": "/repo", "branch": "refs/heads/master"},
        {"path": "/repo-feature", "branch": "refs/heads/feature"},
    ]
    violations = check_worktrees_cleaned(worktrees)
    assert any("feature" in v and "/repo-feature" in v for v in violations), violations


def test_worktrees_cleaned_flags_locked_worktree_critical():
    """A locked worktree is critical — it cannot be removed automatically."""
    worktrees = [
        {"path": "/repo", "branch": "refs/heads/master"},
        {"path": "/repo-locked", "branch": "refs/heads/exp", "locked": True},
    ]
    violations = check_worktrees_cleaned(worktrees)
    assert any("CRITICAL" in v and "/repo-locked" in v for v in violations), violations


# ── B-025 voce 14: rationale entry in PR ────────────────────────────


def test_rationale_entry_in_pr_compliant():
    """A PR that touches phases/*.md AND RATIONALE.md passes."""
    prs = [{
        "number": 1,
        "body": "Add D-024 entry in RATIONALE.md",
        "files": [{"path": "phases/5-program-design.md"}, {"path": "RATIONALE.md"}],
    }]
    assert check_rationale_entry_in_pr(prs) == []


def test_rationale_entry_in_pr_detects_orphan_change():
    """A PR that touches phases/*.md but not RATIONALE.md is a violation."""
    prs = [{
        "number": 2,
        "body": "Tweak phase 5 wording",
        "files": [{"path": "phases/5-program-design.md"}],
    }]
    violations = check_rationale_entry_in_pr(prs)
    assert any("#2" in v and "orphan" in v for v in violations), violations


def test_rationale_entry_in_pr_skips_unrelated_prs():
    """A PR that doesn't touch the rule registry is not subject to the check."""
    prs = [{
        "number": 3,
        "body": "Refactor the audit module",
        "files": [{"path": "audit.py"}],
    }]
    assert check_rationale_entry_in_pr(prs) == []


# ── B-025 voce 15: audit.py --repo cited in latest handoff ─────────


def test_audit_in_handoff_compliant():
    """The latest handoff cites the audit.py --repo command."""
    handoffs = [{
        "name": "h-20261017.md",
        "text": "## Verification\n\n`python ~/agentic-workflow/audit.py "
                "--repo owner/foo`\n",
    }]
    assert check_audit_in_handoff(handoffs) == []


def test_audit_in_handoff_detects_missing_command():
    """The latest handoff has Verification but no audit.py --repo citation."""
    handoffs = [{
        "name": "h-20261017.md",
        "text": "## Verification\n\nManual review by the committente.\n",
    }]
    violations = check_audit_in_handoff(handoffs)
    assert any("h-20261017.md" in v for v in violations), violations


def test_audit_in_handoff_picks_latest_by_name():
    """When multiple handoffs are given, the one with the lexicographically
    largest name is treated as the latest."""
    handoffs = [
        {"name": "h-20261016.md", "text": "No audit cite here.\n"},
        {"name": "h-20261017.md", "text": "`audit.py --repo owner/foo`\n"},
    ]
    assert check_audit_in_handoff(handoffs) == []


