# Rationale — public decision register

Generated from the canonical workflow source by `sync_kit.py`.
Each entry keeps the decision and its rationale; the private session evidence stays in the canonical repository.

## D-001 — Phase 6: the main agent orchestrates and never edits code (2026-08-03)

- **Decision.** WORKFLOW.md phase 6: the main agent orchestrates; subagents
  implement with TDD and fresh context; reviewers see verbatim diffs and
  the criteria family (D-004). The main agent never edits code directly.
- **Rationale.** An orchestrator that edits code becomes part of the
  implementation: its edits bypass the TDD loop, arrive without fresh
  context, and the review sees them as already-settled. The separation is
  what keeps the criteria family (D-004) independent of the code it judges.
  It is a process rule, not an invariant — unverifiable from traces by
  design, which is why it lives here and not in INVARIANTS.md.

## D-002 — Every session ends with the transversal gate: audit + handoff (2026-08-05)

- **Decision.** WORKFLOW.md General rules: every session, including
  exploration-only ones, ends with (1) `python ~/agentic-dev-kit/audit.py
  --repo <owner/repo>` reported in the final message and (2) a git-committed
  handoff `handoff-<feature>-<YYYYMMDD>.md` in `~/agentic-dev-kit/handoffs/`
  (I5). The audit's mutation tests (`tests/test_audit.py`) guarantee every
  check can fail — I1–I6 are laws, not opinions.
- **Rationale.**
  1. **A session without a handoff is unauditable** — the audit only sees
     tracker artifacts; the handoff is the bridge that makes out-of-pipeline
     work visible. The corpus gap was never missing handoffs, it was
     handoffs outside version control: the fix is a canonical, git-versioned
     location.
  2. **The gate is mandatory, not best-effort** — "including exploration-
     only sessions" closes the escape hatch; a discretionary gate is the
     first thing dropped under pressure, which is exactly how the Desktop
     handoffs happened.
  3. **Audit + mutation tests keep the invariants honest** — an audit
     whose checks cannot fail is decoration; anti-vacuity is what makes the
     gate load-bearing rather than ritual.

## D-003 — Phase-3 skip criteria judge domain value, not diff shape (2026-08-06)

- **Decision.** Those two skip types still skip the diagrams and endpoint
  contracts, but the `## System Invariants` section survives the skip — in
  reduced form (law → negation → where verified) whenever the touched
  module has domain laws, documented or new; when there are none, the skip
  annotation must state "no domain laws" explicitly.
- **Rationale.** The invariant section is the only phase-3 output with
  permanent value: it is the test contract downstream (implement-loop
  writes those tests first, the Spec review verifies coverage). A
  mechanical refactor of a module with laws is precisely where regression
  tests matter — the skip was protecting against bureaucracy and throwing
  away the one artifact that pays for the phase. The example campaign showed
  bugs live at boundaries and in triplicated logic; refactors touch exactly
  those.

## D-004 — Reviews and gates are families of small orthogonal criteria (2026-08-16)

- **Decision.** Phases 6–7 and the review skills (code-review,
  implement-loop, tightrope) run as criteria families: defined scope,
  falsifiable per-item question, pre-sliced evidence, structured finding
  with **verified** evidence (quote must match the diff verbatim), severity
  (`fatal` / `advisory`, pass-with-warning kept), dependency order (DAG).
  Taxonomy is owned by `WORKFLOW.md → ## Review criteria family`; the skills
  reference it. Criteria are codified, not invented — no new criteria were
  added beyond what already existed.

## D-005 — Phase-2 (product-review) decisions are pronounced in session (2026-08-16)

- **Decision.** WORKFLOW.md phase 2 carries a visibility rule: the phase-2
  decision is always stated in the session — "Phase 2 — product review:
  run" or "Phase 2 skipped — <reason> (annotation on the ticket)". A skipped
  phase is never silent.
- **Rationale.** A decision the user never sees is a decision they can't
  trust or challenge. The phase was working correctly (skip criteria were
  applied, I3 annotations written); the failure was visibility, not logic —
  so the fix is a spoken line, not a process change.

## D-006 — Pocock skills refreshed from upstream; local edits re-applied (2026-08-16)

- **Decision.** (a) Update the 18 Pocock skills to upstream HEAD; (b)
  re-apply the 5 criteria-family edits onto the new code-review base
  (verified: installed-vs-upstream diff is exactly those edits); (c) install
  the 5 routed-but-missing skills — `research`, `wizard`,
  `resolving-merge-conflicts`, `to-questionnaire`, `wait-what`; (d) skip
  `implement` (custom `/implement-loop` covers it), `writing-for-agents`
  (installed `writing-great-skills` covers it), and the in-progress/misc
  skills (experimental or off-stack).
- **Rationale.** Staying behind upstream accumulates drift silently; updating
  is cheaper than diffing later. The code-review taxonomy survives any
  re-setup because it lives in WORKFLOW.md (D-004), not in the third-party
  skill. No registry creep: the two skipped installs would duplicate roles
  already filled. Pre-update backup:
  `~/Desktop/skills-backup-20260816.tar.gz`.

## D-007 — Anti-AI-look baseline in the review family (2026-08-28)

- **Decision.** WORKFLOW.md `## Review criteria family` — code-review
  Standards axis gains (c) anti-AI-look baseline: UI diffs only, advisory
  severity, overridden by a documented design standard or per-project
  design tokens (e.g. `design.md`). product-review Section C gains the
  same guardrail as a mockup rule: derive palette/layout from the project
  brand or a reference design system, state which.
- **Rationale.** The family is curated (D-004: "new criteria enter only for
  a real, observed defect class"), and this class qualifies: it is
  observable, UI-specific, and cheap to check. Advisory severity keeps the
  pass-with-warning path — an anti-AI-look verdict should never block a
  correct feature. The override rule matters: a repo with explicit design
  tokens already answers the question the baseline asks.

## D-008 — Persona × edge-case simulation in UI phases (2026-08-28)

- **Decision.** (a) WORKFLOW.md phase 2: product-review success criteria
  must cover every distinct persona (≥1 checkable criterion each) and the
  edge-case states; the mockup renders those states. (b) WORKFLOW.md phase
  6 + implement-loop: new step 5 "UI verification" — before the PR, drive
  the built UI in a real browser through those personas and edge cases,
  checking usability (navigate, submit, observe state), findings through
  the fix loop; completion criterion updated.
- **Rationale.** Criteria that cannot fail are decoration — persona and
  edge-case criteria are the ones that fail in practice, and the mockup
  already renders the states (product-review §C) but nothing forced the
  criteria to cover them. The browser pass is the evidence-first culture
  applied to UI: a visual confirmation is not proof of usability, an
  exercised persona scenario is. It reuses the existing fix loop, so it
  adds no new machinery.

## D-009 — Program design runs near-always (2026-08-30)

- **Decision.** WORKFLOW.md phase 5 + program-design SKILL.md §3: skip only
  when there is no *shape* to design — one-shot throwaway scripts, pure
  config/data changes that introduce zero new signatures, mechanical
  rename/move with no logic change. Bug fixes and "trivial" features get
  program design whenever they introduce or modify functions or control
  flow.
- **Rationale.** The phase's value is fixing the shape (names, parameters,
  file boundaries) before code is written; that value exists for bug fixes
  and small features too whenever they touch control flow or signatures.
  The old skip list keyed on the *kind* of change (bug fix, trivial) rather
  than on whether there is shape to design — a two-line logic tweak inside
  one function has no shape, but a "trivial" feature that adds a route and
  a type does. Keying on shape, not kind, makes the phase near-always
  without making it bureaucratic.

## D-010 — Tightrope folded into the review: ponytail axis + tension resolution (2026-08-30)

- **Decision.** WORKFLOW.md phase 7 removed; phase 6 review gains the
  **ponytail axis** (delete-list via `/ponytail-review`) as a third
  orthogonal axis alongside Standards + Spec, plus a **tension-resolution**
  criterion: balanced by default (every real tension escalates to the human
  with both sides stated), or lean (`--lean ponytail` / `--lean engineering`)
  auto-resolving the tension in that direction; `fatal` engineering findings
  escalate regardless of lean; ambiguous tensions are escalated, never
  guessed. The `tightrope` skill is deprecated.
- **Rationale.**
  1. **An optional final gate is the first thing dropped under pressure** —
     the same failure mode as the Desktop handoffs (D-002) and the silent
     phase-2 skip (D-005). Folding the tension into the mandatory review
     makes it fire every time.
  2. **The two axes genuinely conflict on the same code**; resolving a
     tension is a product/engineering-judgment call that belongs to the
     human, not the reviewer. The reviewer's job is to surface the tension
     with both sides and verbatim evidence, and escalate when ambiguous.
  3. **Weighting keeps the pass-with-warning path** — lean auto-resolves,
     balanced escalates, so the human controls the default instead of
     inheriting an all-or-nothing gate.

## D-011 — Pre-push gate (test/lint/CI) moves into implement-loop (2026-08-30)

- **Decision.** implement-loop gains step 6 (pre-push gate): canonical
  full-suite test + lint; red → fixer subagent → re-review → re-run the gate.
  Step 7 (PR) now blocks on CI green before the item is declared done.
  Completes B-002. I1 (CI-before-merge) is unchanged — the gate adds faster
  local feedback, not a new invariant.
- **Rationale.** The subagent's own tests are not the repo's tests — a change
  can pass its local red-green loop and still break the suite; tightrope was
  the only place that ran the canonical command. Moving it into
  implement-loop (mandatory, not optional) keeps the capability while
  removing the optional-gate failure mode. Blocking on CI green closes the
  loop — the gate must be mandatory, not best-effort (D-002).

## D-012 — "Session" means the whole conversation: the gate runs at the end (2026-09-11)

- **Decision.** WORKFLOW.md General rules states explicitly that a session is
  the whole conversation, not a turn: the gate runs once, at the end, and never
  mid-session.
- **Rationale.** The failure is not the wasted seconds of an early audit — it is
  a *wrong artifact in the audited corpus*, which is worse than a missing one
  because the audit trusts it. The rule was already correct; what was missing
  was the statement that a turn is not a session, because a long working
  conversation contains many natural stopping points that look like endings.
  The user's correction was the signal: the rule was being read literally
  against its intent.

## D-013 — Phase 1 falsifies the spec's premises before publishing (2026-09-11)

- **Decision.** Phase 1 has an explicit closing step: state the spec's claims
  about the current codebase as falsifiable statements and dispatch a scout to
  refute them *before* the spec is published.
- **Rationale.** A premise inherited from a conversation or an external document
  is an unverified claim wearing the clothes of context, and it is the cheapest
  thing in the pipeline to check and the most expensive thing to get wrong.
  Cost of refutation in phase 1: one scout. Cost of discovery in phase 6: the
  design plus everything built on it. Where the premise asserts an *absence*
  ("nothing enforces this", "this surface does not exist"), the refutation is a
  search — cheap, decisive, and exactly what was skipped here.

## D-014 — Three new invariants: gates in CI, follow-ups tracked, commands quoted (2026-09-11)

- **Decision.** Three invariants with checks and mutation tests: **I8** (every
  gate a repo configures is executed by CI), **I9** (a follow-up named in a
  handoff carries a resolving tracker reference, windowed to the recent
  handoffs), **I10** (a handoff quotes the command behind a result it reports —
  declared weak).
- **Rationale.** (a) **I8** exists because I1 cannot fail for the reason it
  exists; the vacuity was observed, not theorised. It reads the *declaration* of
  a gate — the stable, cheap part — and deliberately does not judge the gate's
  output. (b) **I9** restores I7's discipline as a rule rather than a habit. I7
  was removed for cross-project lookup noise; windowing to the recent handoffs
  and resolving against the repo already under audit removes that cause. Its
  first version resolved against issues only and produced a false violation on a
  PR reference — recorded here because that is the class of error this invariant
  will keep making. (c) **I10 is deliberately weak and says so in its own
  clause**: it can prove that no command was offered, not that the offered
  command produced the reported result. A stronger version would have to parse
  outcomes and would be unfalsifiable — the weak version fails on exactly the
  observed failure mode, which is the test it has to pass.

## D-015 — I9 reads *named* follow-ups, not words: label or follow-up section (2026-09-17)

- **Decision.** "Named" becomes two shapes, neither of which is prose: (a) a
  marker labels the line — optional bullet, number, `>` or bold, then
  `advisory` / `follow-up` / `non bloccante` — anywhere in the document; (b) any
  marker appears inside a section whose heading declares the pending items
  (`## Next session`, `## Follow-up`, `## Advisory`, …), where the unambiguous
  words count anywhere in the heading and `advisory` counts only as its first
  word. `audit.py` `ANCHORED_MARKER_RE` + `FOLLOWUP_SECTION_RE` +
  `HEADING_RE`.
- **Rationale.** The invariant's own word is *named*, and a name is a label.
  The first attempt — anchoring only the ambiguous marker — was published and
  immediately falsified by the second false positive, which is why the rule is
  stated on the *line's role* rather than on the word's ambiguity: an item in
  a "Delivered work" stanza is a report; the same sentence under "Next session"
  is a pending item, and that is a difference the check can see without
  reading semantics. The section half also *strengthens* I9 where it matters:
  inside `## Next session` a pending item can no longer hide behind an
  unlabelled sentence, which the old anywhere-match did catch but only by
  accident. Verified as a set operation over the 72-handoff corpus: 66 flagged
  lines before, 25 after, **0 introduced**, 41 removed, **0 of them inside a
  follow-up section** (checked explicitly — a removal there would have been a
  real pending item losing its check). Non-vacuity measured, not assumed: 9 of
  the 25 current violations come from a labelled line and 16 from inside a
  section, so both halves fire on the real corpus.

## D-016 — I9 is scoped to the project: the handoff declares its repo (2026-09-19)

- **Decision.** A handoff **declares the repo it concerns** — a `Repo:` line,
  one slug, canonical form `- **Repo**: owner/name` (WORKFLOW.md) — and I9
  reads only the handoffs declaring the audited repo (comparison
  case-insensitive, GitHub slugs being case-insensitive). A handoff declaring
  none is skipped: *cannot verify*, not a violation.
- **Rationale.** The check's subject is *a follow-up a handoff declares*, and
  a follow-up belongs to a project: a ref that resolves at home is not
  untracked. Skipping — rather than flagging — the missing field is forced by
  the same measurement that motivates the field: every handoff in the corpus
  predates it, so making it mandatory *now* would report other projects'
  handoffs, recreating the false positive one level up. Measured before
  choosing: over the 7-day window, 5 handoffs, 1 of another project, **1
  violation before → 0 after**; over the 73-handoff corpus, **22 flagged lines
  under the global scope → 0 under the per-repo scope**. The accepted cost,
  stated because it is a cost: 0 of the 73 declare a repo, so coverage over
  the existing corpus is zero and the check binds from now on — I5/I9's own
  windowing argument. (One of the 73 declares a repo: this session's handoff,
  written after the field was introduced.) Two alternatives were rejected on the same measurement:
  inferring the project from the handoff's prose (only 1 of the 5 window
  handoffs cites a repo URL, so a heuristic would be silently wrong on the
  other 4) and from the filename prefix (`handoff-<feature>-…` has no defined
  mapping to a repo — the Example handoff carries the org name, not the
  repo name).

## D-017 — Session isolation: one writer per working tree (2026-09-20)

- **Decision.** At most one session writes per working tree. A session
  entering a repo reads three observable signals — dirty primary tree with
  work it did not produce, another worktree of the repo, a live claim — and
  on any of them isolates in its own worktree (`git worktree add
  ../<repo>-<feature> <base>`); working in the primary tree is allowed only
  after **claiming** it with a marker under the git common dir, removed at
  session end. `git stash` is never a cross-session coordination mechanism.
- **Rationale.** The isolation is structural, not a convention: git refuses
  to check out a branch already checked out elsewhere, so the failure the
  2026-09-19 handoff describes in prose ("non era possibile cambiare branch
  senza spostare il working tree della sessione parallela") stops being
  possible rather than discouraged — and the stash cycling stops with it,
  since neither session has a reason to park work in a shared `refs/stash`.
  The claim closes the one gap a cleanliness test leaves open: a session that
  has committed and pushed is *clean* while still working, so "clean and no
  other worktree" is not evidence of exclusivity. Rejected alternatives, each
  on the evidence above: (a) leave the rule informal (status quo) — an
  unverifiable rule with no rationale is precisely what the implement-loop
  line already was, and it was ignored for its whole life; (b) an `audit.py`
  check or a new invariant — no tracker artifact records whether a session
  *should* have isolated, so the check would be unfalsifiable, the failure
  mode `INVARIANTS.md` anti-vacuity exists to prevent (recorded in its "Not
  auditable" table instead); (c) subagent-level isolation (`worktree: true`
  per child) — it separates writers *inside* a session, not sessions from
  each other, and would not have prevented either half of the 2026-09-19
  failure; (d) unconditional isolation with no exception — sound, rejected as
  the default only because it charges every single-session run a directory
  and a launch-context change for a collision that only concurrent sessions
  can produce; the claim buys that exception without an unsound
  "is another session live?" heuristic. Cost, stated because it is one: a
  session that dies before removing its claim makes the next session isolate
  needlessly — accepted, and asymmetric on purpose, a worktree against a lost
  commit.

## D-018 — Worktrees are cleaned at session end, on evidence (2026-09-21)

- **Decision.** Step (4) of the session-end gate. The session's own worktree
  is always dealt with — removed when the tree is clean and `HEAD` is an
  ancestor of the base, otherwise left and named in the handoff. Another
  session's worktree is removed only on **all three**: no live claim, clean
  tree, HEAD reachable from base. Anything dirty or carrying an unreachable
  commit is archived as a patch first and reported, never silently dropped.
  Finish with `git worktree prune` and remove `.claude/worktrees/` if empty.
- **Rationale.** The three-condition test is D-017's entry test read backwards,
  and the asymmetry between them is the whole point: **entering**, a stale
  claim is treated as live because being wrong costs one directory;
  **removing**, every condition must be positively evidenced because being
  wrong costs a commit. Rejected alternatives: (a) sweep every clean worktree
  regardless of owner — it contradicts D-017's own load-bearing sentence,
  "cleanliness is not exclusivity": a parallel session that has committed and
  pushed is clean *while still working*, and the sweep would pull the tree out
  from under it; (b) report-only in the handoff — zero risk and zero effect,
  since the nine of 2026-09-21 would have accumulated identically, each
  session dutifully listing what the previous one also listed; (c) an
  `audit.py` check — same unfalsifiability as D-017 (recorded in
  `INVARIANTS.md` "Not auditable"): no tracker artifact records which
  worktrees existed, so a check could only assert that a handoff mentions the
  word. The archive-before-remove step earns its cost from the same session:
  "the content shipped under another commit after a rebase" was *plausible*
  for `b04d4bc` and turned out true only because it was checked against
  `develop` — the rule therefore says verify, not assume, and keeps the patch
  either way.

## D-019 — Invariant coverage is proven by an executed mutation, not by reading the diff (2026-09-24)

- **Decision.** Two rules, one at each end of phase 6. (1) **Witness value**
  (implement-loop step 1): for every invariant the implementer states the
  input on which the correct and the plausible-wrong implementation
  *diverge*, and a fixture that is a **fixed point** of the transformation
  under suspicion is banned (`"1.0.0"` for a normaliser, an already-sorted
  list for a sort, `1`/`0` for a coefficient, disjoint dicts for a
  precedence rule, …). (2) **Mutation prover** (implement-loop step 2b, and
  a member of the review criteria family): after the three axes close, a
  read-write subagent confined to a throwaway worktree mutates the line
  implementing each declared law, runs the targeted suite, and reports
  mutant → outcome with the mutation diff. Green mutant = `blind-test`,
  `fatal`. The Spec axis's invariant coverage becomes a three-state verdict
  — `pinned` / `blind-test` / `no-test` — consumed from that matrix.
- **Rationale.**
  1. **Anti-vacuity was already the rule; only its verification was weak.**
     WORKFLOW.md phase 3 already says "an invariant without a test that can
     fail is an opinion". "Can fail" was judged by *reading*, and reading is
     precisely the instrument this defect class defeats. The same workflow
     already applies the executed form to itself — `tests/test_audit.py`
     mutates every `audit.py` check to prove it can fail (D-002) — so the
     move is to extend a proven local practice, not to import a new one.
  2. **Red-green does not cover it.** TDD's red proves the test fails
     against *nothing*; the blind test's red is real, and it is still blind
     against the wrong implementation. The witness value is what turns the
     red into a discriminating one, which is why the cheap half of the rule
     sits with the implementer.
  3. **Why a fourth role rather than letting a reviewer mutate.** The three
     axes are read-only and the main agent never edits code (D-001) — the
     independence of the criteria family rests on it. A prover confined to
     a throwaway copy buys the execution without spending that separation.
  4. **Cost is bounded by scope, deliberately.** Only lines implementing a
     declared law or a `fatal` acceptance criterion; a handful of mutants
     per ticket, not whole-diff mutation testing; run once after the axes
     close, not per fix-loop iteration. Rejected alternatives: (a) a
     coverage tool — line coverage was never the question, the line *was*
     covered; (b) full mutation-testing frameworks (`mutmut`, `cosmic-ray`)
     — minutes-to-hours per run and a mutant list dominated by noise, where
     the targets here are already enumerated by the spec's invariants.

## D-020 — A reviewer answers every item of its brief; the orchestrator checks coverage mechanically (2026-09-24)

- **Decision.** Criterion 7 of the review criteria family: the report
  carries **one line per item of the pre-sliced brief** (each standards file
  and ADR named, each `- [ ]` acceptance criterion, each invariant), with an
  explicit verdict, `pass` and `n/a — <reason>` included. The orchestrator
  enumerates the brief before spawning and then verifies coverage
  **mechanically** — a missing item means the report is rejected and the
  reviewer re-spawned with the missing items named. Paired with D-019's
  three-state verdict, which is what stops a `blind-test` from being
  reported as `no-test`.
- **Rationale.**
  1. **Silence is not a verdict.** In a free-form finding list "conforms",
     "not applicable" and "never read" produce the same output — nothing —
     so a dropped brief item reads as a clean bill of health. Forcing a line
     per item converts an undetectable omission into a detectable absence.
  2. **The check must be one the coordinator can actually make.** The main
     agent does not read the code (D-001), so it cannot second-guess a
     finding — but it *can* count items against the list it wrote itself.
     Criterion 3 of the family already requires the brief to be pre-sliced;
     this is the missing return half of the same contract.
  3. **Rejected alternatives.** (a) A second reviewer on the same axis —
     doubles the cost and correlates the errors, since it fails the same way
     on the same blind spot; (b) trusting a higher-effort model — the
     failure was an omission under a long brief, not a capability limit;
     (c) an `audit.py` check — reviewer reports are not tracker artifacts,
     so it would be unfalsifiable (`INVARIANTS.md`, "Not auditable").

## D-021 — Skills live in one agent-neutral store, not one copy per agent (2026-10-11)

- **Decision.** `~/.agents/skills/` is the single, agent-neutral store. The
  custom skills are installed there from a clone of `agentic-dev-kit`
  (`AGENT_SKILLS_DIR=~/.agents/skills ~/agentic-dev-kit/install.sh`, symlinks
  to the clone — the clone stays the one source). An agent that reads its own
  directory is pointed into the store by a symlink (`~/.claude/skills/<name>`
  → `../../.agents/skills/<name>`), never installed a second time.
- **Rationale.** One store makes "which version is the agent actually
  reading" answerable with one command; with a copy per agent it is not, and
  staleness stays invisible until a rule silently differs from the doc — the
  failure observed here. It is also the agent-agnostic constraint applied to
  installation: naming one vendor's directory as canonical turns a process
  document into a Claude Code document, and every other agent then gets a
  copy that drifts. Correcting the path in the doc is not enough on its own:
  a pointer is only true if the install target it names is the one populated.

## D-022 — Facts the review needs are computed by a tool into the commit, not reported by the agent (2026-10-13)

- **Decision.** A git pre-commit hook (`guards/gate.py`, installed with
  `python3 guards/gate.py install <repo>`) computes the facts — first the
  skipped tests, read from the JUnit report of the configured test command —
  and stages them into the same commit as `.workflow/gate-report.md`. It
  blocks the commit only when it cannot run — the causes are listed by the
  gate's own refusals; inconclusive counts as fail.
- **Rationale.**
  1. **A fact computed by a tool cannot be misreported.** The review reads
     the report from the commit; the agent neither writes nor summarises it.
  2. **Why a git hook.** It is agent-independent — any agent, or a human,
     committing in the repository runs it — and managed agent settings may
     disable agent-specific hooks, while a repo-local `core.hooksPath` is
     plain git.
  3. **Why block only when it cannot run.** Failing tests are the canonical
     test gate's job; a gate that silently produced no report would read
     like a clean one.
  4. **Known bypasses** (`--no-verify`, changing `core.hooksPath`, editing
     the gate) are left to a later after-the-fact check; deselected tests are
     not listed, since JUnit does not contain them and console output is not
     parsed.

## D-023 — Phase 5's design is a verifiable file, and its exits are the test list (2026-10-14)

- **Decision.** The design is `docs/design/<feature>.md` **on the branch**:
  prose plus a fenced `json design` block with the call stack, file tree,
  signatures, data flow, and one contract per call with its exits.
  `guards/check_design.py` (promoted from the
  `2026-10-interactive-program-design` prototype) checks coherence, signatures
  against the real code, state, exits (all four kinds
  `returns`/`absent`/`raises`/`effect` present, every dispatchable exit with a
  `handled_at`) and the frontier (a contract calling outside the repo declares
  its `residual`). At the end of phase 5 it runs in **full** mode (state
  included) and must exit 0 before publishing; at pre-push and in CI it runs
  `--gate` (signatures + coherence + exits + frontier, **not** the state).
- **Rationale.**
  1. **"Only the happy path" becomes structure, not intention**: omitting a
     `raises` is not an invisible oversight, it is a non-zero exit.
  2. **The design's state has a window**: "new / modified / already present" is
     true only while the code does not exist; at pre-push it would be stale by
     construction, and a gate that always fails is one people learn to bypass.
     That is why `--gate` does not check it.
  3. **The frontier is declared, not guessed**: the set of exceptions a library
     raises is not readable with `ast`; the check verifies the declared
     residual, it does not pretend to know the library.
  4. **The exits are the test list**: the implementer receives the contracts,
     not the checker; the prover can turn every exit into a mutant.

## D-024 — Every load-bearing rule declares its enforcement level; staying at `prompt` requires justification and an escalation trigger (2026-10-15)

- **Decision.** Every load-bearing rule **declares** its enforcement level
  (`structure` / `script` / `prompt`) in `WORKFLOW.md`'s
  "Where each rule is enforced" table. **Staying at `prompt` is allowed only
  when justified**: the rule carries a RATIONALE.md entry (or a row in
  `RATIONALE.md`'s "Not a rationale yet" with an owner and a deadline) and
  an **escalation trigger** — a falsifiable condition under which the rule
  becomes `script` (e.g. "two reviewer failures of the same form in the
  same project → write the check"). The level is not promised by the main
  1 if any rule has no level, and the check itself is falsified by
  `tests/test_gen_flow.py::test_livello_di_una_regola_rinominato_in_irriconoscibile`.
  The first row of the table is the caposaldo itself, so a reader sees the
  meta-rule before the rules.
- **Rationale.**
  1. **The table was dead until it was checked.** Before the fix, the
     "Enforced by" column was aspirational — a human-readable gloss, not
     column via `level_of`, which returns `""` if no level is recognized)
     but it converts the question "is this rule enforced by code?" into a
     command-line question with a non-zero exit on failure. This is the
     same anti-vacuity pattern `audit.py` + `tests/test_audit.py` apply to
     the workflow invariants (D-002); D-024 extends it to the rule
     registry itself.
  2. **An escalation trigger beats a one-shot "make it script".** A rule
     promoted to `script` before a real failure is observed is
     over-engineering (ponytail); a rule left at `prompt` indefinitely
     is the bug class this decision addresses. The trigger — phrased as a
     condition on observed failures, not a deadline — sits in the middle:
     the rule stays cheap until evidence arrives, and the rule becomes
     cheap *to escape* once evidence arrives. B-025 is the per-rule
     application of the trigger to the 15 currently-`prompt` rules.
  3. **Anti-vacuity is the load-bearing part.** The mutation test added
     here reproduces the 2026-10-11 defect: "rinominare l'heading faceva
     sparire le regole e `--check` usciva comunque 0 con 'copertura
     completa'". A check that never fails on violating input is decoration;
     the test makes the caposaldo falsifiable, which is what makes it
     load-bearing.

## D-026 — Reviewer evidence quotes are verified verbatim against the commit diff, not trusted (2026-10-15)

- **Decision.** The verbatim rule is enforced by
  `audit.py check_reviewer_evidence_verbatim` (B-025 voce 8): for every
  finding with `claim=code`, the `quote` field must appear byte-for-byte
  in the diff of `commit`; missing `quote`/`commit`, or `commit` not in
  the gathered `pr_diffs`, or `quote` not in the diff, are all
  violations. Coverage claims (`claim=coverage`, e.g. criterion 4 (b)) are
  skipped — their proof is the mutation matrix, not a quoted diff. The
  check has its own mutation tests in `tests/test_audit.py` (compliant,
  quote-not-in-diff, missing-quote, commit-not-in-pr-diffs,
  coverage-claim-skipped) — anti-vacuity.
- **Rationale.** The same anti-vacuity pattern as D-002 (audit mutation
  tests): a check that *could not* fail on a hallucinated quote is
  decoration. The check's input shape (`reports: list[dict]`,
  `pr_diffs: dict[str, str]`) is deliberately minimal — it does not depend
  on the tracker, on `git`, or on the orchestrator: anyone with the JSONL
  reports and the diffs can run it. Coverage claims are exempt because
  they carry their own proof (the matrix); quoting the diff would be
  cargo-culting, and the check is *not* the place to enforce how a coverage
  claim looks — that's the mutation prover's contract.

## D-027 — Brief coverage is counted, not read; every item needs a row (2026-10-15)

- **Decision.** Brief coverage is verified by
  `audit.py check_brief_coverage` (B-025 voce 9): for every `item_id` in
  the brief, at least one row in the JSONL reports carries that `item_id`
  (with any verdict, including `pass`, `fail`, or `n/a — motivo`). A brief
  item with no row is a violation — "silence is not a verdict" made
  mechanical. Mutation tests in `tests/test_audit.py` (compliant,
  missing-item, fully-silent-reviewer).
- **Rationale.** This is the counting-not-judging move the workflow has
  wanted since D-020 ("the orchestrator checks coverage mechanically").
  A reviewer can still produce a wrong verdict — that's the Spec axis's
  judgment — but it can no longer *omit* an item and have that look like
  a pass. The check operates on `item_id` strings, so it does not need
  to understand the criterion semantics; that isolation is what makes it
  cheap and stable.

## D-028 — I6 (zero actionable findings to exit) is enforced by the status field on the JSONL report, not by judgment (2026-10-15)

- **Decision.** I6 is verified by `audit.py check_i6_zero_actionable`
  (B-025 voce 10): for every row in the JSONL reports, an
  `status=open` + `severity=fatal` + `verdict=actionable` combination is
  a violation — the loop must continue. `status=resolved` closes the
  finding (set by the next commit / re-review); `severity=advisory` is
  never a blocker and never triggers I6. Mutation tests in
  `tests/test_audit.py` (compliant, open-fatal, closed-finding-passes,
  advisory-never-blocks). The Spec axis still judges *whether* a finding
  is actionable — that judgment stays prompt — but the *gate* on loop exit
  is mechanical.
- **Rationale.** This is the same `prompt → script` move D-026/D-027 make
  on adjacent rules, and the rationale has the same shape: the check
  operates on a structured field (`status`) and on a closed enumeration
  (`verdict ∈ {open, resolved}`, `severity ∈ {fatal, advisory}`), so its
  semantics are unambiguous and its falsifiability is testable. It does
  *not* try to second-guess whether a finding *should* be actionable — the
  Spec axis owns that judgment, and conflating "is this actionable" with
  "is this open" is the kind of conflation that made the prior rule
  unfalsifiable.

## D-029 — "Phases in order" stays `prompt` until each phase has a machine-readable artifact (2026-10-17)

- **Decision.** "Phases in order" remains at level `prompt` for phases
  1-4. **Trigger of escalation**: when each phase produces a file under
  `output/<phase>-<feature>.json` (or similar), an `audit.py
  check_phase_artifacts` runs and reads them — moving the rule from
  `prompt` to `script`. The phase skill files (`phases/1-...md`,
  `phases/2-...md`, `phases/3-...md`, `phases/4-tickets.md`) declare the
  expected artifact for each phase; once those declarations are
  accompanied by a writing convention, the check becomes possible.
- **Rationale.** Promoting the rule to `script` today would require either
  inventing file formats the workflow does not produce (over-engineering,
  ponytail violation) or scanning chat/ticket prose for keywords (the same
  prompt-in-prose defect B-024 was written to prevent). The honest answer
  is: the rule is load-bearing but not yet machine-checkable, so it stays
  `prompt` until phase skills emit a structured artifact. Phase 5's
  precedent (`docs/design/<feature>.md` → `--gate`) is the same move.

## D-030 — "Main agent never edits / fresh context" stays `prompt` until the adapter records the author (2026-10-17)

- **Decision.** The rule stays at level `prompt`. **Trigger of
  escalation**: when the agent adapter (Claude Code / Pi / OMP)
  records the actor of each commit in a machine-readable way (e.g. via
  `Co-authored-by:` trailers the orchestrator can verify, or a separate
  `actors/<session>.json` log), `audit.py check_actor_isolation` runs
  and verifies (a) the main agent never appears as a committer on the
  session's branch, and (b) every implementer commit has a fresh-context
  marker (e.g. a different committer email or a session-id trailer).
- **Rationale.** Anti-vacuity (D-002) requires the check to fail on
  violating input. Without an actor trail, no check can be written — the
  absence of data is not a state the audit can read. The trigger is
  concrete: the adapter must write the actor somewhere the audit can
  read, then the rule moves to `script`. Today the rule is held by the
  brief and by the human's attention, both of which already failed once
  (2026-10-08) — the fix is structural, not behavioral.

## D-031 — "Review axes read-only, mutation prover in scratch" stays `prompt` until B-004 reporters are in production (2026-10-17)

- **Decision.** Both sub-rules stay at level `prompt`. **Trigger of
  escalation**: when `gather_reviewer_reports` and `gather_pr_diffs`
  (B-004, `audit.py`) are wired into the standard `audit.py --repo ...`
  run *and* the reports carry an `actor` field naming the sub-agent
  role, an `audit.py check_reviewer_isolation(reports, pr_diffs)` runs
  and verifies that no report quotes a diff the analyzer committed (i.e.
  the report's `commit` was authored by a different actor than the
  report's `reviewer` field). At that point, the rule moves to
  `script`.
- **Rationale.** Same anti-vacuity reasoning as D-030: the check cannot
  fail today because the data is not collected. The trigger is
  concrete and aligned with an existing backlog item — B-004's
  `gather_*` functions and the JSONL schema's `reviewer` field are the
  hooks. When the in-production reporters are wired, the rule
  escalates; until then, the brief holds it.

## D-032 — "Pre-push gate" stays `prompt` until the kit's hook is verifiable per-repo (2026-10-17)

- **Decision.** The rule stays at level `prompt` for the *workflow*
  (`WORKFLOW.md` documents the gate as the canonical pre-push
  behavior). **Trigger of escalation**: when an `audit.py
  check_pre_push_gate_installed(repo)` reads the agent's hook
  configuration (`~/.claude/settings.json` for Claude Code, the
  equivalent for other agents) and verifies the kit's gate is wired,
  the rule moves to `script`. Until then, the workflow trusts the
  operator (or the operator's install procedure) to wire the hook.
- **Rationale.** The gate exists; what is missing is the *audit of its
  presence*. Anti-vacuity (D-002) is satisfied by the gate itself
  (it can fail when invoked) but not by the *rule that the gate is
  installed* — that is the load-bearing part this D-032 covers. The
  trigger is concrete and orthogonal to the gate's own correctness:
  install verification is its own concern, and B-007 already lists it
  as a candidate for the kit's adapter layer.

## D-035 — I2 and I6 verification lines now cite the scripts that verify them (2026-10-17)

- **Decision.** `INVARIANTS.md` is updated to cite the verifying scripts
  inline: I2 adds `` `guards/check_design.py --gate` (pre-push hook,
  B-023) `` to its Verification line; I6 adds
  `` `audit.py check_i6_zero_actionable` (D-028) ``. The clauses (the
  "manual handoff audit" / "handoff audit" parts) are preserved — the
  judgment is still required, but it is now accompanied by a script.
rule **visible**, not just promised. `check_invariant_verification_
  exists` makes the same demand on the invariant registry itself: an
  invariant that says "judgment" without an accompanying tool is a
  declaration of taste, not a law. The drift this caught is small
  (citations were missing, scripts were not) — but anti-vacuity
  principles say the *existence of the check* is what makes the rule
  load-bearing, and the check is now wired.

## D-040 — R-003 chiude le 4 ipotesi discriminanti B-025; merge in master (2026-10-15)

- **Decision.** Merge di `experiment/prompt-to-script` in master
  (`--no-ff` per commit di merge visibile). 4/4 ipotesi discriminanti
  B-025 PASS: H5 (premises, T7-synth 5/5), H6 (phase 2, T8-synth 5/5),
  H7 (invariant verification, T6-synth 5/5), H8 (claim present,
  T9-synth 4/4). I 3 check di B-004 (verbatim, copertura, I6) sono
  regression (già in master) — i 190 test mutation esistenti li
  coprono. 1 bug scoperto durante la validazione: D-035-bis
  (`check_invariant_verification_exists` non rilevava `**Verification**: `
  riga vuota, fix in `f349e7e`).
- **Rationale.** R-003 ha pagato il proprio costo: senza T6-synth il
  bug D-035-bis non sarebbe emerso (i test mutation esistenti non
  coprivano il caso "Verification vuota"). Il task synth ha stressato
  un caso limite reale, e il fix è in master. Le 7 voci B-025 sono
  ora in master come check in `audit.py`, ognuna con i propri test
  mutation. Voci 1/6/7/11 restano prompt-only con trigger (D-029/30/31/32).
  Voci 9/10/11 di B-025 non sono state misurate in R-003 (rimandate
  a R-004) — sono coperte da test mutation esistenti.

## D-041 — B-025 voce 14 sale a `struttura` via pre-commit hook (2026-10-15)

- **Decision.** B-025 voce 14 sale a `struttura` via
  `guards/hooks/pre-commit-rationale`. Il check `audit.py
  check_rationale_entry_in_pr` resta come ridondanza (per i merge PR
  su GitHub, dove il hook locale non è stato eseguito). Il workflow
  ha ora **1 regola a livello struttura**: la prima.
- **Rationale.** L'inverse-pyramid di poteto richiede che le regole
  più importanti siano rese **impossibili da violare**, non solo
  rilevate a posteriori. Un hook pre-commit che blocca un commit
  senza `RATIONALE.md` su `phases/*.md` o `WORKFLOW.md` è struttura
  nel senso del workflow: l'agent e l'umano non possono committare
  un cambio di regole senza motivazione. L'eccezione documentata è
  `git commit --no-verify` (escape hatch esplicito, registrato come
  tale; il check `audit.py` lo cattura lo stesso sulla PR).

## D-043 — Le scelte portanti del Program Design richiedono `## Decision` e il sign-off del committente (2026-11-09)

- **Decision.** Una scelta del Program Design che cambia comportamento
  dell'agente, costo di esecuzione o un contratto esterno (CLI argv, chiavi di
  configurazione, wire format, layout dei file) è **load-bearing**: deve
  comparire come `## Decision` con la scelta, le alternative scartate, le
  conseguenze e un link al commento di sign-off del committente. La sezione
  sta nel file di design (`docs/design/<feature>.md`) e nel `## Program Design`
  appeso al ticket; una scelta per sezione, l'heading inizia con `## Decision`.
  Un design che fa una scelta così senza la sezione è incompleto; l'asse Spec
  la alza come `fatal`, citando il testo del design come evidenza. Se il design
  non ha scelte portanti, non serve alcuna sezione.
- **Rationale.** La scelta non è un dettaglio implementativo: cambia il
  comportamento dell'agente e il costo, e il contratto esterno è la parte che
  il committente deve poter vedere prima che diventi codice. Il design è già
  il contratto (I2); una decisione portante senza alternative e senza sign-off
  non è verificabile né reversibile. La regola vive a livello **globale**
  perché lo stesso difetto può comparire in qualunque repo.

## D-044 — Le fixture dei sistemi esterni dichiarano la provenienza in un manifest verificato (2026-11-09)

- **Decision.** Una fixture che sta per un sistema esterno dichiara la
  provenienza in un manifest markdown (default
  `tests/fixtures/PROVENANCE.md`) con `fixture`, `source` (`captured` /
  `contract` / `synthetic`), `reference` e `sanitized`/`why`. `captured` è un
  campione reale ripulito (`sanitized` dice cosa è stato rimosso, o `none`);
  `contract` viene da un documento di contratto nominato; `synthetic` è
  ammesso solo con il motivo. `guards/check_fixtures.py` valida il manifest
  (forma, esistenza dei file, source/reference/sanitized/why) e con `--scope`
  segnala i file non dichiarati. Il design elenca i sistemi esterni e il
  manifest in `## External fixtures`. L'asse Spec (g) giudica se la fixture è
  esterna e se la fonte è quella giusta: una fixture esterna senza riga, o
  `synthetic` senza motivo, è `fatal` se usata da un contract test, `advisory`
  altrimenti. Se il design non ha fixture esterne, lo dice in una riga.
- **Rationale.** La fixture è la prova del contratto con il mondo esterno: se
  è inventata, il test prova una tautologia e la review non ha nulla da
  leggere. Il manifest rende la provenienza un fatto reviewabile (chi, quando,
  da quale sistema, cosa è stato rimosso) invece di una convenzione implicita;
  il checker rende la forma del manifest meccanica, mentre la completezza
  (quali fixture sono esterne) resta giudizio. Il costo è una tabella per
  design, non una nuova infrastruttura.

## D-045 — Il rosso TDD deve fallire per il motivo giusto, e il report lo cita (2026-11-09)

- **Decision.** Due righe nella skill `tdd` e nel brief dell'implementer:
  (1) il test rosso deve fallire per il motivo previsto — un'asserzione sul
  comportamento, non un import/setup error; (2) il report dell'implementer cita
  il test rosso (`path::name`) e l'errore prodotto, poi la corsa verde. Un test
  mai visto rosso non è una prova.
- **Rationale.** D-019 ha già la parte forte (il mutante che resta verde =
  blind test); questa regola copre la parte debole e più economica: il rosso è
  la prima prova che il test discrimina qualcosa, ma solo se il fallimento è
  quello giusto. Un import error o una fixture mancante producono un rosso che
  non dice nulla sul comportamento. Citare il rosso nel report rende la prova
  reviewabile senza ricostruirla dalla chat.

## D-046 — Il mutation prover è agente + script: il giudizio sceglie i mutanti, lo script li esegue (2026-11-09)

- **Decision.** Due ruoli. (1) **Agente**: per ogni legge dichiarata o
  criterio `fatal` sceglie l'implementazione sbagliata ma plausibile e la
  scrive come patch in un manifest macchina — `id`, `file`, `patch`,
  `invariant`, `test`, `note`. (2) **Script**
  `guards/mutation_prover.py`: worktree pulito per mutante,
  `PYTHONPATH` sul worktree, **controllo per primo** (verde/no-test → matrice
  `inconclusive`, exit 1), poi la suite mirata; emette una matrice JSONL
  `pinned` / `blind-test` / `no-test` / `error` con il diff di ogni mutante e
  rimuove i worktree. `mutmut` resta opzionale e consultivo, mai gate.
- **Rationale.** Il giudizio — quale implementazione sbagliata è plausibile —
  resta all'agente, dove serve il contesto; la parte meccanica (applicare,
  isolare, girare, ripristinare, registrare) è deterministica e va a uno
  script. Il controllo è una riga che il prover esegue, non una promessa nel
  brief; `PYTHONPATH` sul worktree elimina il falso verde del venv editable; il
  worktree usa-e-getta elimina la contaminazione del ramo. La matrice JSONL è
  la stessa forma strutturata di B-004, quindi i reviewer possono consumarla.

## D-047 — Ogni claim di report/handoff porta la prova o l'etichetta misurato/dedotto/ipotesi (2026-11-09)

- **Decision.** Ogni affermazione in un report finale o in un handoff porta,
  nella stessa frase, la prova oppure l'etichetta `measured` / `deduced` /
  `hypothesis`. Un numero senza una misura è `hypothesis`, non un risultato.
- **Rationale.** È la versione leggera della confidence ladder (B-020): non
  aggiunge un formato nuovo, ma rende la fiducia reviewabile. Il lettore sa
  subito se sta leggendo un dato, un'inferenza o un sospetto; il reviewer può
  chiedere la prova senza ricostruire la chat.

## D-048 — Risk assessment deterministico nella PR e blast-radius condizionale (2026-11-09)

- **Decision.** Dopo la review a zero findings e prima della PR, il
  coordinatore scrive nel corpo della PR `Risk: low|med|high`,
  `Blast radius:` (file di confine: DB, auth, migrazioni, API pubbliche,
  infra, deps) e `Where to look:`. Euristica deterministica sui path:
  migrazioni/auth/infra/deps/API pubblica = high; API interne/moduli
  condivisi/config = med; UI pura/refactor meccanico/test-only = low. Per
  `med|high`, esegue il blast-radius condizionale: cerca fuori dal diff e
  prova il fatto da cui dipende la sicurezza; se non può eseguirlo, dichiara
  `residual` ed escala un contratto pubblico. La skill `why` (B-017) resta il
  primo passo leggero ed è ancora aperta.
- **Rationale.** La review guarda il diff; le rotture vere stanno spesso fuori
  (un pin, un consumer, un deploy script). L'etichetta non è un giudizio di
  review e non deve diventare un reviewer in più: è una checklist deterministica
  che dice al committente dove guardare. B-014 è condizionale perché il costo
  della ricerca fuori dal diff si giustifica solo per `med|high`.

## D-049 — Il kit pubblico è un artefatto generato dal repo canonico (2026-11-09)

- **Decision.** Il repo canonico è l'unica superficie di modifica. `kit/`
  contiene i file public-only (README, CHANGELOG, LICENSE, install.sh, skills,
  handoffs skeleton); `sync_kit.py` deriva documenti, guardie e test,
  riscrive i path, neutralizza i nomi di progetto e applica un leak gate;
  `--check` fallisce su drift o riferimento privato, `--write` rigenera il
  target. Il repo pubblico `agentic-dev-kit` diventa un mirror generato, con
  lo stesso tag; il `RATIONALE.md` pubblico è un decision register generato
  (heading + Decision + Rationale, senza evidence privata). La skill installata
  può puntare al `kit/skills` canonico o a un checkout generato.
- **Rationale.** Una sola fonte elimina la classe di drift che ha colpito
  questa release; il confine pubblico/privato resta, ma diventa un controllo
  meccanico invece di una disciplina. Gli utenti continuano ad avere un repo
  pubblico normale, con README, CHANGELOG e tag; il manutentore non edita due
  copie. Il leak gate ha già trovato nomi di cliente nel kit v0.3.0: senza
  generatore, il confine era già rotto.
