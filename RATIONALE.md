# Workflow Design Rationale — why the workflow is shaped this way

> The workflow's **memory of why**. WORKFLOW.md states the rules; this
> document states the reasons behind the load-bearing ones — the failure,
> observation, or tradeoff each rule answers. Rules without a rationale here
> are orphans: they will be followed blindly or dropped without a trace.
> Date: 2026-08-16.

---

## Sync contract (keep this aligned)

- **Every change to WORKFLOW.md that adds, alters, or removes a load-bearing
  rule → a decision entry here, in the same session**, citing the rule
  location. No orphan rules.
- **Every decision entry cites its rule location** (WORKFLOW.md section or
  skill) and its source (handoff, commit, issue).
- WORKFLOW.md's General rules points to this document (two-way link).
- Append new decisions with the next `D-###` id; revise an existing decision
  in place when the rule changes, keeping the date.
- The enforcement layer is INVARIANTS.md + audit.py; this document is the
  memory layer — a change without an entry is a drift, not a violation
  (yet).

## The pipeline at a glance

Five phases of design/alignment, then implement-loop. Why: the BASELINE
(Pocock flow) went straight from conversation to implementation — design
decisions were made implicitly, code shape was discovered during
implementation, and nothing was visualized before architecture. The review
inside implement-loop now carries the ponytail-vs-engineering tension
(third axis + user-weighted resolution), and the pre-push gate runs the
canonical test/lint/CI — the separate tightrope gate is folded in.
TARGET.md records the evolution; this document records the reasons.

---

## D-001 — Phase 6: the main agent orchestrates and never edits code (2026-08-03)

- **Context.** The implement-loop rule that the main agent orchestrates and
  never edits code directly is load-bearing but has no decision entry — it
  predates RATIONALE.md and was excluded from INVARIANTS.md only because no
  corpus trace records it ("Not auditable", not "not a rule").
- **Decision.** WORKFLOW.md phase 6: the main agent orchestrates; subagents
  implement with TDD and fresh context; reviewers see verbatim diffs and
  the criteria family (D-004). The main agent never edits code directly.
- **Rationale.** An orchestrator that edits code becomes part of the
  implementation: its edits bypass the TDD loop, arrive without fresh
  context, and the review sees them as already-settled. The separation is
  what keeps the criteria family (D-004) independent of the code it judges.
  It is a process rule, not an invariant — unverifiable from traces by
  design, which is why it lives here and not in INVARIANTS.md.
- **Rule location.** WORKFLOW.md phase 6.
- **Source.** WORKFLOW.md phase 6 (commit `75a62a6`); INVARIANTS.md
  "Not auditable (excluded from codification)".

## D-002 — Every session ends with the transversal gate: audit + handoff (2026-08-05)

- **Context.** The pre-invariant workflow ended sessions with no verifiable
  trace: the 4 example-service handoffs existed but lived on the Desktop,
  outside version control — the gap was the location, not the existence
  (I5 evidence). Work done outside the pipeline — local design docs,
  direct commits, exploration — was invisible to any tracker-based check.
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
- **Rule location.** WORKFLOW.md `## General rules` (transversal gate),
  `## Workflow invariants (transversal)` (I1–I6).
- **Source.** Commits `bb3f634`, `97dcbbf`, `8aeed10`, `43c5575`;
  `INVARIANTS.md` I5; `INVARIANTS-METHOD.md` §6.

## D-003 — Phase-3 skip criteria judge domain value, not diff shape (2026-08-06)

- **Context.** The system-architecture skip list (`mechanical refactor`,
  `follows existing pattern`) silently dropped the phase's invariant-
  elicitation output: refactors of modules with real domain laws (status
  machine, quota, outbox, graph cascade) shipped without a
  `## System Invariants` section (INVARIANTS.md Evolution).
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
  away the one artifact that pays for the phase. The example-project campaign showed
  bugs live at boundaries and in triplicated logic; refactors touch exactly
  those.
- **Rule location.** WORKFLOW.md phase 3.
- **Source.** Commit `371d3da`; `INVARIANTS.md` "Evolution (TARGET)";
  `INVARIANTS-METHOD.md` §6.

## D-004 — Reviews and gates are families of small orthogonal criteria (2026-08-16)

- **Context.** The example-project session (issue #145) showed a monolithic
  adversarial judge failing: one prompt, several vague overlapping
  questions, one boolean verdict — it rationalized bad outputs because the
  criteria were fuzzy and circular ("supported by the body's reasoning"
  while the body itself was ungrounded). The handoff generalized this into
  a family of small criteria; this session reviewed it and corrected it
  before codifying.
- **Decision.** Phases 6–7 and the review skills (code-review,
  implement-loop, tightrope) run as criteria families: defined scope,
  falsifiable per-item question, pre-sliced evidence, structured finding
  with **verified** evidence (quote must match the diff verbatim), severity
  (`fatal` / `advisory`, pass-with-warning kept), dependency order (DAG).
  Taxonomy is owned by `WORKFLOW.md → ## Review criteria family`; the skills
  reference it. Criteria are codified, not invented — no new criteria were
  added beyond what already existed.
- **Rationale (the three load-bearing whys).**
  1. **Evidence must be verifiable** — an LLM that hallucinates a claim can
     hallucinate the supporting quote; "no evidence = not accepted" is
     bypassed with fake evidence. The deterministic quote-match check is the
     cheapest member and the only one that keeps LLM evidence honest.
  2. **Orthogonality is not the mechanism — dependency order is.** V2's
     circularity is fixed by V1 grounding the body first and V2 consuming
     V1's verdicts. The family is a DAG, not a flat fan-out; "parallel
     per-rule calls" would let the circularity back in.
  3. **The gate must not be all-or-nothing** — a binary accept/reject gate
     cries wolf, gets ignored, and recreates the problem it solves. Severity
     + partial-failure thresholds keep the existing pass-with-warning path.
- **Rule location.** WORKFLOW.md `## Review criteria family`, phases 6–7;
  skills: code-review (`## Criteria family`), implement-loop (reviewer
  briefs), tightrope (tension check).
- **Source.** `handoffs/handoff-adversarial-validation-20260816.md` (rev. 2,
  corrections [1]–[6]); `handoffs/handoff-review-criteria-family-20260816.md`;
  commits `5867fa1`, `7d7601d`.

## D-005 — Phase-2 (product-review) decisions are pronounced in session (2026-08-16)

- **Context.** The user observed that product review never surfaces in
  sessions. Root cause: example-project is backend-only — no UI surfaces in the repo —
  so phase 2 is legitimately skipped on every feature; but the skip was
  silent in conversation, annotated only on the tracker (I3).
- **Decision.** WORKFLOW.md phase 2 carries a visibility rule: the phase-2
  decision is always stated in the session — "Phase 2 — product review:
  run" or "Phase 2 skipped — <reason> (annotation on the ticket)". A skipped
  phase is never silent.
- **Rationale.** A decision the user never sees is a decision they can't
  trust or challenge. The phase was working correctly (skip criteria were
  applied, I3 annotations written); the failure was visibility, not logic —
  so the fix is a spoken line, not a process change.
- **Rule location.** WORKFLOW.md phase 2.
- **Source.** `handoffs/handoff-review-criteria-family-20260816.md`;
  TARGET.md row 14.

## D-006 — Pocock skills refreshed from upstream; local edits re-applied (2026-08-16)

- **Context.** Installed Pocock skills were from 2026-07-31/08-03; upstream
  `mattpocock/skills` HEAD `068b6e0` (2026-08-15) had evolved in all 18
  matching skills. The criteria-family edits in code-review were the only
  local customization.
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
- **Rule location.** `~/.claude/skills/` (not git-versioned — deviation
  documented in the handoff); BASELINE.md installed-skills map.
- **Source.** `handoffs/handoff-review-criteria-family-20260816.md`;
  commits `6a22178`, `fb41a75`, `e84133a`.

## D-007 — Anti-AI-look baseline in the review family (2026-08-28)

- **Context.** UI reviews had no criterion for the "generated-by-AI" defect
  class: default purple/indigo gradient heroes, centered glassmorphism
  cards, near-identical card rows, filler copy. The class is real and
  observed — it is the entire premise of the jam.with.ai reel on Claude
  Code UI work (this session), and the mockup rules in product-review
  offered no guardrail against it.
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
- **Rule location.** WORKFLOW.md `## Review criteria family` (Standards
  axis (c)); product-review SKILL.md Section C.
- **Source.** Session 2026-08-28 (reel `DcjEohigobV` analysis);
  `agentic-dev-kit` commit (this session).

## D-008 — Persona × edge-case simulation in UI phases (2026-08-28)

- **Context.** UI success criteria were derived from stories alone — no
  obligation to cover distinct personas or the edge-case states (empty,
  error, loading, long content, small viewport) — and implement-loop had no
  usability verification step: the harness rule says "browser-drive a UI
  change", but nothing required personas or edge cases, so verification
  defaulted to a visual once-over. The reel's author calls her custom
  "simulation" pass — test different edge cases, different user types,
  actually use the site in Chrome — the most important part of her Claude
  Code UI workflow.
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
- **Rule location.** WORKFLOW.md phase 2, phase 6; product-review SKILL.md
  Section B; implement-loop SKILL.md step 5 + completion criteria.
- **Source.** Session 2026-08-28 (reel `DcjEohigobV` analysis);
  `agentic-dev-kit` commit (this session).

---

## D-009 — Program design runs near-always (2026-08-30)

- **Context.** The user observed that program design almost never fires.
  Root cause: the skip list (`bug fix`, `mechanical refactor`, `trivial
  feature`, `one-shot`) covered most real work, so the phase was skipped by
  default. This mirrors D-005's diagnosis (product-review never surfaced),
  but the cause here is the skip criteria themselves, not visibility.
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
- **Rule location.** WORKFLOW.md phase 5; program-design SKILL.md §3.
- **Source.** User request 2026-08-30.

---

## D-010 — Tightrope folded into the review: ponytail axis + tension resolution (2026-08-30)

- **Context.** tightrope was an optional pre-merge gate; the user wanted the
  ponytail-vs-engineering criteria to live in the review itself, not as an
  optional final step — with the user deciding the weight between the two
  axes and the reviewer escalating ambiguous tensions.
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
- **Rule location.** WORKFLOW.md phase 6, phase 7, `## Review criteria
  family` (ponytail axis, tension resolution); implement-loop SKILL.md step 2.
- **Source.** User request 2026-08-30.

---

## D-011 — Pre-push gate (test/lint/CI) moves into implement-loop (2026-08-30)

- **Context.** Removing tightrope would drop its one capability that nothing
  else owned: running the canonical test/lint over the whole repo before the
  PR, and holding the PR to CI green. Backlog B-002 had already designed
  exactly this gate for implement-loop; this session implements it.
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
- **Rule location.** implement-loop SKILL.md steps 6–7; WORKFLOW.md phase 6.
- **Source.** User request 2026-08-30; BACKLOG.md B-002.

---

## D-012 — "Session" means the whole conversation: the gate runs at the end (2026-09-11)

- **Context.** In the session of 2026-09-11 the assistant read "Every session
  **ends** with the transversal gate" as "after each turn" and, following a
  single status question, ran `audit.py` and committed
  `handoff-diario-status-20260911.md`. The conversation continued immediately
  into the design phases, which made that handoff wrong on arrival: it declared
  "next session: grilling" while the grilling was starting in the same session.
  The premature commit was dropped (it had never been pushed).
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
- **Rule location.** WORKFLOW.md General rules — the transversal-gate bullet
  and the clarification beneath it.
- **Source.** User correction, session 2026-09-11 (*"si ma è obbligatorio alla
  fine, no?"*).

## D-013 — Phase 1 falsifies the spec's premises before publishing (2026-09-11)

- **Context.** Epic #263 ("Diario della Casa") was written from an external
  product spec, and its Contesto asserted two things about this codebase —
  **both false**: that `asset_events`/`house_events` were read-only system audit
  logs, and that the Diario would be the first user-facing timeline. In fact the
  user already wrote into those logs (the `user_note` event type has full CRUD)
  and a merged, cursor-paginated timeline already existed behind the
  `HistoryRoot` seam, exposed as the house *History* tab. The grilling round
  found it, and killing the premise **changed the design**: the decision that
  the Diario is the *existing* surface plus a fourth source — not a new view —
  exists only because the false premise died.
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
- **Rule location.** WORKFLOW.md phase 1.
- **Placement (decided 2026-09-11).** The rule lives in WORKFLOW.md phase 1 —
  the file every session is instructed to follow as the canonical reference.
  Two alternatives were considered and **deferred, not rejected**: patching the
  installed copy of the upstream phase-1 skills (`~/.claude/skills/`, not under
  version control, lost on reinstall, diverging from upstream with no record),
  and forking phase 1 into `~/agentic-dev-kit/` (which takes on maintenance of skills
  that are not ours). Both close the residual gap — an agent that reads only
  the skill it is about to execute never sees this rule — at a cost the
  evidence does not yet justify: the observed failure was *"the rule did not
  exist"*, and since 2026-09-11 it does. **Escalation trigger:** the first
  session that publishes a spec carrying an unverified premise about the code
  escalates to the patch (cheap) or the fork. Same criterion as I8 — a defence
  is added when the failure is observed, not when it is hypothesised.
- **Source.** Session 2026-09-11; epic #263 → spec #274; the ADR-0016
  "same surface" decision.

## D-014 — Three new invariants: gates in CI, follow-ups tracked, commands quoted (2026-09-11)

- **Context.** On 2026-09-11 the audit reported *"ok — all workflow invariants
  hold"* for `example-service` while that repo declared `[tool.mypy] strict = true`
  and ran **no mypy and no ruff in CI**: 274 errors had accrued, one of them a
  real bug (`graph/backfill.py` called an async pool-init without `await`, so
  the script had never been executable). **I1 was being satisfied vacuously** —
  a CI that runs no gates is always green. Separately, four consecutive
  handoffs asserted *"ruff/mypy: 0 new errors on touched files"* with no command
  behind the claim, and untracked advisories had already produced a real
  violation once (the evidence that removed I7).
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
- **Rule location.** INVARIANTS.md I8/I9/I10; `audit.py`
  `check_configured_gates_run_in_ci` / `check_followups_tracked` /
  `check_handoffs_quote_commands`; `tests/test_audit.py`.
- **Source.** Session 2026-09-11; #282 (the typing debt and its diagnosis),
  PR #287 (the backfill fix), #286 (the tracked non-blocking follow-ups).

---

## D-015 — I9 reads *named* follow-ups, not words: label or follow-up section (2026-09-17)

- **Context.** Closing the Diario session on 2026-09-17 the audit reported
  `[I9] handoff-diario-verification-20260917.md:23 names a follow-up with no
  tracker reference` for a line describing work **delivered** in that session,
  whose tail read *"solo un'osservazione advisory sull'indicatore della tab
  attiva"*. Nothing in it is a follow-up. **An hour later the same check fired
  again on the handoff written to record the first fix**, this time on
  *"I9 smette di leggere un severity come follow-up"* — the other marker word,
  in a stanza about the invariant itself. Two false positives, two markers:
  the defect was not that `advisory` is ambiguous, it is that the check matched
  a word *containing* a line instead of a line *labelling* something.
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
- **Rule location.** INVARIANTS.md I9 (clause + evidence); `audit.py`
  `check_followups_tracked`; `tests/test_audit.py` (5 mutation tests: both
  observed false positives, the anchored bullet, the unbolded heading, prose
  inside a follow-up section, and the section boundary ending at the next
  heading).
- **Source.** Session 2026-09-17 (example-service Diario, PR #320). The
  workaround used before the fix — rewording a handoff to avoid a bare word —
  is what a heuristic forces when it cannot tell a label from a word.

## D-016 — I9 is scoped to the project: the handoff declares its repo (2026-09-19)

- **Context.** The corpus in `~/agentic-dev-kit/handoffs/` is shared by every
  project, while `gather_known_refs(repo)` resolves `#N` against the repo
  passed to `--repo`. So I9 judged other projects' handoffs against this
  project's tracker: on 2026-09-19
  `python3 audit.py --repo Example/a-data-pipeline` reported
  `[I9] handoff-diario-mockup-fidelity-20260917.md:41 cites follow-up #319,
  which does not resolve on the tracker` — `#319` is a ticket of
  `Example/example-service`, closed by that project's PR #320. The
  defect was invisible on the Example side because the audit aborted
  earlier, in `gather_prs`, on a token without check-run access: the I1
  fallback on the Actions channel (`73a362c`) let the audit reach its end for
  the first time, and I9 spoke the moment it could.
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
- **Rule location.** WORKFLOW.md (handoff content: the repo declaration);
  INVARIANTS.md I9 (verification scope, clause, evidence — third
  false-positive class with the measurements); `audit.py` (`HANDOFF_REPO_RE`,
  `_handoff_repo`, `gather_handoffs`,
  `check_followups_tracked(handoffs, known_issues, repo)`);
  `tests/test_audit.py` (5 new mutation tests + the 9 existing ones updated to
  declare the repo; neutralising the filter turns 2 of them red — verified).
- **Source.** Session 2026-09-19 (Example news pipeline). The false
  positive surfaced within minutes of the audit becoming runnable, and the fix
  is why the audit now exits 0 on that repo instead of blaming another
  project for its own refs.

---

## D-017 — Session isolation: one writer per working tree (2026-09-20)

- **Context.** Git's working tree is per repo, and no rule claimed it.
  Worktrees were named in exactly two places — `implement-loop` §0 and its
  "Dependency resolution" section ("run independent items in parallel in
  separate git worktrees", agentic-dev-kit) and one line of `BASELINE.md`
  ("sequential or parallel with `worktree: true`") — with no entry in this
  document and no mention in `WORKFLOW.md`: an orphan rule, and the corpus
  shows it was followed accordingly. The parallel batch of 2026-08-06 ran
  four implement subagents on one working tree
  (`handoff-invariant-gaps-20260806.md:29`), and the case the rule existed
  for — two sessions on `a-data-pipeline`, 2026-09-19 — went to
  `git stash` instead: one session could not change branch without moving the
  other's working tree, two stashes were cycled between them, and a commit
  landed on the other session's branch
  (`handoff-<feature>-20260919.md`, deviations 1 and 4). The
  premise behind the status quo ("worktrees are already used") was falsified
  before writing anything: the only real worktrees in the 73-handoff corpus
  are incidental — a session launched in one, `/tmp` probe worktrees
  since removed.
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
- **Rule location.** `WORKFLOW.md` ("Session isolation — one writer per
  working tree"); `~/.pi/agent/AGENTS.md` (pi mechanism: cwd is fixed at
  launch, so the worktree is the session's launch directory or is addressed
  explicitly — harness config, deliberately not in `WORKFLOW.md`);
  `INVARIANTS.md` ("Not auditable", why this carries no check); the
  item-parallelism line in `implement-loop` (agentic-dev-kit) is unchanged and
  still valid — it isolates *items* within one session, this rule isolates
  *sessions* from each other.
- **Source.** Session 2026-09-20 (workflow repo). Evidence: the 2026-09-19
  retention handoff (deviations 1 and 4), `handoff-invariant-gaps-20260806.md`,
  and a grep of the whole workflow + skills corpus for `worktree`, which
  found the two orphan mentions and no rationale. The structural half of the
  rationale was verified on a scratch repo, not asserted: `git rev-parse
  --git-common-dir` resolves to the same directory from the primary tree
  (`.git`) and from a worktree (`<primary>/.git`), and `git checkout feat-x`
  in the second tree → `fatal: 'feat-x' is already used by worktree at
  '<path>'`.

---

## D-018 — Worktrees are cleaned at session end, on evidence (2026-09-21)

- **Context.** D-017 told a session when to *open* a worktree and said nothing
  about closing one. Observed the same month on `a-shared-lib`
  (`handoff-<feature>-20260921.md`, deviation 3): **nine** stale
  worktrees, all under `.claude/worktrees/agent-<hash>` — not created by D-017
  at all, but by the agent harness, and **nested inside the repo**, which is
  exactly what D-017 forbids its own worktrees from doing. The cost is not
  disk: "another worktree of the repo" is one of D-017's three isolation
  signals, so every one of them makes every later session read the repo as
  shared and isolate for nothing — a rule whose own residue triggers itself.
  Four of the nine were not free to drop: one carried a commit not reachable
  from `develop` (`b04d4bc`, ADR-0012 slice 3) and three had uncommitted June
  work, which is why "just prune them" was not the answer.
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
- **Rule location.** `WORKFLOW.md` ("Session isolation — Cleanup at session
  end", and step (4) of the transversal gate under General rules);
  `INVARIANTS.md` ("Not auditable", extending D-017's row).
- **Source.** Session 2026-09-21 (`a-shared-lib`, S3 checkpoint offload
  design). Evidence: `git worktree list` on that repo (nine entries),
  per-worktree `git status --porcelain` and `git merge-base --is-ancestor`
  against `develop`, and the confirmation that `b04d4bc`'s content had in fact
  shipped (`_strip_lc_messages` / `_marshal_lc_messages` present in
  `develop`'s `conversation/aws.py` and `local.py`). The nine were removed in
  that session, branches included, following the procedure this entry
  codifies.

## D-019 — Invariant coverage is proven by an executed mutation, not by reading the diff (2026-09-24)

- **Context.** Session 2026-09-24, implement-loop run. The code under review
  was correct: `version = raw[COL_VERSION]` did the right thing from the
  first commit. What was missing was the **proof that it stays** correct —
  the existing test exercised that line with the value `"1.0.0"`, on which
  the rule is invisible: every plausible wrong implementation (normalise,
  strip, coalesce a default, read a sibling column) returns `"1.0.0"` too.
  The test was green under the correct implementation *and* under the wrong
  one. The gap was found only when the workflow owner ran the mutation by
  hand; both reviewers had read the same diff and neither could see it,
  because **a blind test reads exactly like a discriminating one**.
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
- **Rule location.** `WORKFLOW.md` (`## Review criteria family`: criterion 4,
  Spec axis (d), the "mutation prover" member; phase 6 bullet list);
  `implement-loop` SKILL.md (step 1 witness value, step 2b, completion
  criterion).
- **Source.** Session 2026-09-24, implement-loop run; defect observed by the
  loop manager agent and confirmed by the workflow owner running the
  mutation manually.

## D-020 — A reviewer answers every item of its brief; the orchestrator checks coverage mechanically (2026-09-24)

- **Context.** Same session, 2026-09-24. Two reviewer failures of opposite
  sign, both invisible in the report itself. The **Standards** axis never
  mentioned ADR-0013 — *which was in its own brief*: a silent false
  negative. The **Spec** axis claimed the invariant had no coverage at all,
  when a test existed and merely failed to discriminate: a true finding
  reported at the wrong weight. Both findings were valid in substance; the
  reports misrepresented their weight, and nothing in the output made either
  error detectable without redoing the review.
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
- **Rule location.** `WORKFLOW.md` (`## Review criteria family`: criterion 7,
  Standards axis preamble; phase 6 bullet "the reviewers are verified like
  the code"); `implement-loop` SKILL.md (step 2, "Verify the reviewers like
  the code", and the completion criterion).
- **Source.** Session 2026-09-24, implement-loop run; both reviewer errors
  reported by the loop manager agent.

---

## Not a rationale yet (candidates)

- **Audit check for this document** — RATIONALE.md is self-policing (a
  change without an entry is "drift, not yet a violation"). If drift shows
  up, the audit could flag WORKFLOW.md changes without a corresponding
  RATIONALE.md entry in the same commit. Requires a mutation test
  (anti-vacuity) — do not add without it.
- **The /implement pointer gap in ask-matt** — the upstream router routes to
  `/implement`, which is intentionally not installed (custom
  `/implement-loop`). Pre-existing; either edit ask-matt locally or accept
  the gap. Undecided.
