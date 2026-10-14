# Phase 2. Product review (only when the feature touches UI)

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://product-review` — standalone HTML mockup at
`.scratch/product-review/<feature>/index.html` (problem statement + success
criteria + wireframe). Success criteria must cover every distinct persona in
the user stories (at least one criterion per persona) and the edge-case
states (empty, error, loading, long content, small viewport); the mockup
renders those states. Skip: API-only, UI bug fix, mechanical refactor,
follows existing pattern, pure data.

**Visibility rule:** the phase-2 decision is always stated in the session,
run or skip — "Phase 2 — product review: run" or "Phase 2 skipped —
<reason> (annotation on the ticket)". A skipped phase is never silent: the
user sees the decision in conversation even when the phase produces nothing.
