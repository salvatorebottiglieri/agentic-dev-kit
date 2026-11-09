# Feature branch

### 1. Place the feature

Read the existing module map (from the previous specs, or reconstruct it from
the layout and the fitness-function config). For each user story, name the
module whose bounded context owns its vocabulary and its data.

_Done when: every user story has an owning module, and any story that needs
a new context is flagged — that is a new module, cut with
[GREENFIELD.md](GREENFIELD.md) steps 1–3 for that context alone._

### 2. Diff the published interfaces

List what each touched module must add or change in its published interface
(calls, DTO fields, events), and any new edge in the graph. A new edge that
closes a cycle is resolved here, with the moves of GREENFIELD.md step 3.
A change to an existing published interface entry names every module that
calls it.

_Done when: the module-map diff (`+ / ~ / -`) lists every interface entry,
edge and piece of stored data the feature touches, and the graph stays
acyclic._

Then go back to SKILL.md step 2 — usually to extend the fitness function for
a new module or edge.
