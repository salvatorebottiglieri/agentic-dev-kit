# Phase 3. System architecture (only when non-trivial)

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

`skill://system-architecture` — appends `## System Architecture` to the spec:
endpoint contracts, data model, **and `## System Invariants`** (laws of the
domain in the campaign format — law → negation → where verified;
anti-vacuity: an invariant without a test that can fail is an opinion). The
invariants are test contract downstream: implement-loop writes their tests
first (red), review verifies coverage. The diagrams (sequence, state) are
owned by `skill://uml-modeling`, not by this skill: the architecture says
*which* components and contracts exist, the model shows *how they interact*.
When the system is a modular monolith (or the spec creates or splits one),
`skill://modular-monolith` runs inside this phase: module map, module
invariants, and a fitness function in CI that goes red on a boundary
violation. A feature that adds a module, an edge between modules or stored data
is not skippable here, whatever its diff shape.
Skip: bug fix, trivial feature, data-only. Mechanical refactor and
follows-existing-pattern skip the contracts but NOT the invariant
elicitation: if the touched module has domain laws (documented or new), the
`## System Invariants` section must still be written (possibly reduced —
law, negation, where verified); if there are none, the skip annotation must
state "no domain laws" explicitly. The criteria judge domain value, not
diff shape. The diagram decision belongs to `uml-modeling`; this phase does
not produce them.

`skill://uml-modeling` — add `## Sequence` with the real components and
contracts, and reconcile the flow/statecharts with the architecture. The
module map is the source of **area boundaries**: if it is missing or unclear,
that is a finding to escalate, not a `core` area to invent.
