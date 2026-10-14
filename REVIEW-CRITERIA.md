# Review criteria family

> Part of the agentic workflow. Index and reading order: `WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

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
