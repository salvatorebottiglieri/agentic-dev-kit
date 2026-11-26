"""Mutation tests for audit.py — every check must be able to fail.

Anti-vacuity rule (INVARIANTS.md): a check that never fails on violating
input is worse than no check. Each test feeds a deliberately violating
input and asserts the violation is detected; a green twin asserts the
compliant input passes.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

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
    check_model_declared,
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

# Real commit from example/example-repo, where the fallback
# was needed (a fine-grained PAT without check-run access).
SHA = "0887aaea7ae21185fee524a59facc500e43465f3"

#: I9 is scoped per project: only handoffs declaring this repo are read.
REPO = "example/example-repo"


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


# ── D-051: model declared in the PR ──────────────────────────────────


def test_model_declared_compliant():
    prs = [{
        "number": 1, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Model: docs/models/demo.md",
        "files": [{"path": "docs/models/demo.md"}],
    }]
    assert check_model_declared(prs) == []


def test_model_declared_reasoned_skip():
    prs = [{
        "number": 2, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Model: n/a — pure config change",
    }]
    assert check_model_declared(prs) == []


def test_model_declared_missing_is_a_violation():
    prs = [{
        "number": 3, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Fixes #1",
    }]
    violations = check_model_declared(prs)
    assert any("[D-051]" in v and "#3" in v for v in violations), violations


def test_model_declared_n_a_without_reason_is_a_violation():
    prs = [{
        "number": 4, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Model: n/a",
    }]
    violations = check_model_declared(prs)
    assert any("without a reason" in v for v in violations), violations


def test_model_declared_ignores_prs_before_the_rule():
    prs = [{
        "number": 5, "merged": True, "mergedAt": "2026-11-09T10:00:00Z",
        "body": "old PR without a model line",
    }]
    assert check_model_declared(prs) == []


def test_model_declared_requires_docs_models_path():
    prs = [{
        "number": 6, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Model: README.md",
        "files": [{"path": "README.md"}],
    }]
    violations = check_model_declared(prs)
    assert any("docs/models" in v for v in violations), violations


def test_model_declared_file_must_be_in_diff():
    prs = [{
        "number": 7, "merged": True, "mergedAt": "2026-11-11T10:00:00Z",
        "body": "Model: docs/models/demo.md",
        "files": [{"path": "src/app.py"}],
    }]
    violations = check_model_declared(prs)
    assert any("not in the PR diff" in v for v in violations), violations


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


def test_skip_annotations_accepts_lowercase_model_skip():
    issues = [{
        "number": 8, "state": "CLOSED",
        "body": "Model skipped — no flow, no state",
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


def test_audit_marker_list_is_one_module_constant_and_check_skip_annotations_uses_it(monkeypatch):
    """A ticket carrying only an entry of `audit.SPEC_MARKERS` is not flagged; the list is the
    one `check_skip_annotations` reads (a list edit changes the audit)."""
    assert "## Solution" in audit.SPEC_MARKERS and "## Program Design" in audit.SPEC_MARKERS
    monkeypatch.setattr(audit, "SPEC_MARKERS", ("## Only This",))
    kept = [{"number": 20, "state": "CLOSED", "body": "## Only This"}]
    dropped = [{"number": 21, "state": "CLOSED", "body": "## Solution"}]
    assert check_skip_annotations(kept) == []
    assert any("#21" in v for v in check_skip_annotations(dropped))


@pytest.mark.parametrize("marker", [
    "## Problem Statement", "## Solution", "## What to build", "## Acceptance",
    "## System Architecture", "## Invariants", "## Program Design",
])
def test_each_spec_marker_alone_in_the_body_makes_an_issue_carry_a_spec(marker):
    assert audit.issue_has_spec({"body": f"{marker}\nx"}) is True


@pytest.mark.parametrize("issue", [
    {"body": "## Problem Statement\nx"},
    {"body": "prose", "comments": [{"body": "## Solution\nfix"}]},
    {"body": "prose", "comments": [{"body": "Skipped — docs only"}]},
    {"body": "Model SKIPPED — no flow"},
])
def test_issue_has_spec_is_true_for_a_marker_in_the_body_a_marker_in_a_comment_or_skipped(issue):
    assert audit.issue_has_spec(issue) is True


@pytest.mark.parametrize("issue", [
    {"body": "fix the typo in the docs"},
    {"body": "", "comments": []},
    {"body": None, "comments": [{"body": "looks good"}, {"body": None}]},
    {"body": "see the Solution below", "comments": [{"body": "### Notes"}]},
])
def test_issue_has_spec_is_false_for_prose_and_for_an_empty_or_none_body(issue):
    assert audit.issue_has_spec(issue) is False


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
    assert audit._handoff_repo("- **Repo**: Example/x\n") == "Example/x"
    assert audit._handoff_repo("Repo: owner/name\n") == "owner/name"
    assert audit._handoff_repo("**repo:** owner/name (audited)\n") == "owner/name"
    assert audit._handoff_repo("## Repo state\nExample/example-x\n") is None


def test_followups_tracked_ignores_handoffs_of_another_repo():
    """The third false-positive class (2026-09-19): the corpus of handoffs is
    shared by every project, the tracker is not. The vault handoff
    citing its own #319 resolves at home and must not be judged here."""
    handoffs = [_handoff(
        "Closes #316 #317 #318 #319\n- Advisory: legacy note, already tracked\n",
        name="handoff-diario-mockup-fidelity-20260917.md",
        repo="acme/vault",
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
                         repo="example/EXAMPLE-REPO")]
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
    monkeypatch.setattr(audit, "GH_BACKOFF", 0)

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


def test_a_pr_whose_view_fails_still_falls_back_to_the_runs(monkeypatch):
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


def test_a_pr_whose_view_fails_without_head_sha_has_the_views_unread_and_skips_the_runs_channel(monkeypatch):
    calls = _gh_stub(monkeypatch, rollup_error=True, head_ref=None)
    prs = gather_prs("owner/repo", "2026-09-01")
    assert isinstance(prs[0]["statusCheckRollup"], audit.Unread)
    assert prs[0]["statusCheckRollup"].call.startswith("gh pr view 12")
    assert check_ci_before_merge(prs) == []
    assert not any(c[:2] == ["run", "list"] for c in calls)


def test_a_pr_whose_view_and_runs_are_both_unreadable_has_an_unread_rollup_and_is_not_a_ci_violation(monkeypatch):
    """Both channels unreadable: 'cannot verify', never an exception."""
    _gh_stub(monkeypatch, rollup_error=True, runs_error=True)
    prs = gather_prs("owner/repo", "2026-09-01")
    rollup = prs[0]["statusCheckRollup"]
    assert isinstance(rollup, audit.Unread)
    assert rollup.call.startswith("gh run list")
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


def _i6_row(verdict, **kw):
    row = {"criterion": "1", "item_id": "AC-1", "verdict": verdict,
           "severity": "fatal", "reviewer": "spec", "status": "open"}
    row.update(kw)
    return row


@pytest.mark.parametrize("verdict", ["fail", "missing", "blind-test", "no-test"])
def test_i6_open_fatal_non_pass_verdict_blocks(verdict):
    violations = check_i6_zero_actionable([_i6_row(verdict)])
    assert any("AC-1" in v and verdict in v for v in violations), violations


@pytest.mark.parametrize("verdict", ["pass", "pinned", "n/a — not applicable"])
def test_i6_open_fatal_pass_like_verdict_does_not_block(verdict):
    assert check_i6_zero_actionable([_i6_row(verdict)]) == []


def test_i6_blocker_closed_by_resolved_row_same_item_and_criterion():
    reports = [
        _i6_row("missing", _feature="f"),
        _i6_row("pass", _feature="f", status="resolved"),
    ]
    assert check_i6_zero_actionable(reports) == []


def test_i6_blocker_not_closed_by_resolved_row_of_other_criterion():
    reports = [
        _i6_row("missing", _feature="f"),
        _i6_row("pass", _feature="f", status="fixed", criterion="2"),
    ]
    violations = check_i6_zero_actionable(reports)
    assert any("AC-1" in v for v in violations), violations


def test_i6_blocker_not_closed_by_resolved_row_of_other_feature():
    """Characterization: the close key includes ``_feature``."""
    reports = [
        _i6_row("missing", _feature="f"),
        _i6_row("pass", _feature="g", status="resolved"),
    ]
    assert any("AC-1" in v for v in check_i6_zero_actionable(reports))


def test_i6_blocker_not_closed_by_resolved_row_of_other_item_id():
    """Characterization: the close key includes ``item_id``."""
    reports = [
        _i6_row("missing", _feature="f"),
        _i6_row("pass", _feature="f", status="resolved", item_id="AC-2"),
    ]
    assert any("AC-1" in v for v in check_i6_zero_actionable(reports))


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
        "text": "## Verification\n\n`python ~/agentic-dev-kit/audit.py "
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




# ── #99 audit-speed: pooled, retried reads and unread items ───────────


def _transient(failures, answer):
    """A fake ``_gh``: raises RuntimeError *failures* times, then answers."""
    attempts = []

    def fake(args):
        attempts.append(list(args))
        if len(attempts) <= failures:
            raise RuntimeError("gh failed: error connecting to api.github.com")
        return answer

    return fake, attempts


CALL = ["api", "repos/o/r/commits/abc"]


def test_a_transient_failure_is_retried_and_the_data_arrives(monkeypatch):
    fake, attempts = _transient(2, {"ok": 1})
    monkeypatch.setattr(audit, "_gh", fake)
    assert audit._gh_many([CALL], sleep=lambda s: None) == [{"ok": 1}]
    assert len(attempts) == 3


def test_retries_wait_with_a_growing_backoff(monkeypatch):
    fake, _ = _transient(2, {"ok": 1})
    monkeypatch.setattr(audit, "_gh", fake)
    waits = []
    audit._gh_many([CALL], sleep=waits.append)
    assert waits == [audit.GH_BACKOFF, 2 * audit.GH_BACKOFF]


def test_a_persistent_failure_becomes_an_unread_item_naming_the_call_and_the_reason(monkeypatch):
    fake, attempts = _transient(10**6, None)
    monkeypatch.setattr(audit, "_gh", fake)
    [got] = audit._gh_many([CALL], sleep=lambda s: None)
    assert isinstance(got, audit.Unread)
    assert got.call == "gh api repos/o/r/commits/abc"
    assert "error connecting to api.github.com" in got.reason
    assert got.reason.startswith("RuntimeError: ")
    assert len(attempts) == audit.GH_ATTEMPTS


def test_a_persistent_failure_stops_after_three_attempts_with_two_waits_and_none_after_the_last(monkeypatch):
    fake, attempts = _transient(10**6, None)
    monkeypatch.setattr(audit, "_gh", fake)
    waits = []
    [got] = audit._gh_many([CALL], sleep=waits.append)
    assert isinstance(got, audit.Unread)
    assert len(attempts) == 3
    assert waits == [0.5, 1.0]


def test_gather_pr_diffs_reads_all_the_commits_in_one_pooled_call(monkeypatch):
    seen = []

    def recorder(calls, sleep=None):
        seen.append(calls)
        return [{"files": []} for _ in calls]

    monkeypatch.setattr(audit, "_gh_many", recorder)
    reports = [{"commit": "b"}, {"commit": "a"}, {"commit": "a"}]
    audit.gather_pr_diffs("o/r", reports)
    assert seen == [[["api", "repos/o/r/commits/a"], ["api", "repos/o/r/commits/b"]]]


def test_gather_prs_reads_all_the_pr_views_in_one_pooled_call(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "_gh", lambda args: [
        {"number": n, "body": "", "headRefOid": f"s{n}"} for n in (1, 2, 3)])

    def recorder(calls, sleep=None):
        seen.append(calls)
        return [{"state": "MERGED", "statusCheckRollup": []} for _ in calls]

    monkeypatch.setattr(audit, "_gh_many", recorder)
    audit.gather_prs("o/r", "2026-09-01")
    assert seen == [[["pr", "view", str(n), "--repo", "o/r",
                      "--json", "state,statusCheckRollup"] for n in (1, 2, 3)]]


def test_a_failing_call_does_not_stop_the_other_calls(monkeypatch):
    def fake(args):
        if args[-1] == "c4":
            raise RuntimeError("boom")
        return {"id": args[-1]}

    monkeypatch.setattr(audit, "_gh", fake)
    got = audit._gh_many([["x", f"c{n}"] for n in range(10)], sleep=lambda s: None)
    assert [g for i, g in enumerate(got) if i != 4] == [
        {"id": f"c{n}"} for n in range(10) if n != 4]
    assert isinstance(got[4], audit.Unread) and got[4].call == "gh x c4"


def test_an_unexpected_exception_in_a_read_is_an_unread_item_not_a_crash(monkeypatch):
    def fake(args):
        raise {"v": ValueError("no json"), "o": OSError("gh missing")}[args[0]]

    monkeypatch.setattr(audit, "_gh", fake)
    got = audit._gh_many([["v"], ["o"]], sleep=lambda s: None)
    assert [type(g) for g in got] == [audit.Unread, audit.Unread]
    assert got[0].reason.startswith("ValueError: ")
    assert got[1].reason.startswith("OSError: ")


def test_results_come_back_in_the_order_of_the_requests(monkeypatch):
    first_may_finish = threading.Event()

    def fake(args):
        if args[0] == "n0":
            assert first_may_finish.wait(5), "the last call never ran"
        if args[0] == "n7":
            first_may_finish.set()
        return {"id": args[0]}

    monkeypatch.setattr(audit, "_gh", fake)
    got = audit._gh_many([[f"n{n}"] for n in range(8)], sleep=lambda s: None)
    assert got == [{"id": f"n{n}"} for n in range(8)]


def test_calls_run_concurrently_up_to_the_pool_size_and_no_more(monkeypatch):
    barrier = threading.Barrier(8)
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def fake(args):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            barrier.wait(timeout=5)
        finally:
            with lock:
                state["now"] -= 1
        return {"id": args[0]}

    monkeypatch.setattr(audit, "_gh", fake)
    got = audit._gh_many([[f"n{n}"] for n in range(16)], sleep=lambda s: None)
    assert all(isinstance(g, dict) for g in got)  # the barrier released: eight at once
    assert state["max"] == 8


def test_no_more_than_eight_reads_are_in_flight_at_once(monkeypatch):
    """witness 24 reads that each hold 50 ms - distinguishes a pool of 8 (peak 8) from a wider pool
    (peak well above 8); the Barrier test above only proves that eight can be at once."""
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def fake(args):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return {"id": args[0]}

    monkeypatch.setattr(audit, "_gh", fake)
    got = audit._gh_many([[f"n{n}"] for n in range(24)], sleep=lambda s: None)
    assert len(got) == 24 and all(isinstance(g, dict) for g in got)
    assert state["max"] <= 8


def _commit_fake(unreadable=()):
    def fake(args):
        sha = args[1].rsplit("/", 1)[1]
        if sha in unreadable:
            raise RuntimeError(f"gh api failed: no commit {sha}")
        return {"files": [{"patch": f"+line {sha}\n"}]}

    return fake


def _twenty_commit_reports():
    return [{"commit": f"c{n:02d}", "quote": f"+line c{n:02d}" if n % 3 else "+absent",
             "item_id": str(n), "criterion": "4"} for n in range(20)]


def test_the_same_answers_give_the_same_diffs_in_a_pool_of_one_and_of_eight(monkeypatch):
    monkeypatch.setattr(audit, "_gh", _commit_fake())
    monkeypatch.setattr(audit, "GH_BACKOFF", 0)
    reports = _twenty_commit_reports()
    results = {}
    for workers in (1, 8):
        monkeypatch.setattr(audit, "GH_WORKERS", workers)
        diffs = audit.gather_pr_diffs("o/r", reports)
        results[workers] = (list(diffs.items()),
                            check_reviewer_evidence_verbatim(reports, diffs))
    assert len(results[1][0]) == 20 and results[1][1]  # witness: violations exist
    assert results[1] == results[8]


def test_the_same_answers_give_the_same_pr_rollups_in_a_pool_of_one_and_of_eight(monkeypatch):
    monkeypatch.setattr(audit, "GH_BACKOFF", 0)

    def fake(args):
        if args[:2] == ["pr", "list"]:
            return [{"number": n, "body": "Fixes #1", "headRefOid": f"s{n}"}
                    for n in range(1, 21)]
        if args[:2] == ["pr", "view"]:
            n = int(args[2])
            if n % 5 == 0:
                raise RuntimeError("gh pr view failed: no access")
            return {"state": "MERGED", "statusCheckRollup": [
                {"name": "CI", "conclusion": "FAILURE" if n % 3 == 0 else "SUCCESS"}]}
        if args[:2] == ["run", "list"]:
            return [{"name": "CI", "status": "completed", "event": "pull_request",
                     "conclusion": "failure"}]
        raise AssertionError(args)

    monkeypatch.setattr(audit, "_gh", fake)
    results = {}
    for workers in (1, 8):
        monkeypatch.setattr(audit, "GH_WORKERS", workers)
        prs = gather_prs("o/r", "2026-09-01")
        results[workers] = (prs, check_ci_before_merge(prs))
    assert len(results[1][1]) > 5  # witness: violations exist
    assert results[1] == results[8]


def test_gather_pr_diffs_keeps_the_readable_commits_when_one_is_unreadable(monkeypatch):
    monkeypatch.setattr(audit, "GH_BACKOFF", 0)
    monkeypatch.setattr(audit, "_gh", _commit_fake(unreadable={"bad"}))
    diffs = audit.gather_pr_diffs(
        "o/r", [{"commit": "aaa"}, {"commit": "bad"}, {"commit": "zzz"}])
    assert diffs["aaa"] == "+line aaa" and diffs["zzz"] == "+line zzz"
    assert isinstance(diffs["bad"], audit.Unread)
    assert diffs["bad"].call == "gh api repos/o/r/commits/bad"


def test_gather_pr_diffs_marks_a_non_dict_response_as_unread(monkeypatch):
    monkeypatch.setattr(audit, "_gh", lambda args: ["not", "a", "dict"])
    diffs = audit.gather_pr_diffs("o/r", [{"commit": "abc"}])
    assert isinstance(diffs["abc"], audit.Unread)
    assert "unexpected API response" in diffs["abc"].reason


def test_an_unread_commit_is_not_a_b025_8_violation():
    unread = audit.Unread("gh api repos/o/r/commits/aaaaaaaa1", "RuntimeError: x")
    reports = [
        {"commit": "aaaaaaaa1", "quote": "+q", "item_id": "1", "criterion": "4"},
        {"commit": "bbbbbbbb2", "quote": "+q", "item_id": "2", "criterion": "4"},
        {"commit": "bbbbbbbb2", "item_id": "3", "criterion": "4"},
    ]
    got = check_reviewer_evidence_verbatim(
        reports, {"aaaaaaaa1": unread, "bbbbbbbb2": "+other"})
    assert got == [
        ("[B-025/8] finding item_id=2 criterion=4 commit=bbbbbbbb: "
         "quote does not appear verbatim in diff"),
        ("[B-025/8] finding item_id=3 criterion=4 claim=code: "
         "missing quote or commit (cannot verify)"),
    ]


def _main_world(monkeypatch, tmp_path, *, reports, diffs, prs=()):
    """Stub every gatherer so only the diffs/prs under test can be wrong."""
    store = tmp_path / "r.sqlite"
    store.write_text("")
    for name, value in (
        ("gather_prs", lambda repo, since: list(prs)),
        ("gather_issues", lambda repo, since: []),
        ("gather_gate_config", lambda repo: {"configured": {}, "ci_text": ""}),
        ("gather_handoffs", lambda days=7: []),
        ("gather_known_refs", lambda repo: set()),
        ("check_recent_handoff", lambda handoffs: []),
        ("gather_reviewer_reports", lambda repo, store=None: reports),
        ("gather_brief_items", lambda repo, store=None: {"f": ["1", "2"]}),
        ("gather_feature_dates", lambda repo, store=None: {}),
        ("gather_pr_diffs", lambda repo, reps: diffs),
    ):
        monkeypatch.setattr(audit, name, value)
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(store)])


def _row_for(item, commit, quote):
    return {"repo": "o/r", "actor": "spec-1", "criterion": "4", "claim": "code",
            "item_id": item, "verdict": "pass", "severity": "minor",
            "reviewer": "spec", "status": "resolved", "_feature": "f",
            "commit": commit, "quote": quote}


UNREAD = audit.Unread("gh api repos/o/r/commits/unreadcommit", "RuntimeError: boom")


def test_main_lists_unread_items_after_the_violations_and_exits_1(
        monkeypatch, tmp_path, capsys):
    _main_world(monkeypatch, tmp_path,
                reports=[_row_for("1", "unreadcommit", "+q"),
                         _row_for("2", "goodcommit1", "+nope")],
                diffs={"unreadcommit": UNREAD, "goodcommit1": "+other"})
    assert audit.main() == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "1 violation(s):"
    assert out[1].startswith("  - [B-025/8] finding item_id=2")
    assert out[2] == ("1 item(s) could not be read "
                      "(not checked; this is not a pass):")
    assert out[3] == "  - gh api repos/o/r/commits/unreadcommit: RuntimeError: boom"
    assert len(out) == 4


def test_main_with_only_unread_items_prints_no_ok_and_exits_as_decision_1_says(
        monkeypatch, tmp_path, capsys):
    _main_world(monkeypatch, tmp_path,
                reports=[_row_for("1", "unreadcommit", "+q"),
                         _row_for("2", "goodcommit1", "+same")],
                diffs={"unreadcommit": UNREAD, "goodcommit1": "+same"})
    assert audit.main() == 1
    out = capsys.readouterr().out
    assert "ok — all workflow invariants hold" not in out
    assert "violation(s)" not in out
    assert "1 item(s) could not be read (not checked; this is not a pass):" in out
    assert "  - gh api repos/o/r/commits/unreadcommit: RuntimeError: boom" in out


def test_main_lists_an_unread_pr_rollup_too(monkeypatch, tmp_path, capsys):
    pr = {"number": 7, "merged": True, "body": "Fixes #1", "mergedAt": "2020-01-01",
          "statusCheckRollup": audit.Unread("gh pr view 7", "RuntimeError: nope")}
    _main_world(monkeypatch, tmp_path, reports=[], diffs={}, prs=[pr])
    monkeypatch.setattr(audit, "gather_brief_items", lambda repo, store=None: {})
    assert audit.main() == 1
    out = capsys.readouterr().out
    assert "  - gh pr view 7: RuntimeError: nope" in out
    assert "ok — all workflow invariants hold" not in out


def test_main_with_nothing_wrong_and_nothing_unread_prints_ok_and_exits_0(
        monkeypatch, tmp_path, capsys):
    _main_world(monkeypatch, tmp_path,
                reports=[_row_for("1", "goodcommit1", "+same"),
                         _row_for("2", "goodcommit1", "+same")],
                diffs={"goodcommit1": "+same"})
    assert audit.main() == 0
    assert capsys.readouterr().out == "ok — all workflow invariants hold\n"


def test_a_failed_pr_list_still_aborts_with_an_error(monkeypatch, tmp_path, capsys):
    _main_world(monkeypatch, tmp_path, reports=[], diffs={})

    def boom(repo, since):
        raise RuntimeError("gh pr list failed: HTTP 502")

    monkeypatch.setattr(audit, "gather_prs", boom)
    assert audit.main() == 1
    assert "error: gh pr list failed: HTTP 502" in capsys.readouterr().err


# --- audit-window #102: the --since window also bounds the reviewer rows --------------------------

def _dated_store(tmp_path, features):
    """A store of dated features, inserted directly: the module's writer stamps recorded_at with now() and these tests need chosen dates."""
    import json
    mod = audit._store_module()
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = mod.open_store(tmp_path / "w.sqlite")
    with conn:
        for f in features:
            for r in f["rows"]:
                conn.execute(
                    "INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion,"
                    " verdict, severity, status, actor, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f["at"], "o/r", f["name"], f"{f['name']}-spec", r["item_id"], r["criterion"],
                     r["verdict"], r["severity"], r.get("status", "open"), r.get("actor", ""),
                     json.dumps(r)))
            for item in f["brief"]:
                conn.execute("INSERT INTO brief_item (repo, feature, item_id, recorded_at) VALUES (?,?,?,?)",
                             ("o/r", f["name"], item, f["at"]))
    conn.close()
    return tmp_path / "w.sqlite"


def test_gather_feature_dates_with_no_store_is_empty_and_creates_nothing(tmp_path):
    gone = tmp_path / "state" / "reports.sqlite"
    assert audit.gather_feature_dates("o/r", store=None) == {}
    assert audit.gather_feature_dates("o/r", store=gone) == {}
    assert not gone.exists() and not gone.parent.exists()


def test_gather_feature_dates_reads_the_dates_of_the_store_without_writing_it(tmp_path):
    store = _dated_store(tmp_path, [_feature("a", "2026-11-05T10:00:00", ["1"])])
    before = store.read_bytes()
    assert audit.gather_feature_dates("o/r", store=store) == {"a": "2026-11-05T10:00:00"}
    assert store.read_bytes() == before


def _wrow(feature, item, **over):
    return {**{k: v for k, v in _row_for(item, f"sha-{feature}", f"+line sha-{feature}").items()
               if k != "_feature"}, "severity": "advisory", **over}


def _feature(name, at, items, *, brief=None, **over):
    """A feature to store: rows citing ``sha-<name>`` (the quote is in that commit's fake diff) and its brief."""
    return {"name": name, "at": at, "rows": [_wrow(name, i, **over) for i in items],
            "brief": list(items) if brief is None else brief}


def _window_main(monkeypatch, tmp_path, features, since, *, unreadable=()):
    """Run ``main`` on a dated store with only the PR/issue/handoff gathers stubbed; returns
    (exit code, the commits read from GitHub). ``_gh`` answers commit reads only."""
    store = _dated_store(tmp_path, features)
    for name, value in (
        ("gather_prs", lambda repo, since: []),
        ("gather_issues", lambda repo, since: []),
        ("gather_gate_config", lambda repo: {"configured": {}, "ci_text": ""}),
        ("gather_handoffs", lambda days=7: []),
        ("gather_known_refs", lambda repo: set()),
        ("check_recent_handoff", lambda handoffs: []),
    ):
        monkeypatch.setattr(audit, name, value)
    monkeypatch.setattr(audit, "GH_BACKOFF", 0)
    commits: list[str] = []
    inner = _commit_fake(unreadable)

    def fake(args):
        commits.append(args[1].rsplit("/", 1)[1])
        return inner(args)

    monkeypatch.setattr(audit, "_gh", fake)
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(store), "--since", since])
    return audit.main, commits


OLD, NEW = "2026-10-01T09:00:00", "2026-11-15T09:00:00"


def test_a_feature_dated_before_since_is_left_out_with_all_its_rows_and_its_brief():
    rows = [{"_feature": "old", "item_id": "1"}, {"_feature": "old", "item_id": "2"},
            {"_feature": "new", "item_id": "1"}]
    kept, briefs, left_out = audit.apply_window(
        rows, {"old": ["1", "2"], "new": ["1"]}, {"old": OLD, "new": NEW}, "2026-11-01")
    assert kept == [{"_feature": "new", "item_id": "1"}]
    assert briefs == {"new": ["1"]} and left_out == {"old": 2}


def test_a_feature_dated_on_since_is_kept():
    rows = [{"_feature": "edge", "item_id": "1"}]
    kept, briefs, left_out = audit.apply_window(
        rows, {"edge": ["1"]}, {"edge": "2026-11-01T00:00:00"}, "2026-11-01")
    assert (kept, briefs, left_out) == (rows, {"edge": ["1"]}, {})


def test_a_feature_dated_days_before_since_in_the_same_month_is_left_out():
    rows = [{"_feature": "early", "item_id": "1"}]
    kept, briefs, left_out = audit.apply_window(
        rows, {"early": ["1"]}, {"early": "2026-11-05T09:00:00"}, "2026-11-10")
    assert (kept, briefs, left_out) == ([], {}, {"early": 1})


def test_a_feature_dated_the_last_second_before_since_is_left_out():
    rows = [{"_feature": "eve", "item_id": "1"}]
    kept, briefs, left_out = audit.apply_window(
        rows, {"eve": ["1"]}, {"eve": "2026-10-31T23:59:59"}, "2026-11-01")
    assert (kept, briefs, left_out) == ([], {}, {"eve": 1})


def test_a_feature_that_straddles_the_edge_is_kept_whole():
    # witness: the feature's date is its LATEST row (the second), the first row alone is older than since
    rows = [{"_feature": "f", "item_id": "1", "at": "2026-10-30"}, {"_feature": "f", "item_id": "2"}]
    kept, briefs, left_out = audit.apply_window(rows, {"f": ["1", "2"]}, {"f": "2026-11-02T08:00:00"}, "2026-11-01")
    assert kept == rows and briefs == {"f": ["1", "2"]} and left_out == {}


def test_a_feature_with_no_date_is_kept():
    rows = [{"_feature": "undated", "item_id": "1"}]
    assert audit.apply_window(rows, {"undated": ["1"]}, {}, "2026-11-01") == (
        rows, {"undated": ["1"]}, {})


def test_left_out_counts_rows_per_feature_and_a_brief_only_feature_counts_zero():
    rows = [{"_feature": "a", "item_id": "1"}, {"_feature": "a", "item_id": "2"}]
    _, briefs, left_out = audit.apply_window(
        rows, {"a": ["1", "2"], "b": ["1"]}, {"a": OLD, "b": OLD}, "2026-11-01")
    assert left_out == {"a": 2, "b": 0} and briefs == {}


def test_the_window_note_is_empty_when_nothing_was_left_out():
    assert audit.window_note({}) == []
    assert audit.window_note({"only-brief": 0}) != []  # witness: a left-out feature gives a note


def test_the_window_note_says_how_many_rows_and_lists_each_feature_sorted():
    assert audit.window_note({"zeta": 3, "alpha": 1, "mid": 0}) == [
        "4 reviewer rows older than the window were not checked",
        "  - alpha: 1 row(s)",
        "  - mid: 0 row(s)",
        "  - zeta: 3 row(s)",
    ]


def test_main_does_not_read_the_commits_of_a_feature_left_out(monkeypatch, tmp_path):
    run, commits = _window_main(monkeypatch, tmp_path, [
        _feature("old", OLD, ["1", "2"]), _feature("new", NEW, ["1"])], "2026-11-01")
    run()
    assert commits == ["sha-new"]


def test_main_reads_the_commits_of_every_feature_when_since_is_earlier(monkeypatch, tmp_path):
    run, commits = _window_main(monkeypatch, tmp_path, [
        _feature("old", OLD, ["1"]), _feature("new", NEW, ["1"])], "2000-01-01")
    run()
    assert sorted(commits) == ["sha-new", "sha-old"]


def _four_violations(name, at):
    """One feature that breaks B-004 (no actor), B-025/8 (quote not in the diff), I6 (open fatal), B-025/9."""
    return {"name": name, "at": at, "brief": ["1", "2", "3", "9"], "rows": [
        _wrow(name, "1", actor=""),
        _wrow(name, "2", quote="+not in the diff"),
        _wrow(name, "3", claim="coverage", verdict="fail", severity="fatal", status="open"),
    ]}


def test_main_applies_the_one_window_to_every_reviewer_check(monkeypatch, tmp_path, capsys):
    run, _ = _window_main(monkeypatch, tmp_path / "in", [_four_violations("f", NEW)], "2026-11-01")
    assert run() == 1
    inside = capsys.readouterr().out
    tags = {line.split("]")[0].split("[")[1] for line in inside.splitlines() if line.startswith("  - [")}
    assert tags == {"B-004", "B-025/8", "I6/B-025/10", "B-025/9"}  # witness: the data does break all four
    run, _ = _window_main(monkeypatch, tmp_path / "out", [_four_violations("f", OLD)], "2026-11-01")
    run()
    outside = capsys.readouterr().out
    assert "[B-004]" not in outside and "[B-025" not in outside and "[I6" not in outside


def test_main_keeps_the_verdicts_of_the_kept_features(monkeypatch, tmp_path, capsys):
    features = [_four_violations("kept", NEW), _four_violations("old", OLD)]
    features[1]["rows"] = [{**r, "item_id": "old-" + r["item_id"]} for r in features[1]["rows"]]
    features[1]["brief"] = ["old-1", "old-2", "old-3", "old-9"]
    run, _ = _window_main(monkeypatch, tmp_path / "full", features, "2000-01-01")
    run()
    full = capsys.readouterr().out.splitlines()
    run, _ = _window_main(monkeypatch, tmp_path / "cut", features, "2026-11-01")
    run()
    cut = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  - [")]
    expected = [line for line in full if line.startswith("  - [") and "old" not in line]
    assert len(expected) == 4 and len(full) > len(expected) + 1  # witness: old features add violations
    assert cut == expected


def test_main_prints_the_note_after_the_violations_and_before_the_unread_block_and_keeps_the_exit_code(
        monkeypatch, tmp_path, capsys):
    kept = _feature("kept", NEW, ["1", "2"])
    kept["rows"][0]["actor"] = ""
    kept["rows"][1]["commit"] = "unreadcommit"
    run, _ = _window_main(monkeypatch, tmp_path, [kept, _feature("old", OLD, ["1", "2"])],
                          "2026-11-01", unreadable={"unreadcommit"})
    assert run() == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "1 violation(s):"
    assert out[1] == "  - [B-004] report kept/1 has no actor"
    assert out[2:4] == ["2 reviewer rows older than the window were not checked", "  - old: 2 row(s)"]
    assert out[4].startswith("1 item(s) could not be read")


def test_main_clean_run_with_a_note_prints_the_note_then_ok_and_exits_0(monkeypatch, tmp_path, capsys):
    run, _ = _window_main(monkeypatch, tmp_path, [
        _feature("old", OLD, ["1", "2"]), _feature("new", NEW, ["1"])], "2026-11-01")
    assert run() == 0
    assert capsys.readouterr().out.splitlines() == [
        "2 reviewer rows older than the window were not checked",
        "  - old: 2 row(s)",
        "ok — all workflow invariants hold",
    ]


def test_main_without_a_window_effect_prints_today_s_output_and_no_note(monkeypatch, tmp_path, capsys):
    run, _ = _window_main(monkeypatch, tmp_path, [_feature("new", NEW, ["1"])], "2026-11-01")
    assert run() == 0
    assert capsys.readouterr().out == "ok — all workflow invariants hold\n"
