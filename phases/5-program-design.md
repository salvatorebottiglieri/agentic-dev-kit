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

A choice that changes agent behaviour, run cost, or an external contract (CLI
argv, config keys, wire format, file layout) is a **load-bearing choice**. It
must appear in the design — `docs/design/<feature>.md` and the appended
`## Program Design` — as a `## Decision` section with the choice, the rejected
alternatives, the consequences, and a link to the owner's sign-off comment. A
design that makes such a choice without it is **incomplete**; the Spec axis
raises it as `fatal`, quoting the design text as evidence. One section per
choice; the heading starts with `## Decision`.

A test fixture that stands in for a system the repo does not own (an API
response, a file produced by another tool, a schema of another service) is an
**external fixture**: it declares its provenance in the design's `## External
fixtures` section and in the fixtures manifest named there (default
`tests/fixtures/PROVENANCE.md`). Each row has `source` — `captured` (a real
sample, sanitized), `contract` (a named contract document), or `synthetic` (no
real sample available, with the reason) — plus `reference` and
`sanitized`/`why`. `guards/check_fixtures.py` validates the manifest's shape
and that every listed file exists; the Spec axis judges whether the fixture is
external and whether the source is the right one. A fixture simpler than
production hides exactly the bug class the work exists to fix.
