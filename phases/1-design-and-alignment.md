# Phase 1. Design & alignment

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

- `skill://grill-with-docs` — structured interview; updates CONTEXT.md and ADRs
- `skill://domain-modeling` — glossary when new terms emerge
- `skill://to-spec` — synthesize the conversation into a spec on the tracker
- **Falsify the premises before publishing.** A spec inherits the claims the
  conversation made about the current codebase — "the audit log is read-only",
  "this surface does not exist yet", "nothing enforces that rule". Write them
  down as falsifiable statements and dispatch a scout to refute them *before*
  the spec is published. A premise that dies changes the design tree, and it
  is far cheaper to lose it in phase 1 than to build on it in phase 6.
