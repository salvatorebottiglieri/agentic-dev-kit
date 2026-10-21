# Greenfield branch

### 1. Find the bounded contexts

Read `CONTEXT.md` and the spec. Group the terms by the language that uses
them: where one word means two things ("account" in billing vs. identity),
there are two contexts. If `CONTEXT.md` is thin or the groupings stay
ambiguous, run `domain-modeling` first.

_Done when: every noun of the spec's user stories belongs to exactly one
candidate context, and every term with two meanings is split._

### 2. Cut the modules

One module per context. Where the line between two candidate contexts is
uncertain, keep them as one context named with the broader `CONTEXT.md`
term and record the doubt in the module map: splitting a wrongly merged
module later costs a data migration, so split on evidence. For each module
write its published interface as exactly the commands and queries the user
stories need from it.

_Done when: every user story is served by a call chain through published
interfaces, and each published interface entry is used by at least one
story._

### 3. Assign data ownership and edges

Give each table to one module. Draw the edges: a call where the caller needs
the answer, an event where others only react (law 5). Break every cycle —
usually by turning the back edge into an event, or by moving the concept
that both sides need into the module that creates it.

_Done when: every table has exactly one owner and the graph is acyclic._

### 4. Lay out the code

Python layout (other stacks: the same shape in their packaging unit). All
code that is not published lives under `internal/`:

```text
src/<app>/
  shared/            # technical kernel only (law 6)
  <module>/
    __init__.py      # empty — the published interface lives in api.py
    api.py           # published interface: functions, DTOs, event types
    internal/        # domain, persistence, adapters — private to the module
```

_Done when: every module has `api.py` and `internal/` and nothing else
besides `__init__.py`, and `shared/` holds no `CONTEXT.md` term._

Then go back to SKILL.md step 2.
