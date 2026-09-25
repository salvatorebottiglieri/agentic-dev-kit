# Workflow Invariants — the workflow under its own invariants

> Invariants of the **agentic process**, valid transversally across every
> project (example-project, example-service, …). A declared-but-unverifiable invariant is
> an opinion: each one here has a negation (→ check), a verification home,
> and the evidence collected in the corpus audit.
>
> Campaign format (example-project stress campaign): law → negation → verification →
> evidence → clause. **Per-feature** (domain) invariants do not live here:
> they live in the `## System Invariants` section of the spec, produced by
> system-architecture (see WORKFLOW.md phase 3).

## Corpus audit (2026-08-05)

Corpus examined: 4 example-service handoffs (quota-gate #191/#192, auth error
#193-#196, status machine #205-#209, search split #210-#214) + example-service
tracker + example-project stress-campaign session (#115). Evidence per invariant.

---

## I1 — PRs are merged only after CI is green

- **Law**: every PR is merged only after all required CI checks have passed.
- **Negation** → check: a merged PR whose `statusCheckRollup` is not `success` on all jobs.
- **Verification**: `audit.py check ci_before_merge` (best-effort: the rollup is the current state, not the state at merge time).
- **Evidence**: respected 5/5 (4 example-service sessions "committer-confirmed rule respected" + example-project #115). **No violation found** — keep the check, anti-vacuity via mutation test.
- **Clause**: a manual merge is an exception and must be annotated on the ticket.

## I2 — Program-design signatures are contract; deviation is legal only if justified and documented

- **Law**: if the ticket has a `## Program Design` section, the implementation follows the signatures; every deviation is legal only if the reason is documented on the ticket **or** the PR, and mentioned in the handoff.
- **Negation** → check: a handoff/PR that declares a deviation without documenting its reason on the ticket or PR.
- **Verification**: judgment (Spec axis of the review) + manual handoff audit.
- **Evidence**: **2 real deviations** in different sessions. #212: documented in an issue comment (✓). **#191: the handoff claimed "documented on #191" but the annotation is only on PR #192, not the issue (✗)** — the clause accepts both, but the inconsistency was the real violation: the documentation existed, its *location* was unpredictable.
- **Clause**: the deviation must be annotated on the issue (comment) **or** in the PR body; the handoff must say *where*. Never just "documented" without a location.

## I3 — Every skipped phase is annotated "Skipped — reason" on the ticket

- **Law**: every pipeline phase (product-review, system-architecture, program-design) skipped for a non-trivial feature is annotated on the ticket with `Skipped — <reason>`.
- **Negation** → check: a closed non-trivial ticket with no spec section and no `Skipped` annotation.
- **Verification**: `audit.py check skip_annotations` (warning — "non-trivial" needs judgment) + judgment.
- **Evidence**: **repeated violation**. The loops declared in the handoffs (grill → … → implement-loop) omit product-review and system-architecture, but no `Skipped` annotation is present on #205/#210/#212. The example-project campaign (#115) created no formal spec ticket. **Check/clause drift, fixed 2026-09-11** (same class as the I1-vacuous defect, in miniature): `gather_issues` requested only `number,state,body,title` and the check matched body-only, so an annotation the clause explicitly allows in a comment was invisible unless the body happened to carry a spec marker — #285 passed on its `## What to build`, not on its comment. Against the closed corpus the body-only check falsely flagged 8 tickets whose spec section was posted as a comment (#61/#73/#82/#84/#85/#88/#89/#90). The check now matches body ∪ comment bodies; the 8 are no longer flagged and no pre-fix-passing ticket gains a violation.
- **Clause**: the annotation may live in the issue body or a comment; the reason must be specific (e.g. "API-only", "mechanical refactor"), not generic.

## I4 — The PR links the issue/ticket

- **Law**: every feature PR links the issue(s) it implements.
- **Negation** → check: a merged PR with no issue references in its body.
- **Verification**: `audit.py check pr_links_issue`.
- **Evidence**: respected 7/7 (PRs #192/#196/#200/#208/#209/#213/#214 link their issues). **No violation** — keep the check, anti-vacuity via mutation test.
- **Clause**: cross-references (a PR citing other PRs/issues) do not replace the link to the primary issue.

## I5 — Every session leaves a handoff in the canonical corpus

- **Law**: every session ends with a handoff in `~/agentic-dev-kit/handoffs/` (git-versioned), named `handoff-<feature>-<YYYYMMDD>.md`.
- **Negation** → check: no handoff in the corpus for the last session.
- **Verification**: `audit.py check recent_handoff`.
- **Evidence**: the 4 handoffs existed but lived on the Desktop, **outside version control** — the gap was the location, not the existence. Migrated in this initiative (commit 645823c).
- **Clause**: the handoff lists: repo state, delivered work, verification (suite + review), deviations (with their location, see I2), next session, ground truth.

## I6 — Zero findings to exit the fix loop

- **Law**: the implement-loop ends only with zero actionable findings on both review axes.
- **Negation** → check: a handoff/PR declaring residual actionable findings as "accepted".
- **Verification**: judgment (the review is already a gate) + handoff audit.
- **Evidence**: "final: zero" in every documented loop (quota-gate, auth, status, search). **No violation**.
- **Clause**: findings resolved in re-review count; non-blocking follow-ups must be tracked (see I9).

## I7 — Every follow-up identified in a session is tracked on the tracker in the same session — **REMOVED (2026-08-05)**

Removed by the workflow owner: the corpus-wide repetition check produced
cross-project noise (shared handoff corpus × per-repo tracker lookup) and the
discipline is now carried by the mandatory session-end gate (handoff + audit,
WORKFLOW.md General rules) instead of a transversal check. Historical record:

- **Evidence**: real violation — `TestRateLimitHeadersOnSuccess` ("2-line fix")
  cited in 3 consecutive handoffs (auth → status → search) without being tracked;
  resolved 2026-08-05 via `example-service#241`.
- The follow-ups tracked under I7 (example-project#116, example-service#241) remain valid tracker items.

> **Restored as a check (2026-09-11):** the discipline this invariant carried is
> now **I9**, windowed to the recent handoffs and verified by `audit.py`. The
> removed-proxy problem (cross-project lookup noise) is gone: the reference is
> resolved against the repo the audit is already running against.

## I8 — Every quality gate the repo configures is executed by CI

- **Law**: every quality tool a repo declares in its config (mypy, ruff, …) runs as a CI step.
- **Negation** → check: a `[tool.mypy]` / `[tool.ruff]` section present in a repo config file, with no CI workflow that invokes the tool.
- **Verification**: `audit.py check_configured_gates_run_in_ci` — reads `pyproject.toml` / `ruff.toml` / `mypy.ini` / `setup.cfg` at any depth plus every `.github/workflows/*.yml`.
- **Evidence**: **real violation, 2026-09-11.** `example-service` declared `[tool.mypy] strict = true` and a ruff rule set; its single workflow ran tests only. 274 mypy errors had accrued, one of them a real bug (`graph/backfill.py` called an async pool-init without `await`, so the script had never been executable). The audit had reported "ok — all workflow invariants hold" throughout, because **I1 is vacuously satisfiable by a CI that runs no gates**: green because nothing is checked.
- **Clause**: best-effort like I1 — if the repo's config or workflows cannot be read, the check reports nothing rather than a violation. It fires on the *declaration*, not on the tool's output: whether the code passes is the repo's own business.

## I9 — A follow-up named in a handoff carries a resolving tracker reference

- **Law**: every non-blocking follow-up a handoff declares names a ticket — issue or PR — that exists on the tracker.
- **Negation** → check: a handoff line naming a follow-up whose `#N` does not resolve, or naming one with no reference at all.
- **Verification**: `audit.py check_followups_tracked`, scoped to handoffs inside the recent window (7 days, matching I5) **and to the project**: only handoffs declaring the audited repo (`- **Repo**: owner/name`) are read — a handoff declaring none is *cannot verify*, not a violation.
- **Evidence**: no violation in the window at introduction (2026-09-11). Run against the whole 48-handoff corpus it reports 37 — which is why it is windowed: the invariant binds from now on and does not retroactively judge history written before the convention. Its first version resolved references against **issues only** and raised a false violation on `#270`, which is a PR; it now resolves against issues ∪ PRs. **Second false-positive class, found 2026-09-17, twice in one hour, once per marker word**: the check read any line *containing* a marker, so a handoff sentence about work **delivered** ("solo un'osservazione advisory sull'indicatore della tab") and then a stanza about the invariant itself ("I9 smette di leggere un severity come follow-up") were both reported as untracked follow-ups. Fixed by defining *named* instead of *contained* (see the clause). Measured as a set operation over the 72-handoff corpus: 66 flagged lines before, 25 after, **0 introduced**, 41 removed, and **0 of the removed lines sit inside a follow-up section** — no pending item lost its check. Both halves carry violations in the current corpus (9 from a labelled line, 16 from inside a follow-up section), so neither is vacuous. **Third false-positive class, found 2026-09-19**: the corpus of handoffs is shared by every project while the check resolved `#N` against a single tracker, so any handoff of another project inside the window was a violation waiting to happen — `handoff-diario-mockup-fidelity-20260917.md:41` cited `#319` (a ticket of `Example/example-service`, whose PR #320 closes #316–#319) and was reported as untracked on `Example/a-data-pipeline`. It surfaced only when I1 became verifiable on a token without check-run access (2026-09-19, RATIONALE D-016): before that the audit aborted in `gather_prs` and I9 never ran against this repo at all. Fixed by scoping the check to the project. Measured: over the 7-day window 5 handoffs, 1 of another project, **1 violation before and 0 after**; over the 73-handoff corpus **22 flagged lines under the global scope and 0 under the per-repo scope**. **Trade-off, measured and accepted**: 1 of those 73 handoffs declares a repo (this session's handoff; the field is new), so the check has zero coverage over the pre-existing corpus and binds from now on — the same windowing argument that justified I5/I9 at introduction, and the reason the declaration is required by WORKFLOW.md.
- **Clause**: a line may say "none" / "nessuno" instead of citing a ticket. This restores the discipline I7 carried before its removal, as a checkable rule rather than a habit — a habit is what let three consecutive handoffs cite the same untracked follow-up. A follow-up is **named**, never merely *mentioned*: either a marker labels the line — optional bullet/number/`>`/bold, then `advisory` / `follow-up` / `non bloccante` — or any marker appears inside a section whose heading says the pending items are these (`## Next session`, `## Follow-up`, `## Advisory`, …; the unambiguous words count anywhere in the heading, `advisory` only as its first word). Prose in Delivered work, Verification or Deviations is not a follow-up. The handoff declares the repo it concerns (`- **Repo**: owner/name`): a follow-up's refs resolve on *that* tracker, and a handoff declaring none is skipped instead of judged against the wrong one.

## I10 — A handoff names the command behind a result it reports (weak)

- **Law**: a handoff's verification section quotes the command whose output it reports.
- **Negation** → check: a handoff with a verification section that quotes no command.
- **Verification**: `audit.py check_handoffs_quote_commands`.
- **Evidence**: no violation in the recent window at introduction (2026-09-11); the whole corpus shows 10, all predating the convention.
- **Clause**: **declared weak on purpose.** It proves that no command was offered, not that the quoted command produced the reported result. Four consecutive `example-service` handoffs asserted "ruff/mypy: 0 nuovi errori sui file toccati, debito pre-esistente intatto" — a claim of verification with nothing behind it, while the debt in fact grew and hid a real bug. The weak version catches exactly that failure, and pretending to more would make the check unfalsifiable.

## Not auditable (excluded from codification)

| Candidate | Reason |
|---|---|
| "The main agent orchestrates and never edits code directly" | No corpus data records it — not verifiable with current traces. Would require a verification line in the handoff. |
| "A session isolates in its own working tree when the repo is shared" | The trigger is not observable in tracker artifacts: nothing records whether a session *should* have isolated, so a check could only assert that a handoff mentions a worktree — vacuously satisfiable, and satisfiable by prose. Carried by the handoff's repo-state line (I5, which names the tree) and by WORKFLOW.md's session-isolation rule (RATIONALE **D-017**), which is where the enforcement argument lives. |
| "Stale worktrees are cleaned up at session end" | Same unfalsifiability: nothing in the tracker records which worktrees existed during a session, so a check could only assert that a handoff mentions the word. Carried by WORKFLOW.md's session-end gate step (4) and RATIONALE **D-018**, whose removal test is evidence-based (no claim + clean + reachable) precisely because it cannot be audited after the fact. |

## Anti-vacuity

Every check in `audit.py` must be able to fail: `tests/test_audit.py` runs
each check against deliberately violating inputs (mutation tests) and
asserts the violation is detected. A check that never fails on violating
input must be fixed or removed — a vacuously green check is worse than no
check.

## Evolution (TARGET)

- **Invariant elicitation in grill-with-docs** — today the `## System
  Invariants` section is born in system-architecture; grilling can elicit it
  informally first ("what must always be true in this system?").
- **Skip criteria judge domain value, not diff shape (2026-08-06)** — the
  system-architecture skip list (`mechanical refactor`, `follows existing
  pattern`) silently dropped the invariant-elicitation output of the phase:
  refactors of modules with real domain laws (status machine, quota, outbox,
  graph cascade) shipped without a `## System Invariants` section. Criterion
  changed (WORKFLOW.md phase 3 + system-architecture SKILL.md): those two
  skip types still skip the diagrams/contracts but the invariant section
  survives the skip; when there are no domain laws, the skip annotation must
  say "no domain laws" explicitly.
- **Periodic corpus audit** (monthly): invariants are corrected on real
  violations, like bugs in the campaign.
