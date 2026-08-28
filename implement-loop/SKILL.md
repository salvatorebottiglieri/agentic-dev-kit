---
name: implement-loop
description: |
  Process a batch of work items: implement each via a TDD subagent, review
  via a reviewer subagent, fix until clean, align docs, then create a PR.

  Use when the user says "implement these issues", "process this queue",
  "agentic loop", or passes a list of items to implement. User-invoked only.
disable-model-invocation: true
---

# Implementation Loop

YOU ARE A COORDINATOR. Your only tool is spawning subagents. They build;
you orchestrate. You hand off every item to a subagent; you review their
output; you loop until clean. At no point do you touch code yourself —
every change, every file edit, every test runs inside a subagent.

> **Agent type mapping**: This skill uses role names ("implement subagent", "reviewer subagent"). Map to your harness's concrete agent types — e.g. in OMP, use `task` for implement and `reviewer` for reviewer.

## 0. Setup

- Read the items from wherever the user's issue tracker lives (the user
  tells you where — GitHub Issues, GitLab, Linear, a local file, etc.).
- Identify the spec source per item (the item body IS the spec).
- Check for dependencies between items — order them so no item depends on
  unmerged work. Resolve dependencies sequentially; run independent items in
  parallel in separate git worktrees.

## 1. Spawn implementer

Spawn an implement subagent with a fresh context window (e.g. `task` in OMP). Its task must include:

- The full item body (title + description).
- **TDD instruction**: tell the subagent to use test-driven development
  (red-green-refactor, vertical slices, tests at public seams). If a `/tdd`
  skill is available, the subagent will follow it; if not, it relies on its
  own TDD knowledge — the model carries this natively.
- **Invariants-first** (when the item body contains a `## System Invariants`
  section, inherited from system-architecture): those invariants are **test
  contract**. The subagent writes their tests **first** — every invariant's
  negation as a failing test (red) — then implements until they pass
  (green). An invariant whose test cannot fail must be flagged back, not
  silently dropped (anti-vacuity: a check that never fails is worse than no
  check).
- **Execution order**: instruct the subagent to work **middle-out** — API
  contract first (define the endpoint signature with a stub handler), then
  the consumer (frontend, CLI, or caller), then the service layer, then the
  data layer — testing at each layer. Do **not** work stack-order (DB →
  services → API → frontend); middle-out keeps every layer independently
  testable and avoids dead-end schema designs.
- The relevant file paths and code context the subagent needs (current
  function signatures, API, existing test patterns).
- A clear list of files the subagent **may** modify vs **must not** touch.
- The acceptance criteria from the item.

**Always use a fresh context** so the implementer sees only the information you
explicitly include in the task string. Inheriting the parent session's context
causes drift — the subagent may hallucinate that changes are already applied
and skip making edits.

## 2. Spawn reviewers

When the implement subagent completes, spawn two `reviewer` subagents:

- **Standards review**: full diff against HEAD + code smell baseline (Fowler,
  Refactoring ch.3). Report per-file findings.
- **Spec review**: compare the diff against the item body verbatim — both
  the description and the acceptance criteria checklist. For each acceptance
  criterion (the `- [ ]` list), report whether it is correctly implemented
  (pass), incorrectly implemented (fail), or not implemented (missing).
  A criterion counts as "correctly implemented" only if the code demonstrably
  satisfies it — not if a test for it exists but the logic is wrong. Reference
  specific lines or test assertions for each claim. Also report any gaps,
  scope creep, or wrong implementations against the item description.
  When the item body has a `## System Invariants` section, additionally
  verify **invariant coverage**: every invariant has a test pinning its
  negation, and the test would fail on a violation (not vacuously green).

Both reviewers are read-only — they report findings, they do not edit code.

Reviews are **criteria families** — the taxonomy lives in
`~/agentic-workflow/WORKFLOW.md` → `## Review criteria family`; paste the
family into each reviewer's task (the reviewer has no other access to it).
Every finding must quote **evidence** (file + lines + verbatim excerpt from
the diff) and carry a **severity** (`fatal` blocks, `advisory` does not).
A finding without evidence is not accepted.

**Crucial**: pass the **full raw diff** (`git diff` or equivalent) in the
reviewer's task, verbatim. Do NOT summarise, paraphrase, or excerpt the diff —
a reviewer that receives only a summary may miss context and produce inaccurate
findings. If the diff is too large for a single task, split it per file and
spawn one reviewer per file, or truncate test files (test content is less
critical than source logic).

**Always use a fresh context** so each reviewer sees only the diff and
the review criteria you provide, not the entire chat history. A reviewer that
inherits the parent session may confuse its mandate with the implementer's or
the loop manager's.

## 3. Spawn fixer

If either review finds actionable issues:

1. **Spawn a fresh implement subagent** to fix them. Pass the review findings **verbatim** as task context.
2. In the task, tell the subagent *which files* to change and *what
   specifically* to fix (quote the findings). Include the current diff for
   context.
3. **Be explicit in the fix task**: tell the subagent to edit files and run
   tests. A fix subagent that inherits context may plan without acting —
   a fresh context prevents this by forcing you to put every instruction
   in the task string.
4. When the fix subagent completes, go back to **step 2** (re-review).
5. Exit the loop only when both reviews return **zero actionable findings**.

The only exception to full delegation: purely mechanical findings
(whitespace, typos, comments) you may fix yourself. Anything behavioural,
structural, or involving logic always goes through a subagent.

## 4. Align docs

When all items pass review and before creating the PR, check whether the
code changes need documentation updates:

1. **Identify affected docs**: grep the diff for changes that touch public
   APIs, CLI flags, config formats, environment variables, data schemas,
   architecture decisions (ADRs), or anything documented externally.
2. **Check project conventions**: look for `CONTEXT.md`, `README.md`,
   `docs/ARCHITECTURE.md`, `docs/adr/`, or any `*_docs/` directory that maps
   to the changed code.
3. **Delegate doc updates**: if the diff changes something documented, spawn
   an implement subagent to:
   - Read the relevant docs and the code diff.
   - Update docs to match the new behaviour.
   - Skip docs that are still accurate — no speculative rewrites.
4. **No news is good news**: if nothing documented changed, skip this phase
   entirely. The PR step proceeds directly.

## 5. UI verification (only when the item touches UI)

Before the PR, drive the built UI in a real browser (OMP: the `browser`
tool) through the **personas and edge cases from the phase-2 success
criteria** — at least one scenario per persona, plus the empty/error/
loading states. This is a usability pass, not a visual one: navigate,
submit, observe real state changes, check that nothing blocks the happy
path. Findings go through the implement/fix loop like review findings.
Skip when the item is API-only or the UI diff has no interaction change.

## 6. PR

When all items pass review, docs are aligned, and the UI pass is clean:

1. `git checkout -b <branch-name>` (descriptive, e.g. `feat-<issue-number>`)
2. `git add` the changed files (only what belongs to the task)
3. `git commit -m "..."` with a conventional commit message referencing the items
4. `git push origin <branch-name>`
5. Create a PR using the project's standard tooling (ask the user how).
6. Close/resolve each completed item in the issue tracker.

## Dependency resolution

- **Sequential**: if item B depends on item A's code, implement A → review A
  → fix A → align docs A → merge A (or at least commit A on a shared base)
  → then implement B.
- **Parallel**: if items are independent, run each through the full
  implement/review/fix/align-docs loop in its own git worktree. Merge them
  in dependency order.

## Completion criterion per item

The loop is done with an item when:
- All acceptance criteria from the item description are met
- All tests pass (existing + new)
- A reviewer (Standards + Spec) reports zero actionable findings
- Docs are aligned with the change (or confirmed unnecessary)
- UI items: the browser verification pass ran (personas × edge cases)
  with no open usability findings
- The item is referenced in a PR or commit
