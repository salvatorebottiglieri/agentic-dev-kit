# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

## [0.3.0] — 2026-11-09

Promuove la `v0.3.0-rc` dopo l'uso in produzione su più repo di prodotto:
design gate, JSONL reports, fixture provenance, role isolation, TDD red-first,
mutation prover agent+script, claim labels e risk/blast-radius.
Nessuna rimozione backward-incompatible.

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

## [0.3.0-rc] — 2026-10-15

**Release candidate** — allineamento con il workflow canonico v0.3.0 (32
regole, 1 struttura, 19 script, 12 prompt; 17 check in `audit.py`).
Backward-compatible: nessuna rimozione, solo allineamento. Tutto ciò
che funzionava con v0.2.0 continua a funzionare.

Questa RC non è la release definitiva. Manca smoke test con Claude
Code e validazione end-to-end; l'utente valuta in produzione prima
di promuovere a v0.3.0. **Non consumare senza test**.

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
- `WORKFLOW.md` è un file reale generato dal workflow canonico; la copia
  pubblica riscrive i percorsi e omette i riferimenti privati. Non è un
  symlink, così un clone del repo pubblico resta autosufficiente.
- `skills/implement-loop/SKILL.md` — paragrafo *"Report format — JSONL,
  not prose"* (D-025, B-004): i reviewer scrivono `reports/<feature>-<axis>.jsonl`
  con schema canonico (`criterion`, `item_id`, `verdict`, `severity`,
  `file`, `line`, `quote`, `commit`, `reviewer`, `claim`, `premise`).
  Il markdown è **rendered** dal JSONL, non scritto prima. I 3 check
  meccanici di B-004 (`check_brief_coverage`, `check_reviewer_evidence_verbatim`,
  `check_i6_zero_actionable`) verificano il formato.
- `skills/implement-loop/SKILL.md` — I6 esplicito nel fix loop: un
  finding `severity=fatal + verdict=actionable` deve avere
  `status=resolved` prima dell'exit; `status=open` blocca il loop.
  Check meccanico (`audit.py check_i6_zero_actionable`), non judgment.
- `skills/program-design/SKILL.md` — sezione *"Premises"* (B-025 voce 2,
  D-033): per ogni design non banale, l'agente elenca le premesse e le
  falsifica con `{claim, verdict, evidence}`. `untested` è violazione
  in `audit.py check_premises_falsified`. Refactor che tocca solo call
  graphs esistenti può skippare.
- `skills/system-architecture/SKILL.md` — regola *"`**Verification**:`
  line is mandatory"* (B-025 voce 4, D-035): ogni invariante cita uno
  script o funzione back-tickata che lo pinni. Mai "judgment" da solo.
  `audit.py check_invariant_verification_exists` rifiuta il design se
  la riga manca, cita una funzione inesistente, o dice solo "judgment".
  È il caposaldo D-024 applicato agli invarianti di sistema.

### Not changed (per trasparenza)
- `product-review` non è patchata: le regole modificate (JSONL, premesse,
  verification) non riguardano mockup/ticket UI. Le sue istruzioni
  interne sono auto-contenute e invariate.
- Le skill Matt Pocock (`codebase-design`, `code-review`,
  `ponytail-review`, `tdd`, `to-spec`, `to-tickets`, `grill-with-docs`,
  `domain-modeling`, `ask-matt`) non sono patchate: vivono in un repo
  upstream separato. Leggono WORKFLOW.md (aggiornato dalla copia sincronizzata) e
  quindi traggono automaticamente beneficio dalle 32 regole senza
  richiedere patch locali.

### Rollback
Le 3 patch additive non rompono il comportamento esistente. Se vuoi
tornare a v0.2.0 in locale: `cp -a ~/.agents/skills.bak.20261015/
{implement-loop,product-review,program-design,system-architecture}
~/.agents/skills/` (backup creato durante l'install). Il branch
`v0.3.0-rc` resta intatto.

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

[0.2.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/releases/tag/v0.1.0
