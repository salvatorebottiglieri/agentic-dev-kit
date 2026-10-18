# Phase 5. Program design (near-always)

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://program-design` — appends `## Program Design` to the ticket:
call-stack tree, file-tree diff, type signatures (contract for
implementation). Skip only when there is no *shape* to design: one-shot
throwaway scripts, pure config/data changes that introduce zero new
signatures, mechanical rename/move with no logic change. Bug fixes and
"trivial" features get program design whenever they introduce or modify
functions or control flow.

The design is a **file on the branch**, `docs/design/<feature>.md`: the prose
plus a fenced ` ```json design ` block that is the machine part — call stack,
file tree, signatures, data flow, and one contract per call with its exits.
Before the `## Program Design` section is appended to the ticket,
`python3 guards/check_design.py <design> .` must exit **0** in full mode
(signatures against the real code, coherence, all four exits declared and
handled, the external frontier's `residual`). At pre-push and in CI the
`--gate` subset runs the same checks minus the design's *state*, whose premise
("the code does not exist yet") expires once the implementation lands. The
declared `exits` are the implementer's test list; the Spec axis judges whether
each `handled_at` is the right place.
