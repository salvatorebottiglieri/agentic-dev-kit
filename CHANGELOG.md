# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
