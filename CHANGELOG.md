# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] — 2026-11-26

Adds the per-feature UML model (flow, sequence and state machine, checked
against the code) and ships the phase harness and the desktop window that were
private tools until now.

### Added
- `wf.py` — the phase harness: `wf.py next <feature>` prints the files of the
  phase the feature is in and checks the previous one before moving on; `end`
  closes a session, `report` stores and shows review reports, `post-spec` posts
  the spec's missing sections to its tickets (the only command that writes to
  the tracker). A phase skill that is not installed prints as `MISSING SKILL`.
- `report_store.py` — the report store (SQLite) that `wf.py report` and the
  window read.
- `app/` — the desktop window, started with `python3 app/desk.py serve` and any
  browser: Open and History tabs, the workflow panel (the seven phases as a
  stepper with the live phase marked), Mermaid diagrams drawn from the spec,
  the model and the design, the system invariants with a live status
  (`pinned`, `blind`, `never mutated`, `test missing`, `not checkable`), light
  and dark themes, short motion that `prefers-reduced-motion` turns off, and a
  phone view. Mermaid and marked are vendored, so it works offline; `NOTICE`
  carries their third-party notices.
- `gen-flow.py` and `FLOW.md` — the map of the method generated from
  `WORKFLOW.md`, with a check that every rule is covered.
- `phases/0-start.md` — phase 0, printed by `wf.py next` (with `LESSONS.md`
  when you have one; the kit ships none).
- `skills/uml-modeling/` — three checkpoints (after the spec, after the
  architecture, before the PR), a `ModelFacts` skeleton, a `## Model changes`
  table with approval, statechart triggers, skip rules.
- `guards/check_model.py` — validates the shape of the model, requires an
  approval URL for every semantic change and, with `--gate`, resolves
  `## Elements` against the facts of the code; pre-push hook when
  `docs/models/` exists.
- `guards/model_facts_python.py` — Python `ast` adapter for `model-facts/1`
  (modules, symbols, calls, routes, enum states); `--facts` is the escape
  hatch for other languages.
- `audit.py check_model_declared` — every PR merged after 2026-11-10 declares
  `Model: <path>` or `Model: n/a — <reason>`.
- Spec axis (h) in `REVIEW-CRITERIA.md`.

### Changed
- State directory: the harness, the store and the window keep their data in
  `~/.local/state/agentic-dev-kit` (or `$XDG_STATE_HOME/agentic-dev-kit`).
- The export's leak scan is stricter: it also refuses personal names and hosts
  and any other repository of the maintainer, and it rewrites the listed client
  names instead of letting them through.
- `system-architecture` no longer produces sequence/state diagrams: it owns
  contracts, data model and invariants and points to `uml-modeling`.
- Phase 1 appends `## Model` (flow + statechart); phase 3 adds the sequence
  and reconciles; phase 6 produces the as-built `docs/models/<feature>.md`.
- `WORKFLOW.md` lists `uml-modeling` in phases 1, 3, 6 and the model
  enforcement rows.
- `install.sh` links the sixth custom skill.

### What's new for users
- `python3 ~/agentic-dev-kit/wf.py next <feature>` tells the agent, and you,
  what to read and what must be true before each phase.
- Anyone who does not read the code gets a domain-level model with a checkable
  skeleton, and every semantic change carries the owner's approval.

### Upgrading from v0.5.0
- Reinstall with `./install.sh`: the installer links `uml-modeling`.
- Repositories with `docs/models/` get the pre-push gate; those without are
  untouched. The harness and the window run from the clone; the kit's skills must
  be in the skills store `wf.py` reads (`AGENT_SKILLS_DIR=~/.agents/skills ./install.sh`).
- Breaking changes: none.

### Verification
- The export's `--check` reports no drift and the tests pass in a fresh copy
  of the generated tree.

## [0.5.0] — 2026-11-09

Adds the `modular-monolith` skill and separates the README from the gate
reference. No backward-incompatible removal.

### Added
- `skills/modular-monolith/` — designs or evolves a modular monolith inside
  phase 3: module map (bounded context, published interface, owned stored
  data, edges, events), module rows of the system invariants, and a fitness
  function in CI. Three branches: greenfield, feature, modularize (ratchet on
  the fitness function's exception list). Stack-neutral except
  `ENFORCEMENT.md`, which holds the per-stack layout and tools: the Python
  recipe with `import-linter>=2.6`, other stacks with their tools.
- `docs/GATES.md` — the detailed pre-commit/pre-push gate reference moved out
  of the README; the landing page links to it.

### Changed
- `system-architecture` reads `modular-monolith` at step 2 and carries a
  `### Module map` in its template; phase 3 states that module-map changes
  are not skippable.
- `WORKFLOW.md` phase 3 lists `modular-monolith`; `install.sh` links it.

### What's new for users
- A fifth custom skill, `modular-monolith`, read by `system-architecture` when
  the system is (or is becoming) a modular monolith: module map, module
  invariants, a fitness function in CI, and three branches (greenfield,
  feature, modularize).
- The README is now a 103-line overview; gate configuration, `final` mode,
  bypasses and known limits moved to `docs/GATES.md`.

### Upgrading from v0.4.0
- Reinstall with `./install.sh` (or check out the `v0.5.0` tag); the installer
  links the new `modular-monolith` skill. Existing skills are unchanged.
- Breaking changes: none.

### Verification
- 251 tests in the canonical repo; 234 tests in the generated kit; the export
  `--check` reports no drift.

## [0.4.0] — 2026-11-09

### Changed
- The public kit is now generated from the canonical workflow source by
  `sync_kit.py`; this repository is a read-only mirror, not a second source.
- Private project names in tests and comments were neutralized; the public
  `RATIONALE.md` is a generated decision register (Decision + Rationale, no
  private session evidence).
- `WORKFLOW.md` installation guidance updated for the generated export.

### Unchanged
- The v0.3.0 rules and skills. v0.4.0 is a packaging/release-hygiene release.

### What's new for users
- Nothing behavioural beyond v0.3.0: this release makes the public kit a
  generated, sanitized artifact. The feature list is the v0.3.0 section below.

### Upgrading from v0.3.0
- Reinstall with `./install.sh` (or check out the `v0.4.0` tag) and keep your
  existing `AGENT_SKILLS_DIR`; the layout (`skills/`, `guards/`, `install.sh`)
  is unchanged.
- `guards/sync_workflow.py` and `tests/test_sync_workflow.py` were removed;
  the public repository no longer syncs itself.
- Breaking changes: none.

### Verification
- 234 tests pass in the generated tree; the export's `--check` reports no
  drift.

## [0.3.0] — 2026-11-09

Promotes `v0.3.0-rc` after production use across several product repos:
design gate, JSONL reports, fixture provenance, role isolation, TDD red-first,
mutation prover agent+script, claim labels and risk/blast-radius.
No backward-incompatible removal.

### Added
- `skills/program-design/SKILL.md` — `## Decision` for load-bearing Program
  Design choices (D-043): behaviour, run-cost or external-contract choices
  carry the choice, rejected alternatives, consequences and the owner's
  sign-off; the Spec axis raises a silent one as `fatal`.
- External-fixture provenance (B-022/D-044): a manifest
  (`tests/fixtures/PROVENANCE.md` by default) declares `captured` / `contract` /
  `synthetic` for every fixture standing in for an outside system, and
  `guards/check_fixtures.py` validates it; the pre-push hook runs it when the
  manifest exists. The producer and reviewer sides are in
  `skills/program-design/SKILL.md` and the Spec axis.
- JSONL report wiring (B-004): `audit.py` reads `reports/*.jsonl` and the
  archived `handoffs/*-reports/`, filters by `repo`, requires `actor`, loads
  `reports/<feature>-brief.json`, fetches the patch of each cited commit, and
  **calls** `check_reviewer_evidence_verbatim`, `check_brief_coverage` and
  `check_i6_zero_actionable` (before this they were defined but inert). The
  schema in `REVIEW-CRITERIA.md` / `implement-loop` gains `repo`, `actor` and
  `status`.
- Role isolation prompt reinforcement (B-007/D-031): only the mutation prover
  applies mutants, in its scratch worktree; implementer/fixer briefs contain
  no mutation verb or recipe; a mutant in the session worktree is a process
  violation. The coordinator inspects `git status` after every fixer round.
- TDD red-first rule (B-013/D-045): the red must fail for the expected reason,
  and the implementer's report cites the red test (`path::name`) and quotes the
  failure, then the green run.
- Mutation-prover split (B-026/D-046): the agent writes a manifest of
  plausible-wrong patches; `guards/mutation_prover.py` applies them in clean
  worktrees with `PYTHONPATH` on the worktree, runs the control first (green =
  `inconclusive`), runs the targeted test, emits a JSONL matrix
  `pinned`/`blind-test`/`no-test`/`error`, and removes the worktrees. `mutmut`
  remains optional and consultative.
- Claim labels (B-011/D-047): final reports and handoffs label every claim
  `measured` / `deduced` / `hypothesis` with its evidence in the same sentence.
- Risk assessment (B-001/D-048): the PR body carries `Risk: low|med|high`,
  `Blast radius:` and `Where to look:`, computed deterministically from the
  diff paths; it is triage metadata, not a review criterion.
- Conditional blast-radius (B-014/D-048): for `Risk: med|high` the coordinator
  searches outside the diff (wire format, pins, deploy scripts, cross-repo
  contracts) and executes the fact the safety depends on, or declares a
  residual.
- `WORKFLOW.md` sync: 40 rules (1 structure, 22 script, 17 prompt).

## 0.3.0-rc — 2026-10-15

**Release candidate** — alignment with the canonical v0.3.0 workflow (32
rules, 1 structure, 19 scripts, 12 prompts; 17 checks in `audit.py`).
Backward-compatible: no removal, only alignment. Everything that worked with
v0.2.0 keeps working.

This RC is not the final release. It lacks a smoke test with Claude Code and
end-to-end validation; the user evaluates it in production before promoting it
to v0.3.0. **Do not consume without testing.**

### Added
- `guards/check_design.py` + `guards/hooks/pre-push` — the DesignDoc gate: a
  phase-5 design file (`docs/design/<feature>.md`, prose plus a `json design`
  block) is checked for coherence, signatures against the real code, the four
  exit kinds with their `handled_at`, and the external frontier's `residual`.
  The pre-push hook runs the `--gate` subset (no state); the full mode runs at
  the end of phase 5. Touches `phases/5-program-design.md`, `REVIEW-CRITERIA.md`
  (Spec axis), `skills/program-design/SKILL.md` and `RATIONALE.md` D-023.
  The canonical implementation is in the workflow source; the kit copy
  follows it and must not drift.
- `audit.py` + `tests/test_audit.py` — parity with the canonical `audit.py`
  and its test suite. The canonical is the source: the kit copies it, with
  only the module-docstring example path adapted.
- `WORKFLOW.md` is a real file generated from the canonical workflow; the
  public copy rewrites the paths and omits the private references. It is not a
  symlink, so a clone of the public repo stays self-sufficient.
- `skills/implement-loop/SKILL.md` — paragraph *"Report format — JSONL,
  not prose"* (D-025, B-004): reviewers write `reports/<feature>-<axis>.jsonl`
  with the canonical schema (`criterion`, `item_id`, `verdict`, `severity`,
  `file`, `line`, `quote`, `commit`, `reviewer`, `claim`, `premise`).
  The markdown is **rendered** from the JSONL, not written first. The 3
  mechanical checks of B-004 (`check_brief_coverage`,
  `check_reviewer_evidence_verbatim`, `check_i6_zero_actionable`) verify the
  format.
- `skills/implement-loop/SKILL.md` — explicit I6 in the fix loop: a finding
  with `severity=fatal + verdict=actionable` must have `status=resolved`
  before the exit; `status=open` blocks the loop. Mechanical check
  (`audit.py check_i6_zero_actionable`), not judgment.
- `skills/program-design/SKILL.md` — *"Premises"* section (B-025 item 2,
  D-033): for every non-trivial design, the agent lists the premises and
  falsifies them with `{claim, verdict, evidence}`. `untested` is a violation
  in `audit.py check_premises_falsified`. A refactor that only touches existing
  call graphs may skip.
- `skills/system-architecture/SKILL.md` — rule *"`**Verification**:` line is
  mandatory"* (B-025 item 4, D-035): every invariant cites a back-ticked
  script or function that pins it. Never "judgment" alone.
  `audit.py check_invariant_verification_exists` rejects the design if the line
  is missing, cites a function that does not exist, or says only "judgment".
  This is the D-024 cornerstone applied to the system invariants.

### Not changed (for transparency)
- `product-review` is not patched: the changed rules (JSONL, premises,
  verification) do not concern mockups/UI tickets. Its internal instructions
  are self-contained and unchanged.
- Matt Pocock's skills (`codebase-design`, `code-review`, `ponytail-review`,
  `tdd`, `to-spec`, `to-tickets`, `grill-with-docs`, `domain-modeling`,
  `ask-matt`) are not patched: they live in a separate upstream repo. They
  read WORKFLOW.md (updated by the synced copy) and so benefit automatically
  from the 32 rules without any local patch.

### Rollback
The 3 additive patches do not break existing behaviour. To go back to v0.2.0
locally: `cp -a ~/.agents/skills.bak.20261015/
{implement-loop,product-review,program-design,system-architecture}
~/.agents/skills/` (backup created during the install). The `v0.3.0-rc`
branch stays intact.

### Related
- The canonical workflow source is private; this kit is generated from it.
  The release notes keep the public decisions and omit private session
  evidence.

## [0.2.0] — 2026-10-14

Pre-commit gate: workflow rules that asked the agent to *report* a fact
(skipped tests, a release's stale pins) now have that fact computed by a
tool instead, measured against the textual rule in an A/B replay (B 5/5 vs
A 0/5 on skipped tests, B 5/5 vs A 2/5 on stale pins; 0 bypasses, 0 false
alarms; results in the private workflow repo).

### Added
- `guards/gate.py` + `guards/hooks/pre-commit` — a git pre-commit hook that
  computes skipped/deselected tests (from the canonical test command's
  machine-readable report) and stale pins of a released artifact (scanned
  in configured deploy files), and stages both into a tracked
  `.workflow/gate-report.md`. Agent-neutral: it runs as a standard git hook,
  not an agent-specific adapter.
- `gate.py final` mode — re-runs the checks outside git (end of session, CI)
  and compares against the committed report, catching a disabled hook, an
  edited deploy file after the last commit, or a session that never
  committed.
- `install.sh --gate <repo>` — sets the target repository's `core.hooksPath`
  to the kit's hook directory.
- `RATIONALE.md` D-022 — why the gate computes facts instead of asking the
  agent to state them, and why it blocks only when it cannot run.
- `WORKFLOW.md` — enforcement table row for the gate.

## [0.1.0] — 2026-09-25

Initial public release. The repository was previously a skills-only collection
(`sb-skills`); it now ships the full workflow, the audit gate, and the custom
skills together, de-personalized for public use.

### Added
- `WORKFLOW.md` — the operational spec: the phase-by-phase pipeline (design →
  product review → system architecture → tickets → program design →
  implement-loop) and the review criteria family that gates a merge.
- `skills/` — the four custom skills: `implement-loop`, `product-review`,
  `system-architecture`, `program-design`.
- `audit.py` + `tests/` — the session-end invariant gate (GitHub-only).
- `INVARIANTS.md`, `INVARIANTS-METHOD.md`, `RATIONALE.md` — the workflow
  invariants and the reasoning behind every load-bearing rule.
- `install.sh` — idempotent skill install; target dir via `AGENT_SKILLS_DIR`
  (defaults to `~/.claude/skills`).
- MIT `LICENSE`.

### Notes
- The kit layers on [Matt Pocock's skills](https://github.com/mattpocock/skills)
  (MIT) — install that base layer first.
- `product-review`, `system-architecture`, and `program-design` are first
  versions (WIP), not yet battle-tested.

[0.6.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/releases/tag/v0.1.0
