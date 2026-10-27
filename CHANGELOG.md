# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0-rc] — 2026-10-15

**Release candidate** — allineamento con `agentic-workflow` v0.3.0 (32
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
  The canonical implementation is `~/agentic-workflow/guards/check_design.py`;
  the kit copy follows it and must not drift.
- `audit.py` + `tests/test_audit.py` — parity with the canonical
  `~/agentic-workflow/audit.py` (869 lines, 17 checks) and its test suite
  (842 lines). The canonical is the source: the kit copies it, with only the
  module-docstring example path adapted.
- `WORKFLOW.md` è un file reale derivato dal canonico di `agentic-workflow` da
  `guards/sync_workflow.py` (`--check` rileva il drift). Non è un symlink: i
  cloni del repo pubblico non possono risolvere `../agentic-workflow`, che è
  privato. La copia differisce solo per i percorsi `~/agentic-workflow/` →
  `~/agentic-dev-kit/` e per le righe che citano file solo privati
  (`LESSONS.md`, `BACKLOG.md`, `gen-flow.py`).
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
- `agentic-workflow` master al commit `b35897c` (pushato) contiene
  le 32 regole, D-041 (pre-commit hook per RATIONALE.md = prima regola
  a livello struttura), e 4 task synth R-003 (H5/H6/H7/H8 PASS).
- Handoff: `agentic-workflow/handoffs/handoff-workflow-split-v030rc-20261015.md`.

## [0.2.0] — 2026-10-14

Pre-commit gate: workflow rules that asked the agent to *report* a fact
(skipped tests, a release's stale pins) now have that fact computed by a
tool instead, measured against the textual rule in an A/B replay (B 5/5 vs
A 0/5 on skipped tests, B 5/5 vs A 2/5 on stale pins; 0 bypasses, 0 false
alarms; `experiments/2026-10-pstack-bjarnason/RESULTS-ROUND2.md` in the
private workflow repo).

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
