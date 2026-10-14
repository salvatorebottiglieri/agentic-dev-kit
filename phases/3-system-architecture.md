# Phase 3. System architecture (only when non-trivial)

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://system-architecture` — appends `## System Architecture` to the spec:
Mermaid sequence/state diagrams, endpoint contracts, data model, **and
`## System Invariants`** (laws of the domain in the campaign format —
law → negation → where verified; anti-vacuity: an invariant without a test
that can fail is an opinion). The invariants are test contract downstream:
implement-loop writes their tests first (red), review verifies coverage.
Skip: bug fix, trivial feature, data-only. Mechanical refactor and
follows-existing-pattern skip the diagrams/contracts but NOT the invariant
elicitation: if the touched module has domain laws (documented or new), the
`## System Invariants` section must still be written (possibly reduced —
law, negation, where verified); if there are none, the skip annotation must
state "no domain laws" explicitly. The criteria judge domain value, not
diff shape.
