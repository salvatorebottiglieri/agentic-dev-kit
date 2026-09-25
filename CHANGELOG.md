# Changelog

All notable changes to this project are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/salvatorebottiglieri/agentic-dev-kit/releases/tag/v0.1.0
