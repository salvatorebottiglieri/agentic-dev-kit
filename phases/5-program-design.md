# Phase 5. Program design (near-always)

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://program-design` — appends `## Program Design` to the ticket:
call-stack tree, file-tree diff, type signatures (contract for
implementation). Skip only when there is no *shape* to design: one-shot
throwaway scripts, pure config/data changes that introduce zero new
signatures, mechanical rename/move with no logic change. Bug fixes and
"trivial" features get program design whenever they introduce or modify
functions or control flow.
