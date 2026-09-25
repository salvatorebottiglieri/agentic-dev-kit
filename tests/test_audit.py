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
    check_ci_before_merge,
    check_configured_gates_run_in_ci,
    check_followups_tracked,
    check_handoffs_quote_commands,
    check_pr_links_issue,
    check_recent_handoff,
    check_skip_annotations,
    gather_prs,
)

# Real commit from Example/example-pipeline, where the fallback
# was needed (a fine-grained PAT without check-run access).
SHA = "0887aaea7ae21185fee524a59facc500e43465f3"

#: I9 is scoped per project: only handoffs declaring this repo are read.
REPO = "Example/example-pipeline"


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
    assert audit._handoff_repo("- **Repo**: Example/x\n") == "Example/x"
    assert audit._handoff_repo("Repo: owner/name\n") == "owner/name"
    assert audit._handoff_repo("**repo:** owner/name (audited)\n") == "owner/name"
    assert audit._handoff_repo("## Repo state\nExample/example-x\n") is None


def test_followups_tracked_ignores_handoffs_of_another_repo():
    """The third false-positive class (2026-09-19): the corpus of handoffs is
    shared by every project, the tracker is not. The example-service handoff
    citing its own #319 resolves at home and must not be judged here."""
    handoffs = [_handoff(
        "Closes #316 #317 #318 #319\n- Advisory: legacy note, already tracked\n",
        name="handoff-diario-mockup-fidelity-20260917.md",
        repo="Example/example-service",
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
                         repo="example/example-pipeline")]
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


