---
name: implement-loop
description: |
  Process a batch of work items: implement each via a TDD subagent, review
  via three reviewer subagents (Standards + Spec + Ponytail, tensions
  resolved by the user's weight), prove the invariant coverage by executed
  mutation, fix until clean, align docs, pass the canonical test/lint gate,
  then create a PR held to CI green.

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
- Note the user's **weight** for tension resolution (applies to the whole
  run): `balanced` (default — every real tension escalates to the human) or
  `--lean ponytail` / `--lean engineering`.

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
- **Witness value — the fixture must discriminate.** Red-green proves the
  test fails against *nothing*; it does not prove it fails against the
  **plausible wrong implementation**. Require the subagent to pick, for
  every invariant, an input on which the correct implementation and the
  plausible wrong one **diverge**, and to state it in one line: "witness
  `<value>` — distinguishes `<correct>` from `<wrong>`". Banned: a fixture
  that is a **fixed point** of the transformation under suspicion —
  `"1.0.0"` for a version normaliser, an already-sorted list for a sort, a
  space-free string for a trim, `1` or `0` for a coefficient, disjoint
  dicts for a precedence rule, an id equal to its index, a single-element
  collection for anything about order or merging. A fixed-point fixture is
  green under every implementation, so it survives any reading of the diff
  — which is exactly why it must be caught here, where it costs one line.
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

When the implement subagent completes, spawn three `reviewer` subagents:

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
- **Ponytail review**: run `/ponytail-review` against the same diff. It
  returns a structured delete-list (`delete` / `stdlib` / `native` / `yagni`
  / `shrink`) with a net lines-removable count. This axis finds complexity
  to remove, not correctness — if the report says "Lean already. Ship.",
  the axis finds nothing.

All three reviewers are read-only — they report findings, they do not edit
code.

Reviews are **criteria families** — the taxonomy lives in
`~/agentic-dev-kit/WORKFLOW.md` → `## Review criteria family`; paste the
family into each reviewer's task (the reviewer has no other access to it).
Every finding must quote **evidence** (file + lines + verbatim excerpt from
the diff) and carry a **severity** (`fatal` blocks, `advisory` does not).
A finding without evidence is not accepted.

**Verify the reviewers like the code — brief coverage is mandatory.**
Enumerate each reviewer's brief as a numbered list of items *before*
spawning it (Standards: each standards file and each ADR the ticket cites;
Spec: each `- [ ]` acceptance criterion and each `## System Invariants`
law), and require **one line of answer per item**, with an explicit verdict
including `pass` and `n/a — <reason>`. Then **check the coverage
mechanically**: every numbered item must appear in the report, or the
report is rejected and the reviewer re-spawned with the missing items named.

Reason: a free-form finding list makes "conforms", "not applicable" and
"never read" indistinguishable, so a reviewer can silently drop an item
that was in its own brief and the omission looks like a clean bill of
health. Counting items is something you can do without reading the code;
judging findings is not.

**Tension resolution (after all three axes report).** Where the ponytail
axis and the engineering axes (Standards + Spec) flag the same code in
opposite directions — ponytail says "remove this abstraction", engineering
says "this needs tests at a public boundary" — you have a `tension`. Resolve
it by the user's chosen weight:

- **balanced (default)** — every real tension escalates to the human: present
  both sides verbatim (evidence + severity) and ask which way to land. Never
  guess.
- **`--lean ponytail`** — auto-resolve the tension by removing: apply the
  ponytail finding; note the engineering gap as advisory.
- **`--lean engineering`** — auto-resolve the tension by keeping: apply the
  engineering finding; note the ponytail finding as advisory.

`fatal` engineering findings are escalated regardless of lean. When both
axes flag the same code independently (not in conflict), each finding is
resolved on its own merit — lean only breaks ties between the axes on the
same concern.

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

## 2b. Prove the coverage (mutation prover)

Run **once, after all three axes have closed** — not on every fix-loop
iteration. Skip only when the item declares no invariants and has no
`fatal` acceptance criterion.

The three reviewers are read-only and you never edit code, so the proof
needs its own role: spawn a **prover subagent**, read-write but confined to
a **throwaway copy** — `git worktree add ../<repo>-mutprove-<slug> HEAD`,
a sibling directory, **never inside the repo** (a nested copy is untracked
content in the session's own diff — WORKFLOW.md, session isolation) and
never the session's working tree. Its task:

1. **The target list**, built by you from the Spec reviewer's report: for
   each declared law and each `fatal` acceptance criterion, the file and
   line(s) that implement it and the test(s) that claim to cover it.
2. **One plausible-wrong mutation per target** — the alternative a
   competent implementer could have written: normalise instead of passing
   through, read a sibling column, coalesce a default, flip a boundary
   (`<` ↔ `<=`), drop a guard, return the input unchanged.
3. **Run the targeted suite per mutant**, revert it, move to the next.
4. **Report the matrix** mutant → outcome, and for every mutant the diff
   that produced it.
5. **Destroy the copy** (`git worktree remove`) — the proof leaves no
   branch and no commit behind.

Reading the matrix:

- **red** → `pinned`. The coverage claim is proven.
- **green** → `blind-test`, `fatal`: a test exists, exercises the line, and
  cannot tell right from wrong. This is the defect class that survives
  every reading of the diff. The fix is a **new assertion or a new witness
  value**, not a new test file — and the mutant must go red afterwards.
- **no test to run** → `no-test`, `fatal`, and a *different* finding from
  `blind-test`. Do not collapse the two: "no coverage" said of a blind test
  is a false statement about a real problem, and it sends the fixer to
  write a test that already exists.

Findings from the matrix enter the fix loop like any other, and the mutant
is re-run after the fix.

## 3. Spawn fixer

If any review finds actionable issues (or an unresolved tension):

1. **Spawn a fresh implement subagent** to fix them. Pass the review findings **verbatim** as task context.
2. In the task, tell the subagent *which files* to change and *what
   specifically* to fix (quote the findings). Include the current diff for
   context.
3. **Be explicit in the fix task**: tell the subagent to edit files and run
   tests. A fix subagent that inherits context may plan without acting —
   a fresh context prevents this by forcing you to put every instruction
   in the task string.
4. When the fix subagent completes, go back to **step 2** (re-review), then
   **step 2b** for any target whose mutant was green.
5. Exit the loop only when all reviews return **zero actionable findings**,
   every mutant in the matrix is red, and every tension is resolved
   (escalated or resolved by weight).

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

## 6. Pre-push gate (test + lint)

Before the PR, run the **canonical** full-suite test and lint on the whole
repo — not just the subagent's tests:

1. **Test.** Determine the canonical command from the project's config
   (`pyproject.toml`, `tox.ini`, `setup.cfg`, `pytest.ini` for Python; the
   equivalent for other languages). Default scope: unit tests; the user can
   widen with `--test-scope integration` or `all`.
2. **Lint.** Determine the canonical linter from config (prefer `ruff` →
   `ruff.toml`/`pyproject.toml`, then `flake8`, then `pylint`) and run it.
3. **Red → fixer.** If test or lint fails, spawn a fresh fixer subagent with
   the failures verbatim, then re-review (step 2) and re-run the gate.
4. Only proceed to the PR when test and lint are both green.

## 7. PR (hold until CI green)

When all items pass review, docs are aligned, the UI pass is clean, and the
pre-push gate is green:

1. `git checkout -b <branch-name>` (descriptive, e.g. `feat-<issue-number>`)
2. `git add` the changed files (only what belongs to the task)
3. `git commit -m "..."` with a conventional commit message referencing the items
4. `git push origin <branch-name>`
5. Create a PR using the project's standard tooling (ask the user how).
6. **Wait for CI to be green** before declaring the item done; if CI goes
   red, spawn a fixer, re-push, and wait again. Do not merge yourself — hand
   the PR to the user for review and merge.
7. Close/resolve each completed item in the issue tracker.

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
- All tests pass (existing + new) and the canonical repo test + lint are green
- All three reviewers (Standards + Spec + Ponytail) report zero actionable
  findings, each having answered **every item of its brief** (coverage
  checked mechanically), and every tension is resolved (escalated or
  resolved by weight)
- Every declared invariant is `pinned` by an **executed** mutation (the
  matrix of step 2b is all-red); no `blind-test`, no `no-test`
- Docs are aligned with the change (or confirmed unnecessary)
- UI items: the browser verification pass ran (personas × edge cases)
  with no open usability findings
- The PR is open and CI is green (I1 — CI-before-merge)
