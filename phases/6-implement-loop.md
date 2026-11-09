# Phase 6. Implement-loop

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://implement-loop` — batch execution:

- implement subagent with TDD, working middle-out (API contract → consumer
  → service layer → data layer). The red must be the **right red**: before
  implementing, the test fails for the expected reason (an assertion about
  behavior, not an import/setup error), and the implementer's report cites the
  red test (`path::name`) and quotes the failure, then the green run. A test
  that has never been red is a claim, not a witness (B-013/D-045)
- review as a criteria family: Standards + Spec axes (`skill://code-review`)
  plus the **ponytail axis** (`skill://ponytail-review`, the delete-list) —
  three orthogonal axes, never one vague "does this look right" pass (see
  `REVIEW-CRITERIA.md`)
- the Spec axis also judges the design's `## Decision` section
  (`REVIEW-CRITERIA.md` (f), D-043): a load-bearing behaviour/cost/contract
  choice without an explicit, owner-signed decision is a `fatal` finding; the
  brief names the design file and this criterion
- the Spec axis also judges external fixtures (`REVIEW-CRITERIA.md` (g),
  B-022/D-044): a fixture standing in for a system the repo does not own needs
  a provenance row in the manifest named by the design; missing, or `synthetic`
  without a reason, is `fatal` when a contract test uses it, `advisory`
  otherwise. The brief names the manifest path and this criterion.
- **the reviewers are verified like the code**: every reviewer answers its
  whole brief item by item (criterion 7) and the orchestrator checks that
  coverage mechanically before reading a single finding
- **mutation prover** after the three axes close: the agent chooses, for each
  declared law or `fatal` criterion, the plausible-wrong implementation and
  writes it as a patch in `mutation-manifest.json`; `guards/mutation_prover.py`
  applies each patch in a clean worktree, sets `PYTHONPATH` on the worktree,
  runs the **control first** (green/no-test → matrix `inconclusive`) and then
  the targeted test. A mutant that stays green is `blind-test` — the test is
  blind — and the item goes back into the fix loop. The matrix is JSONL with
  each mutant's diff (B-026/D-046)
- **only the prover mutates** (B-007): the implementer and the fixer never
  apply a mutant, and their brief contains no mutation verb — a witness
  property is *declared*, not executed. A mutant in the session's worktree is
  a process violation, not evidence. After each fixer round the coordinator
  inspects `git status` and the key lines before committing or launching the
  prover
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
- **before the PR**, the coordinator writes `Risk: low|med|high`,
  `Blast radius:` (boundary files: DB, auth, migrations, public API, infra,
  deps) and `Where to look:` (critical points). The label is triage metadata,
  not a review criterion (B-001). For `Risk: med|high`, run the conditional
  blast-radius check: search outside the diff for wire format, version pins,
  deploy scripts and cross-repo contracts, and execute the fact the safety
  depends on; if it cannot be executed, state the residual and escalate
  (B-014)
- **final claims are labelled**: `measured` / `deduced` / `hypothesis` with the
  evidence in the same sentence (B-011)

Rules: the main agent orchestrates and never edits code directly; fresh
context for every subagent; verbatim diffs to reviewers; the criteria
family is pasted into every reviewer task — reviewers have no other
access to it.
