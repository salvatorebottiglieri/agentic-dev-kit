# Phase 6. Implement-loop

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://implement-loop` — batch execution:

- implement subagent with TDD, working middle-out (API contract → consumer
  → service layer → data layer)
- review as a criteria family: Standards + Spec axes (`skill://code-review`)
  plus the **ponytail axis** (`skill://ponytail-review`, the delete-list) —
  three orthogonal axes, never one vague "does this look right" pass (see
  `REVIEW-CRITERIA.md`)
- **the reviewers are verified like the code**: every reviewer answers its
  whole brief item by item (criterion 7) and the orchestrator checks that
  coverage mechanically before reading a single finding
- **mutation prover** after the three axes close: the coverage of every
  declared law is *executed*, not read — a mutant that stays green means
  the test is blind and the item goes back into the fix loop
- **tension resolution**: when the ponytail axis and the engineering axes
  (Standards + Spec) flag the same code in opposite directions, the user
  decides the weight — balanced (default: every real tension escalates to
  the human with both sides stated) or lean toward one axis (`--lean
  ponytail` / `--lean engineering`); the reviewer escalates ambiguous
  tensions rather than guessing
- fix loop until zero actionable findings and an all-red mutant matrix
- UI items: browser verification pass before PR — drive the built UI in a
  real browser through the personas and edge cases from the phase-2 success
  criteria, checking usability, not just appearance
- docs alignment, then the pre-push gate (canonical test + lint), then PR
  held open until CI is green

Rules: the main agent orchestrates and never edits code directly; fresh
context for every subagent; verbatim diffs to reviewers; the criteria
family is pasted into every reviewer task — reviewers have no other
access to it.

## 7. tightrope — folded into phase 6 (2026-08-30)

The tightrope tension check is no longer a separate optional pre-merge
gate. The ponytail-vs-engineering tension is the third review axis of
phase 6 (with user-weighted tension resolution), and its test/lint/PR gate
is implement-loop's pre-push gate (CI-before-merge). The
`skill://tightrope` skill is deprecated.
