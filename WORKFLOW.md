# Agentic Workflow — Operational Spec

> Canonical reference for the agentic workflow. Every session follows this.
> Edit this file to change session behavior — no system-prompt changes needed.

Skills are installed where your agent loads them (Claude Code: `~/.claude/skills/`)
and are read via `skill://<name>`. The
tracker is the one configured for the repo (GitHub, GitLab, Linear, or a
local tracker — `/setup-matt-pocock-skills` configures it).

## Pipeline

For any non-trivial feature, proceed through the phases in order. Each phase
is a skill; read it via `skill://<name>` before executing it.

### 1. Design & alignment

- `skill://grill-with-docs` — structured interview; updates CONTEXT.md and ADRs
- `skill://domain-modeling` — glossary when new terms emerge
- `skill://to-spec` — synthesize the conversation into a spec on the tracker
- **Falsify the premises before publishing.** A spec inherits the claims the
  conversation made about the current codebase — "the audit log is read-only",
  "this surface does not exist yet", "nothing enforces that rule". Write them
  down as falsifiable statements and dispatch a scout to refute them *before*
  the spec is published. A premise that dies changes the design tree, and it
  is far cheaper to lose it in phase 1 than to build on it in phase 6.

### 2. Product review (only when the feature touches UI)

`skill://product-review` — standalone HTML mockup at
`.scratch/product-review/<feature>/index.html` (problem statement + success
criteria + wireframe). Success criteria must cover every distinct persona in
the user stories (at least one criterion per persona) and the edge-case
states (empty, error, loading, long content, small viewport); the mockup
renders those states. Skip: API-only, UI bug fix, mechanical refactor,
follows existing pattern, pure data.

**Visibility rule:** the phase-2 decision is always stated in the session,
run or skip — "Phase 2 — product review: run" or "Phase 2 skipped —
<reason> (annotation on the ticket)". A skipped phase is never silent: the
user sees the decision in conversation even when the phase produces nothing.

### 3. System architecture (only when non-trivial)

`skill://system-architecture` — appends `## System Architecture` to the spec:
Mermaid sequence/state diagrams, endpoint contracts, data model, **and
`## System Invariants`** (laws of the domain in the campaign format —
law → negation → where verified; anti-vacuity: an invariant without a test
that can fail is an opinion). The invariants are test contract downstream:
implement-loop writes their tests first (red), review verifies coverage.
Skip: bug fix, trivial feature, data-only. Mechanical refactor and
follows-existing-pattern skip the diagrams/contracts but NOT the invariant
elicitation: if the touched module has domain laws (documented or new), the
`## System Invariants` section must still be written (possibly reduced —
law, negation, where verified); if there are none, the skip annotation must
state "no domain laws" explicitly. The criteria judge domain value, not
diff shape.

### 4. Tickets

`skill://to-tickets` — splits the spec into tracer-bullet tickets with
blocking edges (native tracker links, or one file per ticket locally).

### 5. Program design (near-always)

`skill://program-design` — appends `## Program Design` to the ticket:
call-stack tree, file-tree diff, type signatures (contract for
implementation). Skip only when there is no *shape* to design: one-shot
throwaway scripts, pure config/data changes that introduce zero new
signatures, mechanical rename/move with no logic change. Bug fixes and
"trivial" features get program design whenever they introduce or modify
functions or control flow.

### 6. Implement-loop

`skill://implement-loop` — batch execution:

- implement subagent with TDD, working middle-out (API contract → consumer
  → service layer → data layer)
- review as a criteria family: Standards + Spec axes (`skill://code-review`)
  plus the **ponytail axis** (`skill://ponytail-review`, the delete-list) —
  three orthogonal axes, never one vague "does this look right" pass (see
  `## Review criteria family`)
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

### 7. tightrope — folded into phase 6 (2026-08-30)

The tightrope tension check is no longer a separate optional pre-merge
gate. The ponytail-vs-engineering tension is the third review axis of
phase 6 (with user-weighted tension resolution), and its test/lint/PR gate
is implement-loop's pre-push gate (CI-before-merge). The
`skill://tightrope` skill is deprecated.

## Review criteria family

Every review and every pre-merge gate is a **family of small, orthogonal
criteria** — never a single vague judgement. A monolithic reviewer — one
prompt, several fuzzy questions, one boolean verdict — leaves too much room
for maneuver. Each criterion has:

1. **A defined scope** — one disjoint class of defects it is responsible
   for. Semantic overlap between criteria is resolved by assignment,
   written down in the families below — never by the reviewer.
2. **A falsifiable question per item** — each finding answers one
   question ("does this hunk violate a documented standard?") with a
   verdict, never a vibe.
3. **Pre-sliced evidence** — the code/repo supplies the candidate items
   (hunks, acceptance criteria, invariants); the reviewer gets a narrow
   brief, not the whole artifact.
4. **A structured, evidence-based finding** — verdict + evidence quote
   (what, where, the supporting excerpt). **No evidence = the finding is
   not accepted**, and the evidence is verified: the quote must match the
   file/diff verbatim. **Two species of evidence, and the claim decides
   which**: a claim *about the code* is carried by a verbatim quote; a
   claim *about test coverage* ("this is not pinned", "this test would
   catch a violation") is carried by an **execution** — the mutation and
   the suite's response — because a test that does not discriminate reads
   exactly like one that does (see criterion 7 and the Spec axis's
   invariant coverage).
5. **Severity** — every finding is `fatal` (blocks) or `advisory`
   (recorded, does not block). The gate keeps a pass-with-warning path:
   an all-or-nothing gate cries wolf and gets ignored.
6. **Dependency order** — criteria that consume another criterion's
   output run after it (Spec's invariant-coverage consumes the ticket's
   `## System Invariants`). The family is a DAG, not a flat fan-out;
   independent criteria run in parallel.
7. **Total coverage of the brief** — the report carries **one line per
   item of the pre-sliced brief** (every standard/ADR named, every
   acceptance criterion, every invariant), each with an explicit verdict,
   `pass` and `n/a — <reason>` included. **Silence is not a verdict**: in a
   free-form finding list, "conforms", "not applicable" and "never read"
   are the same output — which is how a reviewer drops an item that was in
   its own brief. The orchestrator then checks coverage **mechanically** —
   every brief item appears in the report, or the report is rejected and
   the reviewer re-spawned. This is counting, not judging: it is the only
   check a coordinator that does not read the code can make reliably.

The concrete families, **owned by this document** — criteria changes land
here first, then propagate to the skills, which reference this section
(the main agent pastes the family into reviewer tasks; reviewers have no
other access to it):

- **code-review — Standards axis**: the brief names the standards sources
  (repo standards files, the ADRs the ticket cites) item by item, and the
  report answers **every one of them** (criterion 7). (a) documented repo
  standards — cite standard file + rule → `fatal` on breach; (b) the Fowler smell baseline
  (12 smells, each a labelled judgement call; a documented repo standard
  overrides the baseline) → `advisory`; (c) the anti-AI-look baseline —
  UI diffs only: generic AI-generated design patterns (default
  purple/indigo gradients, centered glassmorphism cards, near-identical
  card rows, filler copy) → `advisory`; a documented design standard or
  per-project design tokens (e.g. `design.md`) overrides it. Skip anything
  tooling enforces.
- **code-review — Spec axis**: (a) acceptance criteria — per `- [ ]`
  item: pass / fail / missing; pass only if the code demonstrably
  satisfies it (a passing test with wrong logic is not a pass) →
  `fatal` on fail/missing; (b) scope creep — behaviour not asked for →
  `advisory`; (c) wrong implementation — implemented but incorrect →
  `fatal`; (d) invariant coverage — per law of `## System Invariants`, **one of
  three verdicts, never a binary**: `pinned` (a mutation of the
  implementing line was executed and the test went red — the mutation diff
  is the evidence), `blind-test` (a test exists and exercises the line, but
  the mutation stayed green: the test does not discriminate), `no-test` (no
  test touches the law). `blind-test` and `no-test` are both `fatal` and
  are **distinct findings** — collapsing them misreports the weight of the
  gap, and "no coverage" said of a blind test is a false claim about a real
  problem. The verdict is produced by the mutation prover, never by reading
  the diff. Dependency: (d) consumes the spec's invariants section and the
  prover's mutation matrix.
- **ponytail axis** — the delete-list (`skill://ponytail-review`):
  `delete` / `stdlib` / `native` / `yagni` / `shrink`, with a net
  lines-removable count. Every finding quotes the hunk verbatim →
  `advisory` (minimalism never blocks a correct feature alone). Scope:
  only "could be simpler/shorter"; correctness, security and performance
  are out of scope (routed to the engineering axes). A minimal smoke test
  or `assert` is the ponytail floor, never flagged for deletion.
- **mutation prover** — the executed half of anti-vacuity, and the only
  read-write role in the review stage. Scope: **only the lines implementing
  a declared law or a `fatal` acceptance criterion** — a handful of mutants
  per ticket, not whole-diff mutation testing. For each, it applies one
  plausible-wrong alternative (normalise instead of pass through, swap a
  sibling column, coalesce a default, flip a boundary), runs the targeted
  suite and records mutant → outcome. **Green mutant = the test is blind**
  → `fatal`, and the finding ships the mutation diff, so the claim is
  reproducible instead of trusted. It works in a throwaway copy (scratch
  worktree), never in the session's working tree: the three review axes
  stay read-only and the main agent still never edits code. Dependency:
  runs once after the three axes have closed, not on every fix-loop
  iteration.

- **tension resolution** — where the ponytail axis and the engineering
  axes (Standards + Spec) flag the same code in opposite directions
  (e.g. ponytail "remove this abstraction" vs engineering "this needs
  tests at a public boundary"). The user decides the weight: balanced
  (default — every real tension escalates to the human with both sides
  stated) or lean (`--lean ponytail` / `--lean engineering`, auto-resolving
  the tension in that direction). `fatal` engineering findings are
  escalated regardless of lean; ambiguous tensions are escalated, never
  guessed. Dependency: runs after all three axes and consumes their
  findings; ordered by severity before any decision.

New criteria enter the registry only for a real, observed defect class —
the family is curated, not grown by habit.

## Session isolation — one writer per working tree

A git working tree is per repo, and it is what two agentic sessions fight
over. Observed failure (2026-09-19, `a-data-pipeline`): a second
session could not change branch without moving the first session's working
tree, `git stash` — repo-global, shared by every worktree — was cycled
between the two, and a commit landed on the other session's feature branch
(`handoffs/handoff-<feature>-20260919.md`, deviations 1 and
4).

**At most one writer per working tree.** The default is a worktree per
session: a session's working tree is part of its launch context, decided
before it writes. The primary tree is available only to a session that has
checked that nobody else is there — and claimed it (below).

**Isolation is entered on evidence, not on assumption.** On entering a repo,
before the first write, the session reads three observable signals:

- the primary working tree is dirty with work the session did not produce;
- the repo has a worktree other than the primary tree;
- a **live claim** exists (below).

Any one of them → the repo is shared, and the session works in its own
worktree: `git worktree add ../<repo>-<feature> <base>`, a sibling directory
named after the session's `<feature>` slug (the same slug as its handoff),
never inside the repo (a nested worktree is untracked content in the
session's own diff). None of them → it may work in the primary tree, and it
then **claims** it.

**Claims.** Deciding to work in the primary tree is followed by writing
`<git-common-dir>/agentic-sessions/<feature>` — `git rev-parse
--git-common-dir` is the same directory for every worktree of the repo, so
every session sees it — holding the slug and the start time, and by removing
it at session end (part of the session-end gate, see General rules). Reason:
cleanliness is not exclusivity — a session that has committed and pushed is
clean while still working, so "clean, no other worktree" is not proof that
nobody else is there. A claim left behind by a session that died is treated
as live: a stale claim costs a worktree, a missed collision costs a commit.

**Cleanup at session end — the tree a session opens, it closes.** Worktrees
accumulate: observed 2026-09-21 on `a-shared-lib`, nine of them
(`handoff-<feature>-20260921.md`, deviation 3), every one a false
"the repo is shared" signal for every later session, which then isolates for
nothing. **Two species, and the rule covers both:**

- *workflow worktrees* — `../<repo>-<feature>`, created by this rule;
- *harness worktrees* — `<repo>/.claude/worktrees/agent-<hash>` on branches
  `worktree-agent-<hash>`, created by the agent harness, **nested inside the
  repo** in violation of the rule above and therefore never cleaned by it.
  These are the ones that pile up.

**Removal is on evidence, never on assumption**, in this order:

1. **The session's own worktree is always dealt with.** Tree clean *and*
   `git merge-base --is-ancestor HEAD <base>` → `git worktree remove` plus
   `git branch -D` of its branch. Otherwise it stays, and the handoff names it
   and why.
2. **Another session's worktree is removed only on all three:** no live claim
   for it, tree clean, and HEAD reachable from the base branch. Five of the
   nine of 2026-09-21 were in this case; the other four were not, which is
   what the next point is for.
3. **Dirty, or carrying a commit the base cannot reach → never silently
   dropped.** Archive first (`git format-patch` for the commits, `git diff` for
   the dirty tree), then remove, and the handoff says where the patch went and
   what the evidence for disposability was. "Its content shipped under another
   commit" is a claim to *verify* — grep the base branch for the change — not
   to assume from a rebase.
4. Finish with `git worktree prune`, and remove `.claude/worktrees/` when it is
   left empty — an empty directory still reads as a worktree parent to the next
   session scanning by path.

The asymmetry with the claim rule is deliberate and runs the other way: for
*entering*, a stale claim is treated as live (cheap to be wrong); for
*removing*, evidence is required on every one of the three conditions (a lost
commit is not cheap).

**Never `git stash` to coordinate with another session.** `refs/stash` is
shared by every worktree of a repo and invisible to the other session, so
your parked work reads to them as "residuo di una sessione precedente" — and
theirs to you. Park work by committing it to your own branch.

**Handoff.** The repo-state line names the working tree the session ran in —
primary tree (with its claim) or the worktree path — so the isolation is
auditable after the fact (I5). Deliberately not an `audit.py` check: no
tracker artifact records whether a session *should* have isolated, so a check
would be unfalsifiable (see `INVARIANTS.md`, "Not auditable").

## General rules

- Unsure which skill to use? Read `skill://ask-matt` (router).
- Skipped phases are annotated on the ticket/spec: "Skipped — <reason>".
- Custom skills (implement-loop, product-review, program-design,
  system-architecture) live in `~/agentic-dev-kit/skills/` (repo:
  github.com/salvatorebottiglieri/agentic-dev-kit); installed copies in your
  agent's skills dir (e.g. `~/.claude/skills/`). `tightrope` is deprecated
  (folded into phase 6).
- Reference docs: `~/agentic-dev-kit/INVARIANTS.md` (workflow invariants I1–I6),
  `~/agentic-dev-kit/INVARIANTS-METHOD.md` (theory and process),
  `~/agentic-dev-kit/RATIONALE.md` (the why of every load-bearing rule).
- **Rule changes carry a rationale**: any change to a load-bearing rule in
  this file adds or updates an entry in `~/agentic-dev-kit/RATIONALE.md`
  (same session, citing the rule location). A rule without a rationale
  entry is an orphan.
- **Every session ends with the transversal gate** — including exploration-only
  sessions: (1) run `python ~/agentic-dev-kit/audit.py --repo <owner/repo>`
  and report the outcome in the final message; (2) write and git-commit the
  handoff `~/agentic-dev-kit/handoffs/handoff-<feature>-<YYYYMMDD>.md` (I5);
  (3) release the session claim, if the session claimed a primary tree
  (`<git-common-dir>/agentic-sessions/<feature>` — see "Session isolation");
  (4) **clean up worktrees** per "Session isolation — Cleanup at session end":
  the session's own always, others only on evidence, anything dirty or
  unreachable archived and reported instead of dropped.
  The audit only sees tracker artifacts (issues, PRs, handoffs): work done
  outside the pipeline — local design docs, direct commits, exploration — is
  invisible to it; the handoff is what makes it visible and auditable.
- **"Session" means the whole conversation, not a turn.** The gate runs
  **once, at the end**, never mid-session: a handoff written after a single
  exchange documents a session that has not happened yet, and the next turn
  makes it wrong. While the work continues, the gate has not come due.

## Workflow invariants (transversal)

The workflow itself is subject to invariants, valid in **every** project:
`~/agentic-dev-kit/INVARIANTS.md` (I1–I6: CI-before-merge, signatures-as-
contract with documented deviations, skip annotations, PR links issue,
handoff per session in `~/agentic-dev-kit/handoffs/`, zero-findings exit).
Verified by `~/agentic-dev-kit/audit.py` — **mandatory
at the end of every session** (see General rules) and pre-merge in any repo:

```
python ~/agentic-dev-kit/audit.py --repo <owner/repo> [--since YYYY-MM-DD]
```

Every session ends with a handoff in `~/agentic-dev-kit/handoffs/`
(`handoff-<feature>-<YYYYMMDD>.md`), git-versioned, listing: the repo it
concerns (`- **Repo**: owner/name` — it is what lets the audit scope I9 to the
project), repo state, delivered work, verification, deviations (with where
they are documented), next session, ground truth. The audit's mutation tests
(`~/agentic-dev-kit/tests/test_audit.py`) guarantee every check can fail.
