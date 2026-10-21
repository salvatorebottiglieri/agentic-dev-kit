# Modularize branch

An existing codebase without enforced boundaries. The move is a **ratchet**:
freeze today's violations, forbid new ones, and remove the frozen ones one
ticket at a time.

### 1. Map what is there

Draw the actual dependency graph between the top-level packages with the
stack's dependency-map tool ([ENFORCEMENT.md](ENFORCEMENT.md)). Record which
stored data each package reads and writes.

_Done when: the graph covers every top-level package, every cycle is listed,
and every piece of stored data has its readers and writers listed._

### 2. Draw the target map

Run [GREENFIELD.md](GREENFIELD.md) steps 1–3 against the domain. Where they
say "user stories", use the entry points of the existing code (routes, jobs,
CLI commands) and the cross-package calls found in step 1. Then lay the
target modules over the current packages: each package goes to one target
module, or is marked to be split.

_Done when: every current package has a target module or a split note, and
the target graph is acyclic._

### 3. Freeze the violations

Run SKILL.md step 2 for the **target** map, with every current boundary
violation in the fitness function's **exception list** (ENFORCEMENT.md names
it per stack). The debt is that list plus the cross-module data accesses of
step 1; both only shrink.

_Done when: SKILL.md step 2 is done (green on today's code, every contract
red on its control violation), and each exception and each cross-module
data access is traceable to a target module._

### 4. Plan the extraction

One ticket per module extraction, ordered by the fewest inbound violations
first. Each ticket moves code behind the module's published
interface, moves data access behind the owner (law 3), and **deletes its
entries** from the exception list — the ticket is done when its entries are gone and the check
is green.

_Done when: every exception and every cross-module data access from step 1
belongs to exactly one ticket._

SKILL.md step 2 is already satisfied by step 3: continue at SKILL.md step 3.
