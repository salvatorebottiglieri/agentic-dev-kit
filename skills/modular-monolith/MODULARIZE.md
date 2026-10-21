# Modularize branch

An existing codebase without enforced boundaries. The move is a **ratchet**:
freeze today's violations, forbid new ones, and remove the frozen ones one
ticket at a time.

### 1. Map what is there

Draw the actual dependency graph between the top-level packages (in Python:
`import-linter`'s `lint-imports` with an `acyclic_siblings` contract on the
root reports the cycles; `grimp` or `pydeps` draw the graph). Record which
tables each package reads and writes.

_Done when: the graph covers every top-level package, every cycle is listed,
and every table has its readers and writers listed._

### 2. Draw the target map

Run [GREENFIELD.md](GREENFIELD.md) steps 1–3 against the domain. Where they
say "user stories", use the entry points of the existing code (routes, jobs,
CLI commands) and the cross-package calls found in step 1. Then lay the
target modules over the current packages: each package goes to one target
module, or is marked to be split.

_Done when: every current package has a target module or a split note, and
the target graph is acyclic._

### 3. Freeze the violations

Run SKILL.md step 2 for the **target** map, with every current import
violation listed in `ignore_imports` (or the stack's equivalent). The debt is
that list plus the cross-module table accesses of step 1; both only shrink.

_Done when: SKILL.md step 2 is done (green on today's code, every contract
red on its control violation), and each ignored import and each
cross-module table access is traceable to a target module._

### 4. Plan the extraction

One ticket per module extraction, ordered by the fewest inbound violations
first. Each ticket moves code behind the module's `api.py`, moves table
access behind the owner (law 3), and **deletes its entries** from
`ignore_imports` — the ticket is done when its entries are gone and the check
is green.

_Done when: every frozen import violation and every cross-module table
access from step 1 belongs to exactly one ticket._

SKILL.md step 2 is already satisfied by step 3: continue at SKILL.md step 3.
